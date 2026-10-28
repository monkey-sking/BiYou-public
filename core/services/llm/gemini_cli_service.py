"""Gemini 网页版出图（ego CLI 原生通道版）— 2026-09-14 新增

为什么要有它：
  `core/utils/browser_gemini.py`（2900 行）是 Playwright + CDP 39222 的老路线，
  而 ego lite 现在**没有对外 CDP 端口** → 那条路永远 "Connection failed"，
  再兜底去 standalone 启动一个 ego → 页面被关闭 → 出图失败。
  本模块改用和其它 provider（jimeng/tongyi）一致的 **ego-browser CLI 原生通道**
  （taskSpace / newPage / page.*），彻底不碰 CDP 端口，也不额外启动浏览器实例。

登录：复用 ego profile 里的 Google 登录（authuser 可指定 0/1/2）。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

project_root = str(Path(__file__).parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.services.llm.base import LLMBase
from core.utils.config import load_config
from core.utils.logger import log


class GeminiCliError(Exception):
    pass


class LoginExpired(Exception):
    """Google 登录失效。"""


class GeminiCliService(LLMBase):
    """用 ego CLI 驱动 gemini.google.com 生成/编辑图片。"""

    provider = "gemini_cli"

    GEMINI_URL = "https://gemini.google.com/app"

    def __init__(self, service_config: dict = None):
        self.config = load_config()
        self.service_config = service_config or {}
        self.provider = "gemini_cli"
        self.use_browser = True
        providers = self.config.get("providers", {})
        g = providers.get("gemini_cli", {}) or {}
        self.authuser = int(self.service_config.get("authuser") or g.get("authuser") or 0)
        # 2026-09-26: 三账号额度叠加 —— 主链此前只用 config 里写死的单个 authuser，
        # 一个账号烧干就直接换 provider，另外两个账号的额度全天没被用过。
        # 事后补做脚本 regen_ai_covers.py 早有 accounts=[0,1,2] 轮换，主链缺失。
        # 这里允许显式传入 authuser_pool（如 [0,1,2]），generate_image 失败时逐个换号重试。
        _pool = self.service_config.get("authuser_pool") or g.get("authuser_pool")
        if _pool:
            self.authuser_pool = [
                int(_pool) if isinstance(_pool, (int, str)) else int(x) for x in _pool
            ]
        else:
            _cnt = int(self.config.get("gemini_accounts_count") or 1)
            self.authuser_pool = list(range(max(1, _cnt))) if _cnt > 1 else [self.authuser]
        # 把当前账号排到队首，保证默认行为与配置一致
        if self.authuser in self.authuser_pool:
            self.authuser_pool = [self.authuser] + [a for a in self.authuser_pool if a != self.authuser]
        self.generate_timeout = float(
            self.service_config.get("generate_timeout_seconds")
            or g.get("generate_timeout_seconds")
            or 300
        )
        self.task_space = self.service_config.get("task_space") or g.get("task_space") or "biyou-gemini"
        # 2026-09-16: 模型档位必须在每次调用前显式指定。Google 会在额度耗尽后
        # 把页面回落到 Flash-Lite（不支持出图），此时发出去只会拿到文字回复 →
        # 空等 300s 判 gen-no。配置 providers.gemini_cli.model_picker 即可修复。
        _mp = self.service_config.get("model_picker") or g.get("model_picker") or ""
        if isinstance(_mp, str):
            self.model_pickers = [x.strip() for x in _mp.split(",") if x.strip()]
        else:
            self.model_pickers = [str(x).strip() for x in (_mp or []) if str(x).strip()]
        self.model_picker = ", ".join(self.model_pickers)
        # 2026-09-15: 唯一化 task_space —— 复用同一 space 反复 newPage 会触发 ego
        # reconcilePages/listTabs 崩溃(exit code 1)；唯一名彻底隔离每次调用。
        import uuid as _uuid
        self.task_space = self.task_space + "-" + _uuid.uuid4().hex[:6]
        self.ego_cli = os.path.expanduser(
            self.config.get("ego_cli") or g.get("ego_cli") or "~/.local/bin/ego-browser"
        )
        from core.services.llm.gemini_service import GeminiService

        self._text_service = GeminiService({"provider": "gemini_web", "model": "Flash"})

    async def generate_text(self, prompt: str, model: str = None) -> str:
        return await self._text_service.generate_text(prompt, model=model)

    async def _translate_cover_text(self, client, context_text: str) -> str:
        # 2026-09-15: image_translator._get_translations 会调这个方法；缺了会
        # AttributeError → 翻译词表退化为空(封面文字质量下降)。
        return await self._text_service._translate_cover_text(client, context_text)

    async def generate_image(
        self,
        prompt: str,
        input_image_path: str,
        output_path: str,
        model: str = None,
    ) -> Optional[str]:
        """2026-09-26: 账号池轮换入口 —— 单账号一次不成就换下一个 Google 账号。

        背景：主链此前只用 config 写死的 authuser（=2），#0/#1 的每日额度全天闲置，
        #2 烧干后整条链就被判“耗尽”→ 双规格任一失败 → 阻断上传。
        现在按 authuser_pool 逐个账号尝试，全部不成功才返回 None 交给上层 fallback。
        """
        _pool = list(getattr(self, "authuser_pool", None) or [self.authuser])
        _orig = self.authuser
        try:
            for _i, _au in enumerate(_pool):
                self.authuser = _au
                res = await self._generate_image_once(prompt, input_image_path, output_path)
                if res:
                    if _i > 0:
                        log.info(f"🔄 gemini_cli 换账号成功: authuser={_au}（第 {_i + 1}/{len(_pool)} 个）")
                    return res
                if _i + 1 < len(_pool):
                    log.info(f"🔄 gemini_cli authuser={_au} 未出图，换下一个账号重试")
            return None
        finally:
            self.authuser = _orig

    async def _generate_image_once(
        self,
        prompt: str,
        input_image_path: str,
        output_path: str,
    ) -> Optional[str]:
        if os.path.dirname(output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        input_image = input_image_path if os.path.exists(input_image_path or "") else None
        script = self._build_cli_script(prompt, input_image)

        try:
            proc = await asyncio.create_subprocess_exec(
                self.ego_cli, "nodejs",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(input=script.encode("utf-8")),
                timeout=self.generate_timeout + 120,
            )
        except Exception as e:
            log.warning(f"⚠️ gemini_cli: ego CLI 执行失败: {e}")
            return None

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        result_url, login_expired = None, False
        shot_file, clip, chunks = None, None, {}
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                line = line.strip()
                if line.startswith("RESULT="):
                    v = line[len("RESULT="):]
                    if v == "LOGIN_EXPIRED":
                        login_expired = True
                    elif v.startswith("file:"):
                        shot_file = v[len("file:"):]
                    elif v.startswith("http"):
                        result_url = v
                elif line.startswith("CLIP="):
                    try:
                        clip = json.loads(line[len("CLIP="):])
                    except Exception:
                        clip = None
                elif line.startswith("BLOBB64_") and ":" in line:
                    try:
                        head, data = line[len("BLOBB64_"):].split(":", 1)
                        chunks[int(head.split("/")[0])] = data
                    except Exception:
                        pass
                elif line.startswith("SHOTB64_") and ":" in line:
                    try:
                        head, data = line[len("SHOTB64_"):].split(":", 1)
                        chunks[int(head.split("/")[0])] = data
                    except Exception:
                        pass
                elif line.startswith("UPLOAD="):
                    log.info(f"📎 gemini_cli 上传: {line[len('UPLOAD='):]}")
                elif line.startswith("CONSENT="):
                    log.info(f"📎 gemini_cli 授权框: {line[len('CONSENT='):]}")
                elif line.startswith("IMG_URL="):
                    log.info(f"🧭 gemini_cli 生成图: {line[len('IMG_URL='):][:110]}")
                elif line.startswith("SENT="):
                    log.info(f"🧭 gemini_cli 发送: {line[len('SENT='):]}")
                elif line.startswith("DIAG="):
                    log.info(f"🧭 gemini_cli 诊断: {line[len('DIAG='):]}")
                elif line.startswith("MODEL_LIMIT="):
                    log.warning(f"🚫 gemini_cli 模型额度用尽: {line[len('MODEL_LIMIT='):]}")
                elif line.startswith("MODEL_SET="):
                    log.info(f"🎚️ gemini_cli 切档: {line[len('MODEL_SET='):]}")
                elif line.startswith("MODEL_VERIFY="):
                    log.info(f"🎚️ gemini_cli 档位校验: {line[len('MODEL_VERIFY='):]}")
                elif line.startswith("T="):
                    log.info(f"⏱️ gemini_cli 耗时: {line[2:]}")
                elif line.startswith("MODEL_FAIL="):
                    log.warning(f"🚫 gemini_cli 切档失败: {line[len('MODEL_FAIL='):]}")
        if login_expired:
            raise LoginExpired("Gemini 登录已失效")
        def _reject_if_echo(path):
            """结果与输入图几乎一致 = 抓到的是参考图回声，不是生成结果。

            2026-09-23 修：纯像素相关度误伤合法增强 —— gemini 在源图基础上生成封面时
            （保留主体构图 + 加文字/风格化），整体色彩分布保留，像素相关度天然高（实测 0.98）。
            而真回声（原图）文件大小接近源图。改成「像素相关度极高 + 文件大小接近源图」
            双重条件才判回声，避免把「合法增强封面」误判失败。
            """
            try:
                if input_image and os.path.exists(path):
                    from core.services.image_translator import cover_similarity
                    sim = cover_similarity(path, input_image)
                    log.info(f"🧭 gemini_cli 结果与源图相关度: {sim:.2f}")
                    if sim >= 0.99:
                        log.warning("⚠️ gemini_cli 结果与源图逐像素一致（回声），判为失败")
                        return None
                    if sim > 0.93:
                        # 中间带：真回声是同源图，体积也应接近源图
                        try:
                            s_ratio = os.path.getsize(path) / max(1, os.path.getsize(input_image))
                        except Exception:
                            s_ratio = None
                        # sim>=0.97 且体积接近 → 基本确定是回声
                        if sim >= 0.97 and s_ratio is not None and 0.7 <= s_ratio <= 1.4:
                            log.warning("⚠️ gemini_cli 结果与源图高度相似且体积接近（疑似回声），判为失败")
                            return None
                        log.info(f"✅ gemini_cli 相关度 {sim:.2f} 但体积差 {s_ratio}x，视为合法增强，放行")
                return path
            except Exception:
                return path

        if shot_file and os.path.exists(shot_file):
            from PIL import Image
            img = Image.open(shot_file)
            if clip and clip.get("w"):
                dpr = float(clip.get("dpr") or 1)
                x, y = max(0, int(clip["x"] * dpr)), max(0, int(clip["y"] * dpr))
                x2 = min(img.width, x + int(clip["w"] * dpr))
                y2 = min(img.height, y + int(clip["h"] * dpr))
                if x2 - x > 150 and y2 - y > 100:
                    img = img.crop((x, y, x2, y2))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(output_path, "JPEG", quality=95)
            log.info(f"✅ Gemini(CLI) 封面已保存（下载）: {output_path} {img.width}x{img.height}")
            return _reject_if_echo(output_path)
        if chunks:
            import base64
            b64 = "".join(chunks[i] for i in sorted(chunks))
            if b64.startswith("data:") and "," in b64:
                b64 = b64.split(",", 1)[1]
            raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw))
            if clip and clip.get("w"):
                dpr = float(clip.get("dpr") or 1)
                x, y = max(0, int(clip["x"] * dpr)), max(0, int(clip["y"] * dpr))
                x2 = min(img.width, x + int(clip["w"] * dpr))
                y2 = min(img.height, y + int(clip["h"] * dpr))
                if x2 - x > 150 and y2 - y > 100:
                    img = img.crop((x, y, x2, y2))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(output_path, "JPEG", quality=95)
            log.info(f"✅ Gemini(CLI) 封面已保存（截图裁剪）: {output_path} {img.width}x{img.height}")
            return _reject_if_echo(output_path)
        if not result_url:
            log.warning(f"⚠️ gemini_cli 未获取到结果图。stderr: {stderr[-300:]}")
            return None
        return await self._download(result_url, output_path)

    # ------------------------------------------------------------------ #

    def _build_cli_script(self, prompt: str, input_image: Optional[str]) -> str:
        script = self._CLI_TEMPLATE
        for key, val in (
            ("__TASK_SPACE__", json.dumps(self.task_space)),
            ("__URL__", json.dumps(f"{self.GEMINI_URL}?authuser={self.authuser}")),
            ("__INPUT_IMAGE__", json.dumps(input_image)),
            ("__PROMPT__", json.dumps(prompt)),
            ("__TIMEOUT__", str(float(self.generate_timeout))),
            ("__MODEL_PICKERS_JSON__", json.dumps(self.model_pickers)),
        ):
            script = script.replace(key, val)
        return script

    _CLI_TEMPLATE = r'''const task = await taskSpace(__TASK_SPACE__)
const page = await task.newPage()
try {
  const T0 = Date.now()
  const T = (label) => cliLog("T=" + ((Date.now() - T0) / 1000).toFixed(1) + "s " + label)
  await page.goto(__URL__, { waitUntil: "domcontentloaded" })
  // 页面就绪 = 输入框 + 模型档位 chip 都渲染出来（2026-09-16: 只看输入框会过早，
  // 实测 0.8s 就误判就绪 → 切档 no-picker、上传也不完整 → 白等 300s）
  let READY_INPUT = false, READY_PICKER = false
  for (let i = 0; i < 15; i++) {
    try {
      const st = await page.evaluate(String.raw`(() => {
        const boxes = [...document.querySelectorAll(".ql-editor,[contenteditable=true],rich-textarea textarea,textarea")]
          .filter(e => e.offsetParent !== null)
        const hasInput = boxes.some(e => e.getBoundingClientRect().y > 100)
        const hasPicker = !!document.querySelector(".model-picker-container, bard-mode-switcher")
        return JSON.stringify({ hasInput: hasInput, hasPicker: hasPicker })
      })()`)
      const o = JSON.parse(st)
      READY_INPUT = !!o.hasInput
      READY_PICKER = !!o.hasPicker
    } catch (e) {}
    if (READY_INPUT && READY_PICKER) break
    await wait(1)
  }
  T("page-ready input=" + READY_INPUT + " picker=" + READY_PICKER)
  if (!READY_INPUT) {
    cliLog("MODEL_FAIL=页面未就绪(输入框缺失)")
    throw new Error("PAGE_NOT_READY")
  }
  // 2026-09-19: 新版 Gemini 落地页已不渲染档位 chip（.model-picker-container / bard-mode-switcher 实测均为 0）
  // → 不再硬失败，静置后直接尝试出图；NO_PICKER 记日志便于追溯
  if (!READY_PICKER) {
    cliLog("MODEL_SKIP=no-picker(新版UI无档位chip)")
    await wait(5)
  }
  // 切换模型（2026-09-16: 多档候选，按顺序挑第一个额度可用的；全部受限则快速失败）
  // 背景: Google 在额度耗尽后会把页面回落到 Flash-Lite（不支持出图），
  // 必须每次调用前显式切到能出图的档位，否则消息发出去只拿到文字回复 → 空等 gen-no。
  const WANT_MODELS = __MODEL_PICKERS_JSON__
  let MODEL_LIMIT_HIT = null
  let MODEL_SET_OK = false
  // 已经停在目标档位就别再点菜单（实测菜单操作要 8.5s）；Flash-Lite 一律视为不合格
  let MODEL_ALREADY = ""
  try {
    MODEL_ALREADY = await page.evaluate(String.raw`(() => {
      const el = document.querySelector(".model-picker-container, bard-mode-switcher")
      return el ? (el.innerText || "").trim().replace(/\s+/g, " ") : ""
    })()`)
  } catch (e) {}
  const MODEL_ALREADY_OK = !/Flash-Lite/i.test(MODEL_ALREADY) && WANT_MODELS.some(w => {
    const key = (w || "").split(" ").pop()
    return !!key && (MODEL_ALREADY || "").includes(key)
  })
  if (MODEL_ALREADY_OK) cliLog("MODEL_SKIP=已是 " + MODEL_ALREADY)
  if (WANT_MODELS.length && !MODEL_ALREADY_OK && READY_PICKER) {
    try {
      const picker = await page.evaluate(String.raw`(() => {
        const el = document.querySelector(".model-picker-container, bard-mode-switcher")
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
      })()`)
      if (picker) {
        await page.mouse.click(picker.x, picker.y, { label: "打开模型菜单" })
        await wait(3)
        const items = await page.evaluate(`(() => {
          const wants = __MODEL_PICKERS_JSON__
          return [...document.querySelectorAll("gem-menu-item,[role=menuitem],[role=menuitemradio]")]
            .filter(e => e.offsetParent !== null)
            .map(e => { const t = (e.innerText || "").replace(/\\s+/g, " ").slice(0, 90)
                        const r = e.getBoundingClientRect()
                        return { text: t, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) } })
            .filter(o => wants.some(w => o.text.includes(w)))
        })()`)
        // 带「用量限额重置时间」的档位视为额度已耗尽 → 优先选没这个标记的
        const avail = (items || []).filter(o => !/限额|重置|Limit|Reached/i.test(o.text))
        if (avail.length) {
          await page.mouse.click(avail[0].x, avail[0].y, { label: "选择模型" })
          await wait(4)
          MODEL_SET_OK = true
          cliLog("MODEL_SET=" + avail[0].text)
          // 回读 chip 校验是否真的切过去了（防 Google 改版/点击静默失效）
          const chip = await page.evaluate(String.raw`(() => {
            const el = document.querySelector(".model-picker-container, bard-mode-switcher")
            return el ? (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 40) : ""
          })()`)
          cliLog("MODEL_VERIFY=" + chip)
          if (/Flash-Lite/i.test(chip)) MODEL_LIMIT_HIT = "切档后仍是 Flash-Lite: " + chip
        } else if ((items || []).length) {
          MODEL_LIMIT_HIT = (items || []).map(o => o.text).join(" / ")
        } else {
          cliLog("MODEL_SET=not-found")
        }
      } else {
        cliLog("MODEL_SET=no-picker")
      }
    } catch (e) { cliLog("MODEL_ERR=" + String((e && e.message) || e).slice(0, 80)) }
    if (MODEL_LIMIT_HIT) {
      cliLog("MODEL_LIMIT=" + MODEL_LIMIT_HIT)
      throw new Error("MODEL_LIMIT")
    }
    if (!MODEL_SET_OK) {
      cliLog("MODEL_FAIL=切档未成功")
      throw new Error("MODEL_FAIL")
    }
  }

  T("model-done")

  // 每次开新对话（旧对话累积历史后 Gemini 常卡在"正在创建您的图片"，实测对比得出）
  try {
    await page.evaluate(String.raw`(() => {
      const b = [...document.querySelectorAll("button,[role=button],a")]
        .filter(e => e.offsetParent !== null)
        .find(e => /新建对话|新对话|New chat|发起新对话/.test((e.getAttribute("aria-label") || "") + (e.innerText || "")))
      if (b) { b.click(); return true }
      return false
    })()`)
    for (let i = 0; i < 6; i++) {
      try {
        const ok = await page.evaluate(String.raw`(() => {
          const boxes = [...document.querySelectorAll(".ql-editor,[contenteditable=true],rich-textarea textarea,textarea")]
            .filter(e => e.offsetParent !== null)
          return boxes.some(e => e.getBoundingClientRect().y > 100)
        })()`)
        if (ok) break
      } catch (e) {}
      await wait(1)
    }
  } catch (e) {}
  T("newchat")

  // 登录态：未登录会看到"登录"按钮
  const needLogin = await page.evaluate(String.raw`(() => {
    const t = document.body.innerText || ""
    return /登录|Sign in/.test(t) && !/发消息|Ask Gemini|有什么可以帮忙/.test(t)
  })()`)
  if (needLogin) {
    cliLog("RESULT=LOGIN_EXPIRED")
  } else {
    const INPUT_IMAGE = __INPUT_IMAGE__

    // ── 1. 上传参考图 ──
    // 2026-09-26 重写：原 CDP 拖放（Input.dispatchDragEvent）在 Gemini 新版页面上
    // **收效不稳定且检测误判**（检测用 input[type=file] 存在性 → 恒真 →
    // 日志 drop-ok 但图没上传，提示词被当纯文本）。改为优先 setInputFiles
    // （与 chatgpt_cli 同一套已验证方案），CDP 拖放仅作兜底。
    if (INPUT_IMAGE) {
      let up = "no-target"
      const _checkUploaded = String.raw`(() => {
        const t = (document.body.innerText || "")
        // 2026-09-26 实测：Gemini 上传成功后 body 文案明确出现「图片已上传」
        // （这是唯一可靠的判据）。早先用 img[src^='blob:'] / data:image 会把
        // 页面自带的头像（lh3.googleusercontent.com 的 128x128）误判成缩略图。
        if (/图片已上传|Image uploaded|ファイルをアップロードしました/.test(t)) return "confirmed"
        // 移除按钮（真上传了才会出现）
        const rm = document.querySelector(
          "button[aria-label*='移除' i], button[aria-label*='删除' i], " +
          "button[aria-label*='remove' i], button[aria-label*='delete' i]"
        )
        if (rm) return "remove-btn"
        return ""
      })()`

      // ── 方式 A: setInputFiles（首选；与 chatgpt_cli 同一套已验证写法）──
      // 注意：直接用字符串选择器，不要用 page.locator() 包装
      // （ego 的 page 对象不一定有 locator API，会静默抛异常）。
      try {
        await page.setInputFiles("input[type=file][accept*='image'] >> nth=0", [INPUT_IMAGE])
        up = "setinput-sent"
      } catch (e1) {
        cliLog("SETINPUT_A_ERR=" + String((e1 && e1.message) || e1).slice(0, 90))
        try {
          await page.setInputFiles("input[type=file] >> nth=0", [INPUT_IMAGE])
          up = "setinput-sent-generic"
        } catch (e2) {
          cliLog("SETINPUT_B_ERR=" + String((e2 && e2.message) || e2).slice(0, 90))
        }
      }
      if (up.startsWith("setinput")) {
        for (let i = 0; i < 10; i++) {
          const ok2 = await page.evaluate(_checkUploaded)
          if (ok2) { up = "ok:" + ok2; break }
          await wait(1)
        }
        if (!up.startsWith("ok:")) up = up + "-unconfirmed"
      }

      // ── 方式 B: CDP 拖放（兜底，仅当 A 未确认）──
      if (!up.startsWith("ok:")) {
        try {
          const tgt = await page.evaluate(String.raw`(() => {
            const boxes = [...document.querySelectorAll(".ql-editor,[contenteditable=true],rich-textarea,textarea")]
              .filter(e => e.offsetParent !== null)
            const el = boxes.find(e => e.getBoundingClientRect().y > 100) || boxes[0]
            if (!el) return null
            const r = el.getBoundingClientRect()
            return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
          })()`)
          if (tgt) {
            const data = { items: [], files: [INPUT_IMAGE], dragOperationsMask: 1 }
            await page.cdp("Input.dispatchDragEvent", { type: "dragEnter", x: tgt.x, y: tgt.y, data })
            await page.cdp("Input.dispatchDragEvent", { type: "dragOver", x: tgt.x, y: tgt.y, data })
            await page.cdp("Input.dispatchDragEvent", { type: "drop", x: tgt.x, y: tgt.y, data })
            await wait(1)
            for (let i = 0; i < 7; i++) {
              const ok2 = await page.evaluate(_checkUploaded)
              if (ok2) { up = "drop-ok:" + ok2; break }
              await wait(1)
            }
            if (!up.startsWith("drop-ok:")) up = "drop-unconfirmed"
          }
        } catch (e) {
          up = "err:" + String((e && e.message) || e).slice(0, 120)
        }
      }
      cliLog("UPLOAD=" + up)
      T("upload")
      await wait(1)
      // 上传后 Gemini 会弹"根据图片和文件生成内容"授权框（取消/同意）——必须点同意
      // 2026-09-16 优化: 原来固定 wait(4)+6×wait(2)+wait(2)=18s，但弹窗多数时候不出现；
      // 改成短轮询 + 找到就点，没出现最多等 6s。
      let consentSeen = false
      for (let i = 0; i < 6; i++) {
        const agreed = await page.evaluate(String.raw`(() => {
          const b = [...document.querySelectorAll("button,[role=button]")]
            .filter(e => e.offsetParent !== null)
            .find(e => /^(同意|Agree|I agree|允许)$/.test((e.innerText || "").trim()))
          if (b) { b.click(); return true }
          return false
        })()`)
        if (agreed) { cliLog("CONSENT=agreed"); consentSeen = true; break }
        await wait(1)
      }
      if (!consentSeen) cliLog("CONSENT=none")
      await wait(1)
    }

    // ── 2. 输入提示词（先清空输入框，防重复输入 2026-09-15）──
    const focused = await page.evaluate(String.raw`(() => {
      const boxes = [...document.querySelectorAll(".ql-editor,[contenteditable=true],rich-textarea textarea,textarea")]
        .filter(e => e.offsetParent !== null)
      const main = boxes.find(e => e.getBoundingClientRect().y > 100) || boxes[0]
      if (!main) return false
      main.focus(); main.click()
      if (main.tagName === "TEXTAREA") { main.value = "" } else { main.textContent = "" }
      return true
    })()`)
    if (!focused) {
      cliLog("RESULT=NONE"); cliLog("DIAG=no-input-box")
    } else {
      const PROMPT = __PROMPT__
      // 记录当前已有图片(含参考图)作为基线 → 生成后"新增"的图才是结果图
      const BASELINE = await page.evaluate(String.raw`[...document.querySelectorAll("img")].map(i => i.src)`)
      const baseJson = JSON.stringify(BASELINE || [])
      await page.keyboard.type(PROMPT, { delay: 2 })
      await wait(1)
      // ── 发送：多策略 + 校验输入框已清空（2026-09-15：发送偶发失效导致 gen-no）──
      const readInputLen = async () => await page.evaluate(String.raw`(() => {
        const boxes = [...document.querySelectorAll(".ql-editor,[contenteditable=true],rich-textarea textarea,textarea")]
          .filter(e => e.offsetParent !== null)
        const main = boxes.find(e => e.getBoundingClientRect().y > 100) || boxes[0]
        if (!main) return -1
        const t = main.tagName === "TEXTAREA" ? (main.value || "") : (main.innerText || main.textContent || "")
        return t.trim().length
      })()`)
      let sent = "no"
      for (let attempt = 1; attempt <= 4 && sent === "no"; attempt++) {
        try {
          const clicked = await page.evaluate(String.raw`(() => {
            const b = [...document.querySelectorAll("button,[role=button]")]
              .filter(e => e.offsetParent !== null && !e.disabled)
              .find(e => /发送|Send|提交|Submit/.test((e.getAttribute("aria-label") || "") + " " + (e.innerText || "")))
            if (!b) return "no-btn"
            // 2026-09-16 关键修复: 普通 .click() 在 Gemini 上会静默失效（实测点完输入框内容还在、
            // 最终 gen-no）。改为派发完整 pointer/mouse 事件序列 —— 多账号手工验证此法可发出。
            const opts = { bubbles: true, cancelable: true, composed: true, view: window, button: 0 }
            for (const [t, C] of [["pointerdown", PointerEvent], ["mousedown", MouseEvent],
                                  ["pointerup", PointerEvent], ["mouseup", MouseEvent], ["click", MouseEvent]]) {
              try { b.dispatchEvent(new C(t, opts)) } catch (e) {}
            }
            return "fired"
          })()`)
          if (clicked === "no-btn") { await page.keyboard.press("Enter"); sent = "enter" + attempt }
          else { sent = "button" + attempt }
        } catch (e) { cliLog("SEND_ERR=" + String((e && e.message) || e).slice(0, 60)) }
        await wait(2)
        if (Number(await readInputLen()) === 0) { sent = sent + "|ok"; break }
        // 没发出去 → 重新聚焦再试
        await page.evaluate(String.raw`(() => {
          const boxes = [...document.querySelectorAll(".ql-editor,[contenteditable=true],rich-textarea textarea,textarea")]
            .filter(e => e.offsetParent !== null)
          const main = boxes.find(e => e.getBoundingClientRect().y > 100) || boxes[0]
          if (main) { main.focus(); main.click() }
        })()`)
        await wait(1)
      }
      cliLog("SENT=" + sent)
      T("sent")

      // ── 3. 等结果 → 「下载完整尺寸的图片」→ blob → 截图兜底；卡住自动「重做」──
      const deadline = Date.now() + __TIMEOUT__ * 1000
      let got = "no"
      let redos = 0
      let sentAt = Date.now()
      let TEXT_ONLY_HITS = 0
      while (Date.now() < deadline && got === "no") {
        // 2026-09-20 快速失败：Gemini 明确回文字（未出图）时不要死等到超时。
        // 实测案例：Rick Astley MV 封面连续 3 次各等 343s 拿 gen-no，
        // 每次都白等 5-6 分钟。检测到「已回复但无图」→ 直接放弃本次尝试。
        try {
          const txtOnly = await page.evaluate(String.raw`(() => {
            const body = (document.body.innerText || "")
            // Gemini 的免责声明出现 = 已给出完整文字回答
            const answered = /Gemini 是一款 AI 工具|Gemini can make mistakes|回答未必正确/.test(body)
            const imgs = [...document.querySelectorAll("img")]
              .filter(i => (i.naturalWidth || 0) > 200 && (i.naturalHeight || 0) > 150)
            return { answered: answered, bigImgs: imgs.length }
          })()`)
          if (txtOnly.answered && txtOnly.bigImgs === 0) {
            TEXT_ONLY_HITS++
            cliLog("TEXT_ONLY_ABORT=" + TEXT_ONLY_HITS)
            if (TEXT_ONLY_HITS >= 2) {
              cliLog("TEXT_ONLY_GIVEUP（连续文字回复，判定该图不可出图）")
              break
            }
          }
        } catch (e) {}

        let st = null
        try {
          st = await page.evaluate(`(() => {
            const body = document.body.innerText || ""
            const BASE = new Set(${baseJson})
            const cands = [...document.querySelectorAll("img")]
              .map(im => ({ s: im.src || "", nw: im.naturalWidth || 0, nh: im.naturalHeight || 0, r: im.getBoundingClientRect() }))
              .filter(o => o.nw >= 400 && o.r.width > 250 && !BASE.has(o.s))
            const pick = cands.length >= 1 ? cands[cands.length - 1] : null
            // 2026-09-16 修复误判: 原来拿整页 body 找"限流"字样，但左侧历史对话标题里
            // 就存在 "Image Editing Limit Reached" → 判定永远为真 → 通道被永久判死。
            // 改为只检测【最后一条模型回复】的文本。
            const _respNodes = [...document.querySelectorAll("model-response, .model-response-text, message-content, .response-container")]
              .filter(e => e.offsetParent !== null || e.tagName === "MODEL-RESPONSE")
            const _lastResp = _respNodes.length ? (_respNodes[_respNodes.length - 1].innerText || "") : ""
            return {
              nBig: cands.length,
              limit: /Image Editing Limit Reached|limit reached|达到上限|额度已用完|额度已满|额度重置|无法创建更多图片|无法生成图片|已达使用上限/i.test(_lastResp),
              gen: /正在创建|Generating|正在生成/.test(body),
              tail: body.replace(/\s+/g, " ").slice(-70),
              url: pick ? pick.s : null,
              nw: pick ? pick.nw : 0,
              nh: pick ? pick.nh : 0,
              rect: pick ? { x: Math.round(pick.r.x), y: Math.round(pick.r.y), w: Math.round(pick.r.width),
                             h: Math.round(pick.r.height), dpr: window.devicePixelRatio || 1 } : null,
            }
          })()`)
        } catch (e) {
          cliLog("EVAL_ERR=" + String((e && e.message) || e).slice(0, 80))
        }

        if (st && st.limit) {
          cliLog("RESULT=EXHAUSTED"); cliLog("DIAG=gemini-limit-reached")
          got = "limit"; break
        }
        if (st && st.rect && st.nw >= 400) {
          T("img-found")
          cliLog("IMG_URL=" + String(st.url).slice(0, 70) + "|nw=" + st.nw + "|nBig=" + st.nBig)

          // B. canvas 取原图（blob: 同源不污染 canvas；绕开 Gemini 页面 CSP 对 fetch 的限制，2026-09-15）
          if (got === "no" && st.url) {
            try {
              const urlJson = JSON.stringify(st.url)
              const total = Number(await page.evaluate(`(() => {
                try {
                  const im = [...document.querySelectorAll('img')].find(i => i.src === ${urlJson})
                  if (!im || !im.naturalWidth) return -1
                  const c = document.createElement('canvas')
                  c.width = im.naturalWidth; c.height = im.naturalHeight
                  c.getContext('2d').drawImage(im, 0, 0)
                  window.__gemDurl = c.toDataURL('image/png')
                  return window.__gemDurl.length
                } catch (e) { return -2 }
              })()`)) || 0
              if (total > 5000) {
                const CHUNK = 400000
                let acc = ""
                for (let off = 0; off < total; off += CHUNK) {
                  const part = await page.evaluate(`window.__gemDurl.substr(${off}, ${CHUNK})`)
                  acc += String(part || "")
                }
                if (acc.length > 5000) {
                  const CH = 8000, n2 = Math.ceil(acc.length / CH)
                  cliLog("FETCH_LEN=" + acc.length)
                  for (let i = 0; i < n2; i++) cliLog("BLOBB64_" + i + "/" + n2 + ":" + acc.substr(i * CH, CH))
                  got = "blob"
                }
              } else {
                cliLog("FETCH_SHORT=" + total)
              }
            } catch (e) {
              cliLog("FETCH_ERR=" + String((e && e.message) || e).slice(0, 80))
            }
          }

          // B2. canvas 不可用时的直链兜底 → 交给 Python 下载
          if (got === "no" && st.url && /^https?:/i.test(st.url)) {
            cliLog("RESULT=" + st.url)
            got = "url"
          }

          // A(兜底). canvas 拿不到时才走下载按钮——实测下载事件常不触发会白等 40s，
          // 而 canvas 通常 0.1s 就够，所以降到第二位并把超时砍到 10s（2026-09-16 优化）
          if (got === "no") {
            T("download-fallback-start")
            try {
              await page.mouse.move(st.rect.x + Math.round(st.rect.w / 2), st.rect.y + Math.round(st.rect.h / 2), { label: "悬停结果图" })
              await wait(2)
              const dlBtn = await page.evaluate(String.raw`(() => {
                const b = [...document.querySelectorAll("button,[role=button]")].filter(e => e.offsetParent !== null)
                  .find(e => /下载完整尺寸的图片|下载图片|Download full|Download image/.test((e.getAttribute("aria-label") || "") + (e.innerText || "")))
                if (!b) return null
                const r = b.getBoundingClientRect()
                return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
              })()`)
              if (dlBtn) {
                const dp = page.waitForEvent("download", { timeout: 10000 }).catch(() => null)
                await page.mouse.click(dlBtn.x, dlBtn.y, { label: "点击下载完整尺寸图片" })
                const dn = await dp
                if (dn) {
                  await dn.saveAs("/tmp/gemini_cli_dl.png")
                  cliLog("RESULT=file:/tmp/gemini_cli_dl.png")
                  got = "download"
                } else {
                  cliLog("DL_NO_EVENT=1")
                }
              }
            } catch (e) {
              cliLog("DL_TRY_ERR=" + String((e && e.message) || e).slice(0, 80))
            }
            T("download-fallback-done")
          }


          // C. 截图裁剪兜底（用 CDP clip 精确裁剪，避免截入 Gemini 输入框/悬浮按钮）
          if (got === "no") {
            try {
              try { await page.mouse.move(8, 8) } catch (e) {}
              await wait(1)
              let shot = null
              const cropH = (st.nh && st.nw) ? Math.round(st.rect.w * st.nh / st.nw) : st.rect.h
              const clipRect = { x: Math.max(0, st.rect.x), y: Math.max(0, st.rect.y),
                                 width: st.rect.w, height: cropH, scale: st.rect.dpr || 1 }
              for (let a = 0; a < 3 && !shot; a++) {
                try { shot = await page.cdp("Page.captureScreenshot", { format: "png", clip: clipRect }) } catch (e) { await wait(2) }
              }
              if (shot && shot.data && shot.data.length > 5000) {
                const CH = 8000, n3 = Math.ceil(shot.data.length / CH)
                cliLog("SHOT_LEN=" + shot.data.length)
                for (let i = 0; i < n3; i++) cliLog("SHOTB64_" + i + "/" + n3 + ":" + shot.data.substr(i * CH, CH))
                got = "shot"
              }
            } catch (e) {
              cliLog("SHOT_ERR=" + String((e && e.message) || e).slice(0, 80))
            }
          }
        }

        if (got === "no") {
          // 卡住超过 90 秒 → 点「重做」重试（页面确实有这个按钮）
          if (Date.now() - sentAt > 90000 && redos < 2) {
            try {
              const hit = await page.evaluate(String.raw`(() => {
                const b = [...document.querySelectorAll("button,[role=button]")].filter(e => e.offsetParent !== null)
                  .find(e => /^(重做|重试|Redo|Retry|重新生成)$/.test((e.innerText || "").trim()))
                if (!b) return null
                const r = b.getBoundingClientRect()
                return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
              })()`)
              if (hit) {
                await page.mouse.click(hit.x, hit.y, { label: "点击重做" })
                redos++
                sentAt = Date.now()
                cliLog("REDO=" + redos)
              }
            } catch (e) {}
          }
          await wait(4)
        }
      }
      T("wait-loop-end got=" + got)
      if (got === "no") {
        try {
          const diag = await page.evaluate(String.raw`(() => {
            const srcs = [...document.querySelectorAll("img")].map(i => (i.src || "").slice(0, 60))
            const body = (document.body.innerText || "").replace(/\s+/g, " ")
            return { nImg: srcs.length, sample: srcs.slice(0, 4), tail: body.slice(-140) }
          })()`)
          cliLog("DIAG=" + JSON.stringify(diag))
        } catch (e) {}
      }
      cliLog("DIAG=gen-" + got)
    }
  }
} finally {
  await task.finish({ keep: [] })
}
'''

    async def _download(self, url: str, output_path: str) -> str:
        import httpx
        # googleusercontent 需要在浏览器上下文里取（可能带签名/校验），先直连试，失败则用 CLI 下载
        raw = None
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
                "Referer": "https://gemini.google.com/",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }) as client:
                resp = await client.get(url)
                log.info(f"🧭 gemini_cli 直连下载: HTTP {resp.status_code} {len(resp.content)}B")
                if resp.status_code == 200 and len(resp.content) > 5000:
                    raw = resp.content
        except Exception as e:
            log.info(f"🧭 gemini_cli 直连下载异常: {type(e).__name__} {str(e)[:80]}")
        if not raw:
            raw = await self._download_via_cli(url)
            log.info(f"🧭 gemini_cli 浏览器取图: {'ok ' + str(len(raw)) + 'B' if raw else 'fail'}")
        if not raw:
            raise GeminiCliError("图片下载失败")
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw))
            if img.mode != "RGB":
                img = img.convert("RGB")
            if output_path.lower().endswith((".jpg", ".jpeg")):
                img.save(output_path, "JPEG", quality=95)
            else:
                img.save(output_path, "PNG")
        except Exception:
            with open(output_path, "wb") as fh:
                fh.write(raw)
        log.info(f"✅ Gemini(CLI) 封面已保存: {output_path}")
        return output_path

    async def _download_via_cli(self, url: str) -> Optional[bytes]:
        """在 ego 页面里 fetch 图片（带登录 cookie）并 base64 回传。"""
        script = f'''const task = await taskSpace({json.dumps(self.task_space)})
const page = await task.newPage()
try {{
  await page.goto({json.dumps(self.GEMINI_URL + "?authuser=" + str(self.authuser))}, {{ waitUntil: "domcontentloaded" }})
  await wait(6)
  const b64 = await page.evaluate(`(async () => {{
    try {{
      const r = await fetch(${json.dumps(url)}, {{ credentials: "include" }})
      if (!r.ok) return "ERR:HTTP" + r.status
      const buf = await r.arrayBuffer()
      let s = ""; const b = new Uint8Array(buf)
      for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i])
      return btoa(s)
    }} catch (e) {{ return "ERR:" + e.message }}
  }})()`)
  const d = String(b64 || "")
  if (d.startsWith("ERR")) {{ cliLog("FETCH_ERR=" + d.slice(0, 90)) }}
  else {{
    const CH = 8000, n = Math.ceil(d.length / CH)
    cliLog("FETCH_LEN=" + d.length)
    for (let i = 0; i < n; i++) cliLog("FETCHB64_" + i + "/" + n + ":" + d.substr(i * CH, CH))
  }}
}} finally {{ await task.finish({{ keep: [] }}) }}
'''
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ego_cli, "nodejs",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(input=script.encode("utf-8")), timeout=240)
        except Exception:
            return None
        import base64
        chunks = {}
        combined = out.decode("utf-8", "replace") + err.decode("utf-8", "replace")
        for line in combined.splitlines():
            line = line.strip()
            if line.startswith("FETCH_ERR="):
                log.info(f"🧭 gemini_cli 页面 fetch 失败: {line[10:110]}")
            elif line.startswith("FETCHB64_") and ":" in line:
                try:
                    head, data = line[len("FETCHB64_"):].split(":", 1)
                    chunks[int(head.split("/")[0])] = data
                except Exception:
                    pass
        if not chunks:
            return None
        b64 = "".join(chunks[i] for i in sorted(chunks))
        try:
            return base64.b64decode(b64 + "=" * (-len(b64) % 4))
        except Exception:
            return None
