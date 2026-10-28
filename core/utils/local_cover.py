"""本地封面合成（2026-09-14）

背景：在线封面服务（gemini_web 浏览器路径 / jimeng / tongyi 万相）全部不可用时，
main.py 的兜底只用 ffmpeg 把 YouTube 原图裁一刀 → 封面等于"没处理"，用户能看出来。

本模块把兜底升级成"真的处理过"：
  - 按目标比例 cover-crop + LANCZOS 缩放（16:9 → 1280x720，4:3 → 1200x900）
  - 底部黑色渐变遮罩
  - 居中排版中文标题（自动折行 + 自适应字号 + 描边，最多 2 行）
不依赖任何外部服务，永远可用。

用法:
    from core.utils.local_cover import compose
    compose("/path/orig_cover.jpg", "/path/out_16x9.jpg", "16:9", "标题文字")
"""
from __future__ import annotations

import os
import re

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]

SIZES = {"16:9": (1280, 720), "4:3": (1200, 900)}


def _load_font(size: int):
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


BREAK_CHARS = set(" ，。！？、；：,.、！？;:>】》)]}")


def _tokenize(text: str):
    """把文本切成"不可切断的块"：连续 ASCII 词/数字算一块，中文逐字，空格保留。"""
    import re
    tokens, buf = [], ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "._-+&'"):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    return tokens


def _wrap(draw, text: str, font, max_w: int):
    """词感知折行：拉丁单词整体不拆，中文可逐字；优先在空格/标点后断行。"""
    lines, cur = [], ""

    def width_of(s):
        try:
            return draw.textlength(s, font=font)
        except Exception:
            return len(s) * (font.size if font else 10)

    for tok in _tokenize(text.replace("\r", "")):
        if tok == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + tok
        if width_of(trial) <= max_w or not cur:
            cur = trial
            continue
        # 需要断行：若当前行以空格结尾，或本 token 是标点/空格，直接断
        lines.append(cur.rstrip())
        cur = "" if tok == " " else tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def pick_frame(video_path: str, out_path: str, fracs=(0.30, 0.45, 0.60, 0.75, 0.85)):
    """从视频里抽几帧，挑"信息量最大"（灰度方差最高、非纯黑/纯白）的一帧。

    动机（2026-09-14）：YouTube 缩略图常常烤了英文大字，本地兜底没法擦掉，
    看起来像"没处理"。直接从视频画面取帧 → 画面里没有烤字，再叠中文标题，
    观感就正常了。
    """
    if Image is None or not video_path or not os.path.exists(video_path):
        return None
    import shutil
    import subprocess
    import tempfile

    ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    tmpdir = tempfile.mkdtemp(prefix="framecover_")
    best, best_score = None, -1.0
    try:
        for i, frac in enumerate(fracs):
            dst = os.path.join(tmpdir, f"f{i}.jpg")
            cmd = [ff, "-y", "-loglevel", "error", "-ss", f"{frac:.2f}", "-i", video_path,
                   "-frames:v", "1", "-q:v", "3", dst]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except Exception:
                continue
            if r.returncode != 0 or not os.path.exists(dst):
                continue
            try:
                im = Image.open(dst).convert("L")
                small = im.resize((160, 90))
                px = list(small.getdata())
                mean = sum(px) / len(px)
                var = sum((p - mean) ** 2 for p in px) / len(px)
                # 太暗/太亮的帧降权
                if mean < 25 or mean > 232:
                    var *= 0.25
                if var > best_score:
                    best_score, best = var, dst
            except Exception:
                continue
        if best:
            shutil.copyfile(best, out_path)
            return out_path
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def frame_cover(video_path: str, dst: str, aspect: str = "16:9", title: str = ""):
    """从视频取帧 + 叠中文标题，生成一张干净的封面。"""
    tmp = dst + ".frame.jpg"
    f = pick_frame(video_path, tmp)
    if not f:
        return None
    try:
        return compose(f, dst, aspect, title)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def compose(src: str, dst: str, aspect: str = "16:9", title: str = "", max_lines: int = 2):
    """把 src 合成为带标题排版的封面并写入 dst。返回 dst 或 None。"""
    if Image is None or not src or not os.path.exists(src):
        return None
    W, H = SIZES.get(aspect, SIZES["16:9"])

    try:
        im = Image.open(src)
        im = im.convert("RGB")
        scale = max(W / im.width, H / im.height)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        left = max(0, (im.width - W) // 2)
        top = max(0, (im.height - H) // 2)
        im = im.crop((left, top, left + W, top + H))

        title = (title or "").strip()
        if title:
            draw = ImageDraw.Draw(im, "RGBA")
            band = int(H * 0.44)
            for i in range(band):
                alpha = int(210 * ((i + 1) / band) ** 1.5)
                y = H - band + i
                draw.rectangle([0, y, W, y + 1], fill=(0, 0, 0, alpha))

            size = int(H * 0.088) if aspect == "16:9" else int(H * 0.078)
            font = _load_font(size)
            if font is not None:
                lines = _wrap(draw, title, font, int(W * 0.92))
                if len(lines) > max_lines:
                    lines = lines[:max_lines]
                    lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else lines[-1]
                step = size + int(size * 0.34)
                y = H - len(lines) * step - int(H * 0.05)
                for line in lines:
                    try:
                        w = draw.textlength(line, font=font)
                    except Exception:
                        w = len(line) * size
                    x = max(0, (W - w) // 2)
                    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 230))
                    draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
                    y += step

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.save(dst, "JPEG", quality=92)
        return dst
    except Exception:
        return None


def _split_title(title: str):
    """把标题拆成 (标签, 主钩子, 副标题)，用于两级排版。"""
    t = (title or "").strip()
    tag = ""
    m = re.match(r"^\s*【([^】]{1,10})】\s*", t)
    if m:
        tag = m.group(1)
        t = t[m.end():].strip()
    m2 = re.match(r"^(.{2,16}?)[！!？?。]\s*", t)
    if m2:
        hook = m2.group(1).strip()
        rest = t[m2.end():].strip()
    else:
        hook, rest = t, ""
    hook = hook.strip("，,、：:；;")
    if not rest and len(hook) > 15:
        cut = max([hook.rfind(c) for c in "，,、：:）)"] if any(c in hook for c in "，,、：:）)") else [-1])
        if cut > 3:
            hook, rest = hook[:cut], hook[cut + 1:].strip()
    return tag, hook, rest


def bold_cover(src: str, dst: str, aspect: str = "16:9", title: str = "",
               blur: int = 22, max_lines: int = 2):
    """"大字报"式封面 v2：虚化背景 + 标签徽标 + 主钩子(大字) + 副标题(金色小字)。

    为什么需要它（2026-09-14）：
      源缩略图常常烤了英文大字，本地没法擦掉；直接叠标题会出现"英文残留 + 中文压英文"。
      虚化背景 + 两级中文排版 → 观感是"刻意设计过"的封面，且完全本地、必定可用。
    v2 (2026-09-14 下午)：词感知折行（不再把 Edge 断成 Edg/e）+ 主副标题两级 + 标签徽标。
    """
    if Image is None or not src or not os.path.exists(src):
        return None
    from PIL import ImageEnhance, ImageFilter

    W, H = SIZES.get(aspect, SIZES["16:9"])
    try:
        bg = Image.open(src).convert("RGB")
        s = max(W / bg.width, H / bg.height)
        bg = bg.resize((max(1, int(bg.width * s)), max(1, int(bg.height * s))), Image.LANCZOS)
        left = max(0, (bg.width - W) // 2)
        top = max(0, (bg.height - H) // 2)
        bg = bg.crop((left, top, left + W, top + H))
        bg = bg.filter(ImageFilter.GaussianBlur(blur))
        bg = ImageEnhance.Brightness(bg).enhance(0.62)
        bg = ImageEnhance.Color(bg).enhance(0.85)

        draw = ImageDraw.Draw(bg, "RGBA")
        draw.rectangle([0, 0, W, H], fill=(0, 0, 0, 70))
        # 中间竖向聚光，让字更稳
        band = int(H * 0.72)
        for i in range(band):
            a = int(90 * (1 - abs(i - band / 2) / (band / 2)))
            y = int(H * 0.14) + i
            if 0 <= y < H:
                draw.rectangle([0, y, W, y + 1], fill=(0, 0, 0, a))

        tag, hook, rest = _split_title(title)
        if not hook and not tag:
            bg.save(dst, "JPEG", quality=92)
            return dst

        def draw_center(text, font, y, fill, stroke=3):
            try:
                w = draw.textlength(text, font=font)
            except Exception:
                w = len(text) * (font.size or 10)
            x = max(0, int((W - w) / 2))
            draw.text((x + 3, y + 4), text, font=font, fill=(0, 0, 0, 170))
            for dx, dy in ((-stroke, 0), (stroke, 0), (0, -stroke), (0, stroke)):
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 235))
            draw.text((x, y), text, font=font, fill=fill)
            return y

        y = int(H * 0.16)
        # ── 标签徽标（如 UE5 / AI）──
        if tag:
            tf = _load_font(int(H * 0.055))
            if tf is not None:
                label = f" {tag} "
                try:
                    tw = draw.textlength(label, font=tf)
                except Exception:
                    tw = len(label) * int(H * 0.055)
                bx0, by0 = int((W - tw) / 2) - 12, y
                draw.rounded_rectangle([bx0, by0, bx0 + tw + 24, by0 + int(H * 0.085)],
                                       radius=int(H * 0.02), fill=(255, 90, 60, 235))
                draw.text((bx0 + 12, by0 + int(H * 0.008)), label, font=tf, fill=(255, 255, 255, 255))
                y = by0 + int(H * 0.115)

        # ── 主钩子（大字，最多 max_lines 行）──
        size = int(H * 0.145) if aspect == "16:9" else int(H * 0.125)
        while size > int(H * 0.06):
            font = _load_font(size)
            if font is None:
                break
            lines = _wrap(draw, hook, font, int(W * 0.88))
            if len(lines) <= max_lines:
                break
            size = int(size * 0.88)
        font = _load_font(size)
        lines = _wrap(draw, hook, font, int(W * 0.88)) if font is not None else []
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1] + "…"
        step = size + int(size * 0.22)
        for line in lines:
            draw_center(line, font, y, (255, 255, 255, 255), 3)
            y += step

        # ── 副标题（金色小字）──
        if rest:
            y += int(H * 0.02)
            ssize = int(H * 0.062) if aspect == "16:9" else int(H * 0.055)
            sfont = _load_font(ssize)
            if sfont is not None:
                slines = _wrap(draw, rest, sfont, int(W * 0.86))[:2]
                for line in slines:
                    draw_center(line, sfont, y, (255, 214, 92, 255), 2)
                    y += ssize + int(ssize * 0.3)

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        bg.save(dst, "JPEG", quality=92)
        return dst
    except Exception:
        return None
