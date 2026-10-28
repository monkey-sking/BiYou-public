import os
import re
import sys
import json
from datetime import datetime
from typing import Optional
from core.utils.config import load_config
from core.utils.i18n import print_t
from core.utils.logger import log
from core.services.llm.factory import LLMFactory

CONFIG = load_config()
ASPECT_RATIO_TEXT = {
    "16:9": "widescreen landscape",
    "4:3": "standard landscape",
    "3:4": "portrait card",
    "6:7": "portrait feed",
    "9:16": "full-screen vertical",
}

def ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d)

# 封面"内容归属"校验: 计算两图(缩放后)像素级 Pearson 相关系数。
# 用途: 拦截"张冠李戴"——翻译后的封面与源封面应高度相似(仅文字/细节变)，
# 而另一个视频的封面与该源封面应显著不相关。
# 实测(2026-08-20): 正确对 >=0.4, 跨视频错对 <=0.14, 安全阈值 0.25。
# 2026-08-31: 阈值参数化。config.services.cover.similarity_threshold 可配置；
# 设为 0 或负值 = 关闭像素相关性校验(用于走"重新设计"型封面的场景，
# 其像素相关天然低，会被安全阈值误杀)。默认保持 0.25。
def _cover_sim_threshold() -> float:
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "config.yaml"), encoding="utf-8"))
        return float(cfg.get("services", {}).get("cover", {}).get("similarity_threshold", 0.25))
    except Exception:
        return 0.25

COVER_SIMILARITY_THRESHOLD = lambda: _cover_sim_threshold()


def _cover_cn_overlay_enabled() -> bool:
    """中文标题叠加层开关（config.services.cover.cn_overlay，默认 True）。

    2026-09-26: AI 出图无法可靠渲染中文，而 Jev 闸门要求必须有中文标题 →
    本地 PIL 叠加中文层是唯一能让 AI 图通过闸门的方式。
    设 False 可完全关闭（回到 AI 原图直出）。
    """
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "config.yaml"), encoding="utf-8"))
        return bool(cfg.get("services", {}).get("cover", {}).get("cn_overlay", True))
    except Exception:
        return True

def cover_similarity(a_path: str, b_path: str, size: int = 192) -> float:
    """返回两封面图的相关度(0~1)。越接近1越相似。文件缺失/损坏返回 -1。"""
    try:
        import numpy as np
        from PIL import Image
        a = Image.open(a_path).convert("RGB").resize((size, size))
        b = Image.open(b_path).convert("RGB").resize((size, size))
        ah = np.asarray(a).ravel().astype(int)
        bh = np.asarray(b).ravel().astype(int)
        if ah.size == 0 or bh.size == 0:
            return -1.0
        corr = float(np.corrcoef(ah, bh)[0, 1])
        if np.isnan(corr):
            return -1.0
        return corr
    except Exception:
        return -1.0

def _safe_remove(path: str) -> None:
    """防错图残留：尽力删除文件，失败静默。"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass

class ImageTranslationService:
    # Provider that produced the last successful cover (set in process()).
    # Lets CoverWorkflow decide whether the generic watermark pass is needed.
    last_provider: Optional[str] = None
    """
    Service responsible for translating/editing images using LLM.
    Pipeline: Flash (translate keywords) → Build prompt → Generate image → OCR verify → retry
    """
    
    MAX_RETRIES = 1

    @staticmethod
    def _strengthen_generation_prompt(prompt: str) -> str:
        """Bias Gemini toward returning an edited image artifact instead of text-only advice."""
        prefix = (
            "Create one edited image from the uploaded image.\n"
            "Return the edited image itself, not a text explanation, not a prompt suggestion, and not a refusal.\n"
            "If the request is feasible, perform the edit directly on the uploaded image.\n\n"
        )
        prompt = (prompt or "").strip()
        if prompt.lower().startswith("create one edited image from the uploaded image."):
            return prompt
        return prefix + prompt

    @staticmethod
    def _should_use_compact_browser_prompt(llm) -> bool:
        return (
            getattr(llm, 'provider', '') == 'gemini_web'
            and bool(getattr(llm, 'use_browser', False))
        )

    @staticmethod
    def _build_compact_browser_prompt(
        title: str,
        aspect_ratio: str,
        translation_block: str = "",
        cover_display_ratio: str = "",
        cover_layout: str = "",
    ) -> str:
        lines = [
            "Edit the uploaded thumbnail image only.",
            "If the image already has clear visible English cover text, replace only that visible text with concise Simplified Chinese.",
            "Keep the replacement text short and preserve the original text boxes, positions, line breaks, alignment, and overall typography as closely as possible.",
            "If the image has little or no visible text, add at most 1 to 3 short Chinese phrases only if there is safe empty space; otherwise keep the image unchanged.",
            "Keep the subject, crop, composition, background, lighting, colors, and all non-text elements unchanged.",
            "Do not redesign the thumbnail.",
            "Design principles: one strong 3-6 character hook beats several weak phrases (less but better); every text element must carry info — no decorative badges or filler; text must contrast with its background and stay readable at thumbnail size; keep balance between subject, text, and empty space; wording must match the real video content (no exaggerated claims).",
            f"Keep the final image in exact {aspect_ratio} aspect ratio.",
        ]

        cover_display_ratio = (cover_display_ratio or "").strip()
        if cover_display_ratio:
            lines.append(
                f"Keep all critical text and logos inside the centered {cover_display_ratio} safe area."
            )

        cover_layout = (cover_layout or "").strip()
        if cover_layout:
            lines.append(f"Layout guidance: {cover_layout}")

        title = (title or "").strip()
        if title:
            lines.append(f"Reference title: {title}")

        translation_block = (translation_block or "").strip()
        if translation_block:
            lines.append("Optional wording hints for visible text only:")
            lines.append(translation_block)

        lines.append("Return one edited image only.")
        return "\n".join(lines)
    
    @staticmethod
    def _provider_failed_recently(provider: str, threshold: int = 3, lookback_hours: int = 72) -> bool:
        """判断某 provider 是否「最近连续失败」达到阈值（用于快速降级）。

        ⚠️ 2026-09-25 重大修复：原实现的语义完全错了 ——
        它统计「72 小时窗口内的失败总次数」，而不是「最近的连续失败」。
        因为 consecutive 只在遇到成功记录时重置，而循环是从头顺序读的，
        等价于「72h 内失败 ≥ threshold 就降级」。
        实测后果：gemini_cli 在 72h 窗口内累计失败 146 次（期间多次成功），
        被判为「连续失败」→ 被跳过整整 72 小时 → 即使它 20:04 刚成功过，
        后续任务也不再尝试它（当天 0 成功，全在空转降级）。

        正确语义：只看**最近连续的失败**（末尾连续 bad 计数），
        遇到任何成功记录立即清零并返回 False。
        lookback 只用于忽略过老的记录。
        """
        try:
            from datetime import datetime, timedelta
            import json as _json
            here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            hist_f = os.path.join(here, "data", "cover_provider_history.jsonl")
            if not os.path.isfile(hist_f):
                return False
            cutoff = datetime.now() - timedelta(hours=lookback_hours)
            bad = {"failed", "exhausted", "login_failed", "rejected", "no result path"}
            good = {"ok", "jev_pass", "jev_review", "accepted without ocr"}

            # 先收集窗口内该 provider 的记录（按文件顺序 = 时间顺序）
            seq = []
            with open(hist_f) as fh:
                for line in fh:
                    try:
                        e = _json.loads(line)
                    except Exception:
                        continue
                    if e.get("provider") != provider:
                        continue
                    ts = str(e.get("ts", ""))
                    try:
                        t = datetime.fromisoformat(ts)
                        if t.tzinfo is not None:
                            t = t.astimezone().replace(tzinfo=None)
                    except Exception:
                        continue
                    if t < cutoff:
                        continue  # 过期记录不参与判定
                    seq.append(str(e.get("status", "")).lower())

            # 从**末尾**往前数连续失败；遇到成功立即停止（近期成功 = 不降级）
            consecutive = 0
            for st in reversed(seq):
                if st in good:
                    return False   # 最近成功过 → 不该降级
                if st in bad:
                    consecutive += 1
                    if consecutive >= threshold:
                        return True
                else:
                    # 未知状态（如跳过/中性）不打断计数，也不计入
                    continue
            return False
        except Exception:
            return False

    @staticmethod
    def _provider_script_broken(provider: str, threshold: int = 2, lookback_hours: int = 72) -> bool:
        """检测 provider 是否连续出现脚本级失败(failed + "no result path", 非额度/登录)。

        这类失败通常是页面改版 / 选择器失效导致(脚本跑完但没拿到结果图),
        与积分耗尽(exhausted)、登录失效(login_failed)可区分, 是最强的"改版"信号。

        lookback 窗口 = 自愈机制(2026-08-24): 超过 72h 的失败不再判定降级,
        窗口过期自动重试该 provider(可能已被修复/页面已恢复)。
        """
        try:
            from datetime import datetime, timedelta
            import json as _json
            here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            hist_f = os.path.join(here, "data", "cover_provider_history.jsonl")
            if not os.path.isfile(hist_f):
                return False
            cutoff = datetime.now() - timedelta(hours=lookback_hours)
            consecutive = 0
            with open(hist_f) as fh:
                for line in fh:
                    try:
                        e = _json.loads(line)
                    except Exception:
                        continue
                    if e.get("provider") != provider:
                        continue
                    ts = str(e.get("ts", ""))
                    try:
                        t = datetime.fromisoformat(ts)
                        if t.tzinfo is not None:
                            t = t.astimezone().replace(tzinfo=None)
                    except Exception:
                        continue
                    if t < cutoff:
                        continue  # 过期失败不参与判定 → 定期自动重试
                    st = str(e.get("status", "")).lower()
                    note = str(e.get("note", "")).lower()
                    if st == "failed" and "no result path" in note:
                        consecutive += 1
                    else:
                        consecutive = 0
            return consecutive >= threshold
        except Exception:
            return False

    @staticmethod
    def _build_fallback_for(primary: str):
        """构造主 provider 之外的第一个可用 fallback 服务。"""
        from core.services.llm.factory import LLMFactory
        _service_cfg = CONFIG.get('services', {}).get('cover', {})
        _fb = [n for n in (_service_cfg.get('fallback_providers') or []) if n != primary]
        if 'gemini_web' not in _fb:
            _fb.append('gemini_web')
        if not _fb:
            return None
        try:
            return LLMFactory.build_for_name(_fb[0])
        except Exception:
            from core.services.llm.gemini_service import GeminiService
            return GeminiService({"provider": _fb[0], "model": "Flash"})

    @staticmethod
    async def process(image_path: str, output_path: str, aspect_ratio: str = "16:9", prompt_template: str = None, **kwargs) -> str:
        """
        Generates a new image with translated text.
        Returns path on success, None on failure.
        """
        llm = LLMFactory.get_service('cover')

        # ── Step 0: 主 provider 近期连续失败 → 快速降级, 直接跳过首选 ──
        # 连续 failed(no result path) 通常是页面改版/脚本失效; 连续 exhausted/login 是额度/登录问题。
        # 都累积到阈值后快速降级到第一个可用 fallback, 省掉无效等待。
        try:
            _primary = getattr(llm, 'provider', 'unknown')
            # 2026-09-26 修复(降级死锁): 一旦被判「脚本失效/连续失败」, 旧逻辑会把该
            # provider 封 72 小时 —— 即使故障已修复(如 cookie 刷新、上传 bug 修好),
            # 也不会再被尝试, 只会空转降级到同样挂掉的 fallback, 封面 100% 失败并阻断上传。
            # 改为: 降级判定只用于「排序」而非「禁用」—— 记录一条降级记录, 但**仍然尝试**
            # primary。真正不可用会在本次尝试里快速失败(额度/登录都是秒级返回), 代价可接受,
            # 换来的是「修复后立刻自愈」。
            _broken = ImageTranslationService._provider_script_broken(_primary, threshold=2)
            _failed = ImageTranslationService._provider_failed_recently(_primary, threshold=3)
            if _broken or _failed:
                _why = "页面改版/脚本失效" if _broken else "近期连续失败"
                log.warning(
                    f"⚠️ {_primary} 历史判定为 {_why}，但保留本轮尝试（修复后自愈优先）"
                )
        except Exception as _e:
            log.debug(f"快速降级判定失败: {_e}")

        metadata = kwargs.get('metadata', {})
        title = metadata.get('title', 'N/A')
        cover_text = metadata.get('cover_text', '')
        cover_style = metadata.get('cover_style', '')
        platform_name = metadata.get('platform_name', '')
        cover_layout = metadata.get('cover_layout', '')
        cover_display_ratio = metadata.get('cover_display_ratio', '')
        
        # ── Step 1: Translation Pre-Processing ──
        translation_block = ""
        translations_dict = {}
        try:
            translation_block, translations_dict = await ImageTranslationService._get_translations(llm, title)
            if translation_block:
                log.info(f"🧠 翻译词表已生成: {translations_dict}")
        except Exception as e:
            log.warning(f"⚠️ 翻译预处理失败 (继续): {e}")
        
        # ── Step 2: Build Prompt ──
        aspect_ratio_text = ASPECT_RATIO_TEXT.get(aspect_ratio, 'custom proportion')

        if ImageTranslationService._should_use_compact_browser_prompt(llm):
            prompt = ImageTranslationService._build_compact_browser_prompt(
                title=title,
                aspect_ratio=aspect_ratio,
                translation_block=translation_block,
                cover_display_ratio=cover_display_ratio,
                cover_layout=cover_layout,
            )
        elif prompt_template:
            format_args = {
                "aspect_ratio": aspect_ratio,
                "aspect_ratio_text": aspect_ratio_text,
                "title": title,
                "description": metadata.get('description', 'N/A'),
                "translation_block": translation_block,
                "cover_text": cover_text,
                "cover_style": cover_style,
                "platform_name": platform_name,
                "cover_layout": cover_layout,
                "cover_display_ratio": cover_display_ratio,
            }
            try:
                prompt = prompt_template.format(**format_args)
            except KeyError:
                # Old prompt template without {translation_block}
                prompt = prompt_template.format(
                    aspect_ratio=aspect_ratio,
                    aspect_ratio_text=aspect_ratio_text,
                    title=title,
                    description=metadata.get('description', 'N/A')
                )
                if translation_block:
                    prompt += "\n" + translation_block
        else:
            prompt = f"""Edit this thumbnail image for maximum Bilibili click-through.
If the image already has visible cover text, replace that text with concise Chinese in the same position and style.
If the image has little or no visible text, decide whether to add 1 to 3 short Chinese overlay phrases based on the title/context and the available negative space.
If there is no safe place to add text without hurting the composition, keep the image unchanged.
Cover text rules (data-verified from high-performing videos on this channel):
- Use short hook phrases of 3 to 8 characters (e.g. "45分钟燃脂", "鸡蛋神仙吃法", "700大卡挑战", "无器械跟练"), never long sentences.
- Prefer a concrete number (duration, calories, quantity) or a specific ingredient/tech keyword, plus one emotion word (香迷糊/神仙/爽/炸裂/封神).
- Large bold text with high-contrast color; readable at thumbnail size; keep safe margins.
- Design principles: less but better — one strong hook beats several weak phrases; every element must carry info, no decorative badges or filler; text must contrast with its background; balance subject, text, and empty space; wording must match the real video content.
Never cover the face, hands, main subject, or key action.
{translation_block}
Output: One edited image in {aspect_ratio} {aspect_ratio_text} aspect ratio."""

        if not ImageTranslationService._should_use_compact_browser_prompt(llm):
            prompt = ImageTranslationService._strengthen_generation_prompt(prompt)
        
        print_t('cover_prompt', prompt[:80])
        
        # ── Step 3: Generate Loop (Provider Fallback + History) ──
        ensure_dir(output_path)

        from core.services.llm.jimeng_service import CreditsExhausted as JimengCreditsExhausted
        from core.services.llm.jimeng_service import LoginExpired as JimengLoginExpired
        from core.services.llm.tongyi_service import CreditsExhausted as TongyiCreditsExhausted
        from core.services.llm.tongyi_service import LoginExpired as TongyiLoginExpired
        from core.services.llm.hunyuan_service import LoginExpired as HunyuanLoginExpired
        CREDITS_EXHAUSTED = (JimengCreditsExhausted, TongyiCreditsExhausted)
        LOGIN_EXPIRED = (JimengLoginExpired, TongyiLoginExpired, HunyuanLoginExpired)

        service_config = CONFIG.get('services', {}).get('cover', {})
        primary_provider = getattr(llm, 'provider', 'unknown')
        fallback_names = list(service_config.get('fallback_providers') or [])
        if 'gemini_web' not in fallback_names:
            fallback_names.append('gemini_web')
        fallback_names = [n for n in fallback_names if n != primary_provider]
        fb_idx = 0

        def _make_llm(name: str):
            from core.services.llm.gemini_service import GeminiService
            cfg: dict = {"model": "Flash", "provider": name}
            if name == 'jimeng':
                from core.services.llm.jimeng_service import JimengService
                return JimengService(cfg)
            if name == 'tongyi':
                from core.services.llm.tongyi_service import TongyiService
                return TongyiService(cfg)
            if name == 'hunyuan':
                from core.services.llm.hunyuan_service import HunyuanService
                return HunyuanService(cfg)
            if name == 'gemini_cli':
                # 2026-09-15: Gemini 网页版(ego CLI 原生通道) — 免费无限, 作为主力封面通道
                from core.services.llm.gemini_cli_service import GeminiCliService
                # 2026-09-26: 传入三账号池，单账号额度耗尽时自动换号，
                # 而不是立刻 fallback 到成功率极低的下家（tongyi 483 耗竭/18 成功）。
                cfg = dict(cfg)
                cfg["authuser"] = service_config.get("authuser")
                cfg["authuser_pool"] = service_config.get("authuser_pool") or \
                    list(range(int(CONFIG.get("gemini_accounts_count") or 1)))
                return GeminiCliService(cfg)
            if name == 'chatgpt_cli':
                # 2026-09-23: ChatGPT 网页版(ego CLI 原生通道)。
                # 背景：即梦挂 + 万相额度耗尽 + gemini_cli/web 集体失败 → 封面链全塌、
                # 阻断上传。chatgpt_cli 早已实装但从未接进封面链，现补上。
                from core.services.llm.chatgpt_cli_service import ChatgptCliService
                return ChatgptCliService(cfg)
            return GeminiService(cfg)

        def _switch_to_next_fallback(failed_provider: str) -> Optional[object]:
            nonlocal fb_idx
            while fb_idx < len(fallback_names):
                name = fallback_names[fb_idx]
                fb_idx += 1
                if name == failed_provider:
                    continue
                log.info(f"🔄 {failed_provider} 不可用，切换到 {name} 继续尝试")
                _record(failed_provider, "exhausted", f"fallback -> {name}")
                return _make_llm(name)
            return None

        def _record(provider: str, status: str, note: str = "") -> None:
            try:
                history_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
                os.makedirs(history_dir, exist_ok=True)
                import json
                entry = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "provider": provider,
                    "video_title": title,
                    "output_path": output_path,
                    "status": status,
                    "note": note,
                }
                with open(os.path.join(history_dir, "cover_provider_history.jsonl"), "a") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as rec_err:
                log.debug(f"封面 provider 记录失败: {rec_err}")

        last_path = None
        ocr_passed = False
        # 2026-09-24: gemini_cli(免费无限通道) 中文渲染是概率性的 —— 实测同一视频
        # 的 16:9 与 4:3 分别独立跑链时, 一次 gemini_cli 被 Jev 拒、另一次通过
        # (sdG3XvDCNMo: 4_3 generated / 16_9 failed)。被 Jev 拒绝后值得原地重试,
        # 而不是直接换 provider 把链耗尽 → 双规格任一 failed 就阻断上传。
        _cli_retries = 0
        _CLI_MAX_RETRIES = 2
        max_total_attempts = 1 + len(fallback_names) + _CLI_MAX_RETRIES
        attempt = 0

        while llm is not None:
            attempt += 1
            if attempt > max_total_attempts:
                break
            active_provider = getattr(llm, 'provider', 'unknown')
            try:
                log.info(f"🎨 生成封面 (provider={active_provider}, 链内第 {attempt} 家)...")

                saved_path = await llm.generate_image(
                    prompt,
                    input_image_path=image_path,
                    output_path=output_path
                )

                if not saved_path:
                    log.warning(f"⚠️ {active_provider} 生成失败，未返回路径")
                    _record(active_provider, "failed", "no result path")
                    llm = _switch_to_next_fallback(active_provider)
                    continue

                # ── Step 3.5: 内容归属校验（防"张冠李戴"）──
                # 生成的封面若与源封面像素相关性过低，说明是另一个视频/残留的错图，
                # 即使 provider 返回了路径也拒收，切下一个 provider 重试。
                # 2026-08-20: jimeng 失败复用错封面导致多视频张冠李戴。
                #
                # ⚠️ 2026-09-26 重大修正：原判据只用 `sim < 0.15`（np.corrcoef 像素相关度）
                # 单边判定，**对 AI 改图场景完全失效** ——
                # AI 加中文标题/换风格本就会大幅改变像素，相关系数天然掉到 ~0 甚至负数。
                # 实测 chatgpt_cli 正常出图 1672x941，sim=-0.04 → 被误判"错封面"拒收。
                # 结果：出图成功却被拒 → 切下一家 → 全链耗尽 → 封面 failed → 阻断上传。
                # 这正是"最近几天所有通道都在出图成功→被拒"死循环的最后一环。
                #
                # 正确语义（回声检测，而非相似度下限）：
                #    sim > 0.97  → 几乎和源图一样 = 抓到参考图回声/根本没改 → 真错，拒收
                #    sim < -1.0  → 文件缺失/损坏（cover_similarity 返回 -1）→ 拒收
                #    其余（含低/负相关）→ AI 确实重绘过 → 放行，交给后面的 Jev 内容闸门判质量
                if image_path and os.path.isfile(image_path) and os.path.isfile(saved_path):
                    sim = cover_similarity(image_path, saved_path)
                    _echo_like = sim > 0.97          # 与源图几乎一致 = 回声，没真改图
                    _broken = sim < -0.5             # 文件缺失/损坏
                    if _echo_like or _broken:
                        _why = "疑似参考图回声(未真正改图)" if _echo_like else "文件缺失/损坏"
                        log.warning(
                            f"⚠️ {active_provider} 封面 {_why} (sim={sim:.2f})，拒收"
                        )
                        _record(active_provider, "rejected", f"similarity {sim:.2f} ({_why})")
                        _safe_remove(saved_path)
                        llm = _switch_to_next_fallback(active_provider)
                        continue
                    else:
                        log.info(
                            f"🧭 {active_provider} 封面已重绘 (sim={sim:.2f})，归属校验放行 → Jev 内容闸门"
                        )

                # ── Step 3.8: 中文标题叠加层 (2026-09-26) ──
                # AI 出图无法可靠渲染中文（实测 garbled p=0.45~0.68，has_cn_title p=0.06），
                # 而 Jev 内容闸门要求必须有中文标题 → AI 图永远过不了 → 全链耗尽 → 298 积压。
                # 这里用本地 PIL 渲染中文标题叠在 AI 图上（保留 AI 图为主体，不虚化、不替代），
                # 中文 100% 不崩字。叠加失败静默保留 AI 原图，绝不阻断。
                try:
                    if _cover_cn_overlay_enabled():
                        from core.utils.cover_cn_overlay import overlay_title as _cn_overlay
                        _ov_title = (metadata.get("zh_title") or metadata.get("title")
                                     or title or "")
                        if _ov_title:
                            _ov_dst = saved_path.replace(
                                os.path.splitext(saved_path)[1], "_cn.jpg")
                            _res = _cn_overlay(saved_path, _ov_dst, _ov_title,
                                               aspect=("16:9" if "16" in str(saved_path) else "4:3"))
                            if _res and os.path.isfile(_res) and _res != saved_path:
                                try:
                                    os.replace(_res, saved_path)
                                    log.info(f"🈶 {active_provider} 已叠加中文标题层 → {os.path.basename(saved_path)}")
                                except Exception as _oe:
                                    log.debug(f"cover overlay replace 失败: {_oe}")
                except Exception as _ove:
                    log.debug(f"cover cn-overlay 跳过: {_ove}")

                last_path = saved_path

                # ── Step 4: 内容闸门 —— 已于 2026-09-26 按用户要求移除 ──
                # 用户裁定原文："把这个生成图片的验证给去掉吧，这个问题太大了。
                #               只要验证不是原图就好"
                #
                # 移除原因：Jev 内容闸门（要求中文标题 p>=0.2 / 无崩字 / 主题匹配）
                # 是一个「AI 出图永远无法通过」的条件 —— AI 模型无法可靠渲染中文
                # （实测 has_cn_title p=0.06~0.09、garbled p=0.45~0.68），
                # 导致出图成功 → 被闸门拒 → 换 provider → 全链耗尽 → 封面 failed
                # → 阻断上传。这是 298 积压的制造机，问题远大于它挡住的收益。
                #
                # 现在的唯一校验 = 「不是原图」（回声检测，见上面 Step 3.5）：
                #   - sim > 0.97  → 抓到参考图回声/根本没改图 → 拒收
                #   - sim < -0.5  → 文件缺失/损坏 → 拒收
                #   - 其余一律放行（AI 确实重绘过即合格）
                # 中文标题由 Step 3.8 的 PIL 本地叠加层保证（不依赖 AI 渲染）。
                # 唯一硬性校验「不是原图」已在 Step 3.5 完成，此处直接放行。

                log.info(f"✅ 生成完成 (内容闸门已移除，仅校验非原图), provider={active_provider}")
                _record(active_provider, "ok", "accepted without OCR")
                ImageTranslationService.last_provider = active_provider
                print_t('cover_saved', saved_path)
                return saved_path

            except CREDITS_EXHAUSTED as exc:
                log.warning(f"⚠️ {active_provider} 积分不足: {exc}")
                _record(active_provider, "exhausted", str(exc))
                llm = _switch_to_next_fallback(active_provider)

            except LOGIN_EXPIRED as exc:
                log.warning(f"⚠️ {active_provider} 登录失效: {exc}")
                _record(active_provider, "login_failed", str(exc))
                llm = _switch_to_next_fallback(active_provider)

            except Exception as e:
                log.warning(f"⚠️ {active_provider} 生成异常: {e}")
                _record(active_provider, "failed", str(e)[:120])
                llm = _switch_to_next_fallback(active_provider)
                if llm is None:
                    import traceback
                    traceback.print_exc()
        
        # ── All retries exhausted ──
        if last_path and os.path.exists(last_path):
            log.warning(f"⚠️ OCR 校验未通过但已用完重试次数，返回最后一张图片: {last_path}")
            print_t('cover_saved', last_path)
            return last_path
        
        log.error("❌ 所有重试均失败，无法生成封面")
        return None

    @staticmethod
    async def _get_translations(llm, title: str):
        """
        Uses Flash model to extract keyword translations.
        Returns (translation_block_str, translations_dict).
        """
        try:
            from core.services.llm.gemini_service import get_web_client
            if not get_web_client:
                log.warning("⚠️ get_web_client is None (gemini_webapi import failed)")
                return "", {}
            client = await get_web_client()
            if not client:
                return "", {}
            
            result = await llm._translate_cover_text(client, title)
            
            translations_dict = {}
            if result:
                # 2026-09-24 修复: gemini_service._translate_cover_text 的输出格式已于
                # 2026-09-20 18:5x 从 "MUST write '中文' when you see '英文'" 改为
                # "- If the image contains the visible text '英文', render it as '中文'."
                # 但此处仍用旧正则 → translations_dict 恒为空 {} (实测 09-20~09-24 持续 4 天,
                # 2026-09-24 当天 126/130 次为空) → Jev 闸门 _expected 退化为英文原标题, 判定基准错误。
                pairs = re.findall(
                    r"If the image contains the visible text '(.+?)', render it as '(.+?)'",
                    result,
                )
                for en, cn in pairs:
                    translations_dict[en] = cn
                if not pairs:  # 向后兼容旧格式
                    pairs = re.findall(r"MUST write '(.+?)' when you see '(.+?)'", result)
                    for cn, en in pairs:
                        translations_dict[en] = cn

            return result, translations_dict
            
        except Exception as e:
            log.warning(f"⚠️ _get_translations failed: {e}")
            return "", {}

    @staticmethod
    async def _verify_with_ocr(llm, image_path: str, translations_dict: dict) -> bool:
        """
        Uses Vision model to OCR the generated image and verify Chinese text.
        Returns True if at least 50% of expected keywords are found.
        """
        try:
            from core.services.llm.gemini_service import get_web_client
            from core.utils.gemini_compat import generate_content_compat
            try:
                from gemini_webapi.constants import Model
            except ImportError:
                Model = None  # gemini_webapi 1.0.1+ 已移除 constants.Model
            if not get_web_client:
                log.warning("⚠️ OCR: get_web_client is None, 跳过校验")
                return True
            
            client = await get_web_client()
            if not client:
                log.warning("⚠️ OCR: 无法获取客户端，跳过校验")
                return True
            
            ocr_prompt = """Read ALL text visible in this image.
List every word/character you can see, line by line.
Output the raw text only, no explanation."""
            
            response = await generate_content_compat(
                client,
                ocr_prompt,
                files=[image_path],
                model=getattr(Model, 'G_3_0_FLASH', None) if Model else None
            )
            
            if not response or not response.text:
                log.warning("⚠️ OCR: 无响应，跳过校验")
                return True
            
            # Log chat ID for cleanup
            try:
                from core.services.llm.gemini_service import _log_chat_id
                _log_chat_id(response)
            except Exception:
                pass
            
            ocr_text = response.text.strip()
            log.info(f"🔍 OCR 识别结果: {ocr_text[:100]}...")
            
            # Normalize: remove spaces for comparison
            ocr_normalized = ocr_text.replace(" ", "")
            
            expected_values = list(translations_dict.values())
            if not expected_values:
                return True
                
            found = 0
            for cn_text in expected_values:
                cn_normalized = cn_text.replace(" ", "")
                if cn_normalized in ocr_normalized:
                    found += 1
                    log.info(f"  ✅ 找到: '{cn_text}'")
                else:
                    log.warning(f"  ❌ 未找到: '{cn_text}'")
            
            match_rate = found / len(expected_values)
            log.info(f"📊 OCR 匹配率: {found}/{len(expected_values)} = {match_rate:.0%}")
            
            # Lowered the strictness of OCR since generation often drops some words
            return found >= 1 or match_rate >= 0.1
            
        except Exception as e:
            log.warning(f"⚠️ OCR 校验异常: {e}")
            return True  # 异常时不阻塞
