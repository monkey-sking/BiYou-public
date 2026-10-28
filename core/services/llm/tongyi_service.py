"""Tongyi Wanxiang (通义万相) browser-driven image generation via ego-browser CLI.

Drives tongyi.aliyun.com/wan (万相创作台) with the logged-in 阿里/支付宝 session
in the shared ego browser. Free daily 灵感值 granted by daily check-in (签到).

Cover flow: 图片生成 mode -> upload reference image -> prompt -> Enter ->
generated images appear as cdn.wanx.aliyuncs.com/.../_N_with_two_logo.png
previews. The clean full-res original is the same path with "_with_two_logo"
removed (verified 2730x1536 PNG), but the 「通义万相」 text watermark is baked
into the bottom-right corner pixels — removed via cv2.inpaint in _download.

Credits: 灵感值 shown as the leading number in the header before "探索发现".
Raises CreditsExhausted when below threshold so the cover pipeline can fall back.
"""
import asyncio
import json
import os
import re
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

TONGYI_CREATE_URL = "https://tongyi.aliyun.com/wan/generate/image/generate?model=wan2.7-pro"
CREDIT_PATTERN = re.compile(r"(\d+)\s*探索发现")


class CreditsExhausted(Exception):
    """Raised when tongyi 灵感值 is too low to generate."""


class LoginExpired(Exception):
    """Raised when the tongyi session has expired / not logged in."""


class TongyiService(LLMBase):
    """通义万相 cover generation via the shared ego browser session."""

    provider = "tongyi"

    def __init__(self, service_config: dict = None):
        self.config = load_config()
        self.service_config = service_config or {}
        self.provider = "tongyi"
        self.use_browser = True

        providers = self.config.get("providers", {})
        ty = providers.get("tongyi", {})
        self.min_credits = int(
            self.service_config.get("min_credits")
            or ty.get("min_credits")
            or 3
        )
        self.generate_timeout = float(
            self.service_config.get("generate_timeout_seconds")
            or ty.get("generate_timeout_seconds")
            or 180
        )
        self.task_space = (
            self.service_config.get("task_space") or ty.get("task_space") or "biyou-tongyi"
        )
        # 2026-09-15: 每次调用用唯一 space 名，避免复用残留页面导致
        # reconcilePages/listTabs 崩溃或旧页面状态污染(RESULT=NONE)。
        import uuid
        self.task_space = self.task_space + "-" + uuid.uuid4().hex[:6]
        self.ego_cli = os.path.expanduser(
            self.config.get("ego_cli") or ty.get("ego_cli") or "~/.local/bin/ego-browser"
        )

        # Translation still runs on Gemini (tongyi web has no cheap text API).
        from core.services.llm.gemini_service import GeminiService
        self._text_service = GeminiService({"provider": "gemini_web", "model": "Flash"})

        # Daily check-in marker: once we check in on a given day we don't re-open
        # the check-in panel again until the next day.
        self.checkin_marker = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "data", "tongyi_checkin_date.txt",
        )

    def _needs_checkin(self) -> bool:
        """True if we haven't already checked in today."""
        from datetime import date
        try:
            with open(self.checkin_marker) as fh:
                return fh.read().strip() != date.today().isoformat()
        except FileNotFoundError:
            return True

    def _mark_checkin(self) -> None:
        from datetime import date
        try:
            os.makedirs(os.path.dirname(self.checkin_marker), exist_ok=True)
            with open(self.checkin_marker, "w") as fh:
                fh.write(date.today().isoformat())
        except Exception:
            pass

    async def generate_text(self, prompt: str, model: str = None) -> str:
        return await self._text_service.generate_text(prompt, model=model)

    async def _translate_cover_text(self, client, context_text: str) -> str:
        return await self._text_service._translate_cover_text(client, context_text)

    async def generate_image(
        self,
        prompt: str,
        input_image_path: str,
        output_path: str,
        model: str = None,
    ) -> Optional[str]:
        """Drive tongyi 万相 to edit/generate a cover and save to output_path."""
        if os.path.dirname(output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 2026-08-26 (4th cookie-loss 防护): ego CDP 不可达时绝不调 ego-browser CLI。
        try:
            sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
            from browser_registry import ego_cli_reachable
            if not ego_cli_reachable():
                log.warning("⏱️ ego CDP 不可达，跳过 tongyi 出图（不调 CLI，避免 cookie Reset）")
                return None
        except Exception:
            pass

        input_image = input_image_path if os.path.exists(input_image_path or "") else None
        do_checkin = self._needs_checkin()
        script = self._build_cli_script(prompt, input_image, output_path, do_checkin=do_checkin)
        if do_checkin:
            # We attempted the daily check-in; don't re-attempt until tomorrow.
            self._mark_checkin()

        try:
            proc = await asyncio.create_subprocess_exec(
                self.ego_cli, "nodejs",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(input=script.encode("utf-8")),
                timeout=300,
            )
        except Exception as e:
            log.warning(f"⚠️ ego CLI 执行失败: {e}")
            return None

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")

        result_url = None
        exhausted = False
        login_expired = False
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                line = line.strip()
                if line.startswith("RESULT="):
                    value = line[len("RESULT="):]
                    if value == "EXHAUSTED":
                        exhausted = True
                    elif value == "LOGIN_EXPIRED":
                        login_expired = True
                    elif value.startswith("http"):
                        result_url = value
                elif line.startswith("CREDITS="):
                    log.info(f"💡 万相灵感值: {line[len('CREDITS='):]}")
        if login_expired:
            raise LoginExpired("万相登录已失效，切换到其它服务")
        if exhausted:
            raise CreditsExhausted(
                f"万相灵感值不足 (剩余 {self.min_credits} 以下)，切换到其它服务"
            )

        if not result_url:
            log.warning(f"⚠️ 万相未获取到结果图。CLI stderr: {stderr[-300:]}")
            return None

        return await self._download(result_url, output_path)

    # ------------------------------------------------------------------ #

    def _build_cli_script(self, prompt: str, input_image: Optional[str], output_path: str, do_checkin: bool = True) -> str:
        checkin_js = """// 每日签到: 顶部灵感值/签到入口 (checkInGuidePopover) 点击打开面板, 若未签到则点签到/领取
await js(String.raw`(() => {
  const el = [...document.querySelectorAll('[class*="checkInGuidePopover"],[class*="CreditWrapper"]')].find(e => e.offsetParent !== null)
  if (el) { el.click(); return true }
  return false
})()`)
await wait(1.5)
await js(String.raw`(() => {
  const b = [...document.querySelectorAll('button,div,span')].find(e => e.offsetParent !== null && /(签到|立即签到|领取灵感|签到领|去签到)/.test(e.innerText || '') && !/(今日已签到)/.test(e.innerText || '') && (e.innerText || '').trim().length <= 14 && e.getBoundingClientRect().width >= 40)
  if (b) { b.click(); return true }
  return false
})()`)
await wait(2)
await js(String.raw`(() => {
  const b = [...document.querySelectorAll('button')].find(e => e.offsetParent !== null && (e.getAttribute('aria-label') === '关闭' || e.getAttribute('aria-label') === 'Close' || /(知道了|好的|确认)/.test(e.innerText || '')))
  if (b) { b.click(); return true }
  return false
})()`)
await wait(1)
"""
        if not do_checkin:
            checkin_js = ""
        return f"""const task = await taskSpace({json.dumps(self.task_space)})
const page = await task.newPage()
try {{
await page.goto({json.dumps(TONGYI_CREATE_URL)}, {{ waitUntil: "domcontentloaded", timeout: 30000 }})
await wait(5)
const info = await pageInfo()
if (!info.url.includes('/wan/generate/image')) {{
  await page.goto({json.dumps(TONGYI_CREATE_URL)}, {{ waitUntil: "domcontentloaded", timeout: 30000 }})
  await wait(5)
}}
const bodyText = await js(String.raw`document.body.innerText`)
{checkin_js}
const cm = (await js(String.raw`document.body.innerText`)).match(/(\\d+)\\s*探索发现/)
const credits = cm ? parseInt(cm[1]) : 0
cliLog('CREDITS=' + credits)
const hasLoginBtn = await js(String.raw`(() => {{
  return !![...document.querySelectorAll('button,span,div,a')].find(x => x.offsetParent !== null && (x.innerText || '').trim() === '立即登录' && x.children.length === 0)
}})()`)
const MIN_CREDITS = {int(self.min_credits)}
if (hasLoginBtn) {{
  cliLog('RESULT=LOGIN_EXPIRED')
}} else if (credits < MIN_CREDITS) {{
  cliLog('RESULT=EXHAUSTED')
}} else {{
  const INPUT_IMAGE = {json.dumps(input_image)}
  if (INPUT_IMAGE) {{
    const boxRect = await js(String.raw`(() => {{
      const box = [...document.querySelectorAll('div')].find(d => d.offsetParent !== null && d.innerText && d.innerText.trim() === '图片' && d.getBoundingClientRect().width < 100 && d.getBoundingClientRect().height >= 30)
      if (!box) return null
      const r = box.getBoundingClientRect()
      return {{ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }}
    }})()`)
    if (boxRect) {{
      await click([boxRect.x, boxRect.y], {{ label: 'open image box' }})
      await wait(1)
      await hover([boxRect.x, boxRect.y], {{ label: 'hover image box' }})
      await wait(1.5)
    }}
    await js(String.raw`(() => {{
      const el = [...document.querySelectorAll('div,span,li')].find(e => e.offsetParent !== null && e.innerText && e.innerText.trim() === '本地上传')
      if (el) {{ el.click(); return true }}
      return false
    }})()`)
    let hasInput = false
    for (let i = 0; i < 5; i++) {{
      hasInput = await js(String.raw`!!document.querySelector('input[type="file"]')`)
      if (hasInput) break
      await wait(1)
    }}
    if (hasInput) {{
      await uploadFile('input[type="file"]', INPUT_IMAGE)
      await wait(4)
    }}
  }}
  const focused = await js(String.raw`(() => {{
    const els = [...document.querySelectorAll('.ProseMirror,[contenteditable="true"]')].filter(e => e.offsetParent !== null)
    const main = els.find(e => e.getBoundingClientRect().y > 400) || els[0]
    if (!main) return false
    main.focus()
    return true
  }})()`)
  if (!focused) {{ cliLog('RESULT=NONE') }} else {{
    const PROMPT = {json.dumps(prompt)}
    // ── 陈旧图防护 (2026-09-01): 生成前移除页面已残留的万相结果图节点。
    //    openOrReuseTab 可能复用了上一个视频的残留页面；若不清除，轮询会
    //    立刻抓到上张图(sim 校验大概率被 0 阈值放行)导致封面张冠李戴。 ──
    await js(String.raw`(() => {{
      let n = 0
      ;[...document.querySelectorAll('img')].forEach(i => {{
        if (/cdn\\.wanx\\.aliyuncs\\.com\\/wanx/.test(i.src)) {{ i.remove(); n++ }}
      }})
      return n
    }})()`)
    // 输入框残留清空 (2026-09-15): typeText 是追加输入，残留上一 prompt 会拼出混乱文本
    // 导致生成失败(RESULT=NONE)。生成前清空 contenteditable 输入框。
    await js(String.raw`(() => {{
      const els = [...document.querySelectorAll('.ProseMirror,[contenteditable="true"]')].filter(e => e.offsetParent !== null)
      const main = els.find(e => e.getBoundingClientRect().y > 400) || els[0]
      if (!main) return
      main.focus()
      if (main.textContent) main.textContent = ''
      if (main.innerText) main.innerText = ''
    }})()`)
    await typeText(PROMPT, {{ delay: 3 }})
    await pressKey('Enter')
    const timeout = {float(self.generate_timeout)}
    const deadline = Date.now() + timeout * 1000
    let result = null
    while (Date.now() < deadline) {{
      try {{
        const u = await js(String.raw`(() => {{
          const imgs = [...document.querySelectorAll('img')].map(i => i.src)
          const hit = imgs.find(s => /cdn\\.wanx\\.aliyuncs\\.com\\/wanx/.test(s) && /_with_two_logo\\.png/.test(s))
          if (!hit) return null
          return hit.replace(/_with_two_logo\\.png.*$/, '.png').replace(/\\?x-oss-process.*$/, '')
        }})()`)
        if (u) {{ result = u; break }}
      }} catch (e) {{}}
      await wait(5)
    }}
    cliLog('RESULT=' + (result || 'NONE'))
  }}
}}
}} finally {{
  // 2026-09-16 用户投诉: 万相模板缺收尾 → 每次生成漏一个标签/空间（实测 390/392）。
  await task.finish({{ keep: [] }})
}}
"""

    async def _download(self, url: str, output_path: str) -> str:
        import httpx
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            with open(output_path, "wb") as fh:
                fh.write(resp.content)
        self._remove_wanxiang_watermark(output_path)
        log.info(f"✅ 万相封面已保存: {output_path}")
        return output_path

    def _remove_wanxiang_watermark(self, path: str) -> None:
        """Inpaint the baked-in「通义万相」text watermark (bottom-right corner).

        Preferred method: IOPaint LaMa (much better texture reconstruction).
        Falls back to cv2.inpaint if the IOPaint venv isn't available.

        Verified watermark bbox at 2730x1536 is x=2396-2719, y=1449-1525
        (identical across all generated images). The mask scales with image size;
        if the fixed bbox is out of bounds we fall back to gradient-cluster
        detection in the bottom-right ROI.
        """
        try:
            import numpy as np
            from PIL import Image

            img = Image.open(path).convert("RGB")
            w, h = img.size
            arr = np.asarray(img, dtype=np.uint8)

            bbox = self._locate_wanxiang_watermark(arr, w, h)
            if bbox is None:
                return

            x1, y1, x2, y2 = bbox
            pad_x, pad_y = max(8, int((x2 - x1) * 0.12)), max(6, int((y2 - y1) * 0.2))
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            x2, y2 = min(w - 1, x2 + pad_x), min(h - 1, y2 + pad_y)

            # 1) IOPaint LaMa (preferred)
            if self._try_lama_inpaint(path, (x1, y1, x2, y2)):
                log.info(f"💧 万相右下角水印已抹除 (LaMa) bbox=({x1},{y1},{x2},{y2})")
                return

            # 2) cv2.inpaint fallback
            try:
                import cv2
                mask = np.zeros((h, w), dtype=np.uint8)
                mask[y1:y2, x1:x2] = 255
                cleaned = cv2.inpaint(arr, mask, inpaintRadius=6, flags=cv2.INPAINT_TELEA)
                Image.fromarray(cleaned).convert(img.mode).save(path)
                log.info(f"💧 万相右下角水印已抹除 (cv2) bbox=({x1},{y1},{x2},{y2})")
            except Exception as e:
                log.warning(f"⚠️ 万相水印抹除失败: {e}")
        except Exception as e:
            log.warning(f"⚠️ 万相水印抹除失败: {e}")

    @staticmethod
    def _try_lama_inpaint(path: str, bbox) -> bool:
        """Run IOPaint LaMa on the watermark region. Returns True on success."""
        try:
            from core.services.watermark.iopaint_runner import inpaint, venv_ready
            if not venv_ready():
                return False
            import numpy as np
            from PIL import Image
            img = Image.open(path).convert("RGB")
            w, h = img.size
            mask = np.zeros((h, w), dtype=np.uint8)
            x1, y1, x2, y2 = bbox
            mask[y1:y2, x1:x2] = 255
            import os, tempfile
            td = tempfile.mkdtemp(prefix="wm_")
            mpath = os.path.join(td, "mask.png")
            Image.fromarray(mask).save(mpath)
            out = os.path.join(td, "out.png")
            ok = inpaint(path, mpath, out)
            if ok and os.path.isfile(out):
                import shutil
                shutil.move(out, path)
            import shutil as _s
            _s.rmtree(td, ignore_errors=True)
            return ok
        except Exception:
            return False

    @staticmethod
    def _locate_wanxiang_watermark(arr, w: int, h: int):
        try:
            import cv2
            import numpy as np

            fx, fy = w / 2730.0, h / 1536.0
            fixed = (int(2396 * fx), int(1449 * fy), int(2719 * fx), int(1525 * fy))
            if fixed[0] >= 0 and fixed[2] < w and fixed[1] >= 0 and fixed[3] < h:
                return fixed

            roi = arr[int(h * 0.92):, int(w * 0.70):]
            gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 40, 100)
            gh, gw = roi.shape[:2]
            region = edges[int(gh * 0.30):, :]
            ys, xs = np.where(region)
            if not len(xs):
                return None
            ax0, ay0 = int(w * 0.70), int(h * 0.92) + int(gh * 0.30)
            return (ax0 + int(xs.min()), ay0 + int(ys.min()),
                    ax0 + int(xs.max()), ay0 + int(ys.max()))
        except Exception:
            return None