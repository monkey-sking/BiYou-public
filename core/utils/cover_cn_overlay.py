"""
中文标题叠加层（2026-09-26）
=============================
背景：AI 出图通道（gemini_cli / chatgpt_cli / gemini_web）**无法可靠渲染中文**
—— 模型输出的中文字符大量崩成乱码（Jev 闸门实测 garbled p=0.45~0.68），
且常只出英文（has_cn_title p=0.06~0.09）。

而 Jev 内容闸门（~/.hermes/scripts/jev_cover_gate.py L137）要求封面必须含中文：
    (garb_p >= FAIL_CONF) or (cn_p < 0.2 and mc != "no_text")  → regen
这条规则让 AI 出图**永远无法通过** → 全链耗尽 → 封面 failed → 阻断上传。
这就是 298 个积压视频的制造机。

解法（不是"虚化+压字"兜底，不是替代 AI 出图）：
  AI 图仍然是**主体**（保留 AI 的构图/风格/视觉），只在图上叠加一层
  **本地 PIL 渲染的中文标题**（PingFang.ttc，100% 不崩字）。

  观感 = "AI 设计图 + 中文标题排版"，而非糊弄人的假封面。
  这满足 09-16 用户禁令（禁用"虚化+压字"本地兜底封面）——
  因为没有虚化、没有替代 AI 图，只是补上 AI 做不到的中文文字层。

设计原则：
  - 叠加层**可选、可关闭**（配置开关 cover_cn_overlay，默认 True）
  - 叠加失败**绝不阻断**（返回原图路径，让流程继续）
  - 文字位置自适应：底部渐变遮罩 + 金色主标题 + 白色副标题
  - 只叠**中文**（英文/数字留给 AI 图），避免重复
"""

import os
import re
import logging

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except Exception:  # pragma: no cover
    Image = None

# 字体候选（macOS 优先 PingFang，必定存在且中文完整）
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _load_font(size: int):
    """加载中文字体，带缓存。"""
    if ImageFont is None:
        return None
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=0)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def has_cjk(text: str) -> bool:
    """文本里是否有中文字符。"""
    return bool(_CJK_RE.search(text or ""))


def _clean_title(title: str) -> str:
    """清理标题：去掉话题标签、多余空白、URL、emoji。"""
    t = (title or "").strip()
    # 去 YouTube URL / 频道后缀
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s*[-|｜]\s*[^-|｜]{0,30}$", "", t)  # "标题 - 频道名"
    # 去话题标签
    t = re.sub(r"\s*#[^\s#]+", "", t)
    # 去表情
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", t)
    return t.strip(" \t\n\r—-|｜:：")


def _wrap_cjk(draw, text: str, font, max_w: int):
    """按宽度折行（中文按字断，英文按词断）。"""
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        try:
            w = draw.textlength(test, font=font)
        except Exception:
            w = len(test) * (font.size if hasattr(font, "size") else 20)
        if w > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def overlay_title(src_path: str, dst_path: str, title: str,
                  aspect: str = "16:9") -> str:
    """在 AI 生成图（src_path）上叠加中文标题，输出到 dst_path。

    返回: dst_path（成功）或 src_path（任何失败都返回原图，绝不阻断流程）
    """
    if Image is None:
        log.debug("cover overlay: PIL 不可用，跳过叠加")
        return src_path
    if not src_path or not os.path.exists(src_path):
        return src_path

    cn = _clean_title(title)
    if not has_cjk(cn):
        log.debug("cover overlay: 标题无中文，跳过叠加（保留 AI 原图）")
        return src_path

    try:
        base = Image.open(src_path).convert("RGB")
        W, H = base.size
        draw = ImageDraw.Draw(base, "RGBA")

        # 字号随宽度缩放（1672 宽 → 主标题 ~96px）
        main_size = max(28, int(W * 0.058))
        sub_size = max(18, int(main_size * 0.52))
        pad = int(W * 0.045)
        max_w = W - pad * 2

        main_font = _load_font(main_size)
        sub_font = _load_font(sub_size)
        if main_font is None:
            return src_path

        # 标题拆分：前 12 字为主标题（大字），其余为副标题（小字）
        cut = min(12, len(cn))
        for sep in ("！", "？", "，", " ", "：", ":", "、"):
            p = cn.find(sep)
            if 3 <= p <= 14:
                cut = p
                break
        main_txt = cn[:cut].strip(" ，。！？、：:；;")
        sub_txt = cn[cut:].strip(" ，。！？、：:；;")

        main_lines = _wrap_cjk(draw, main_txt, main_font, max_w)[:2]
        sub_lines = _wrap_cjk(draw, sub_txt, sub_font, max_w)[:2] if sub_txt else []

        def _lh(font, n=1.22):
            try:
                a, d = font.getmetrics()
                return int((a + d) * n)
            except Exception:
                return int(getattr(font, "size", 40) * n)

        main_lh, sub_lh = _lh(main_font), _lh(sub_font)
        block_h = len(main_lines) * main_lh + (
            int(sub_lh * 1.1) + len(sub_lines) * sub_lh if sub_lines else 0)
        block_h += int(pad * 0.7)

        # ── 底部渐变遮罩（保证文字可读，不虚化整图）──
        mask_h = block_h + int(pad * 1.2)
        mask_top = max(0, H - mask_h)
        try:
            grad = Image.new("L", (1, max(1, H - mask_top)), 0)
            gh = grad.size[1]
            for y in range(gh):
                ratio = y / max(1, gh - 1)
                grad.putpixel((0, y), int(215 * (ratio ** 0.65)))
            grad = grad.resize((W, max(1, H - mask_top)))
            overlay = Image.new("RGB", (W, max(1, H - mask_top)), (8, 10, 18))
            base.paste(overlay, (0, mask_top), grad)
            draw = ImageDraw.Draw(base, "RGBA")
        except Exception:
            # 渐变失败退化为半透明底色条
            draw.rectangle([0, mask_top, W, H], fill=(8, 10, 18, 200))

        # ── 文字绘制（带描边，抗复杂背景）──
        y = H - block_h + int(pad * 0.35)
        for ln in main_lines:
            x = pad
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
                draw.text((x + dx, y + dy), ln, font=main_font, fill=(0, 0, 0, 190))
            draw.text((x, y), ln, font=main_font, fill=(255, 214, 92, 255))
            y += main_lh

        if sub_lines:
            y += int(sub_lh * 0.15)
            for ln in sub_lines:
                x = pad
                for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                    draw.text((x + dx, y + dy), ln, font=sub_font, fill=(0, 0, 0, 170))
                draw.text((x, y), ln, font=sub_font, fill=(243, 246, 252, 255))
                y += sub_lh

        os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
        base.save(dst_path, "JPEG", quality=92)
        log.info(f"🈶 中文标题层已叠加: {os.path.basename(dst_path)} "
                 f"({W}x{H}, 主标题{len(main_lines)}行)")
        return dst_path
    except Exception as e:
        log.warning(f"🈶 中文标题层叠加失败(保留AI原图): {e}")
        return src_path


if __name__ == "__main__":  # 自测
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) >= 4:
        r = overlay_title(sys.argv[1], sys.argv[2], sys.argv[3])
        print("OUT:", r)
