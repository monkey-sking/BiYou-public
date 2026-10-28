"""Doubao (豆包/Seedream) browser-driven image generation via ego-browser CLI.

Drives doubao.com/chat with the logged-in 飞书/抖音 session in the shared ego
browser. Chat-style image edit: upload reference image -> type prompt -> Enter
-> Seedream generates the edited image.

Result: imagex `rc_gen_image` URLs with a `image_pre_watermark` template render
at 1920x1080. Full-image OCR and corner-blob detection found no visible
watermark on the 1920x1080 output, so we use that URL directly.

Login state: a visible "登录" button means the session expired. Raises
LoginExpired so the cover pipeline can fall back.
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

DOUBAO_CHAT_URL = "https://www.doubao.com/chat/"
RESULT_IMAGE_RE = re.compile(r"https://[^\"'\s]+rc_gen_image/[^\"'\s]+")

LOGIN_WORD = "登录"


class LoginExpired(Exception):
    """Raised when the doubao session has expired / not logged in."""


class DoubaoService(LLMBase):
    """豆包(Seedream) cover generation via the shared ego browser session."""

    provider = "doubao"

    def __init__(self, service_config: dict = None):
        self.config = load_config()
        self.service_config = service_config or {}
        self.provider = "doubao"
        self.use_browser = True

        providers = self.config.get("providers", {})
        cfg = providers.get("doubao", {})
        self.generate_timeout = float(
            self.service_config.get("generate_timeout_seconds")
            or cfg.get("generate_timeout_seconds")
            or 180
        )
        self.task_space = (
            self.service_config.get("task_space")
            or cfg.get("task_space")
            or "biyou-doubao"
        )
        self.ego_cli = os.path.expanduser(
            self.config.get("ego_cli") or cfg.get("ego_cli") or "~/.local/bin/ego-browser"
        )

        from core.services.llm.gemini_service import GeminiService
        self._text_service = GeminiService({"provider": "gemini_web", "model": "Flash"})

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
        if os.path.dirname(output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 2026-08-26 (4th cookie-loss 防护): ego CDP 不可达时绝不调 ego-browser CLI。
        try:
            sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
            from browser_registry import ego_cli_reachable
            if not ego_cli_reachable():
                log.warning("⏱️ ego CDP 不可达，跳过 doubao 出图（不调 CLI，避免 cookie Reset）")
                return None
        except Exception:
            pass

        input_image = input_image_path if os.path.exists(input_image_path or "") else None
        script = self._build_cli_script(prompt, input_image, output_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                self.ego_cli, "nodejs",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(input=script.encode("utf-8")),
                timeout=360,
            )
        except Exception as e:
            log.warning(f"⚠️ ego CLI 执行失败: {e}")
            return None

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")

        result_url = None
        login_expired = False
        for stream in (stdout, stderr):
            for line in stream.splitlines():
                line = line.strip()
                if line.startswith("RESULT="):
                    value = line[len("RESULT="):]
                    if value == "LOGIN_EXPIRED":
                        login_expired = True
                    elif value.startswith("http"):
                        result_url = value
        if login_expired:
            raise LoginExpired("豆包登录已失效，切换到其它服务")

        if not result_url:
            log.warning(f"⚠️ 豆包未获取到结果图。CLI stderr: {stderr[-300:]}")
            return None

        return await self._download(result_url, output_path)

    # ------------------------------------------------------------------ #

    def _build_cli_script(self, prompt: str, input_image: Optional[str], output_path: str) -> str:
        return f"""const task = await useOrCreateTaskSpace({json.dumps(self.task_space)})
await openOrReuseTab({json.dumps(DOUBAO_CHAT_URL)}, {{ wait: true, timeout: 45 }})
await wait(3)
const _info = await pageInfo()
if (/\\/chat\\/\\d+/.test(_info.url)) {{
  await gotoUrl({json.dumps(DOUBAO_CHAT_URL)})
  await wait(3)
}}
const hasLogin = await js(String.raw`(() => {{
  const b = [...document.querySelectorAll('button,span,div,a')].find(x => x.offsetParent !== null && (x.innerText || '').trim() === '登录' && x.children.length === 0)
  return !!b
}})()`)
if (hasLogin) {{
  cliLog('RESULT=LOGIN_EXPIRED')
}} else {{
  const INPUT_IMAGE = {json.dumps(input_image)}
  if (INPUT_IMAGE) {{
    const plus = await js(String.raw`(() => {{
      const b = [...document.querySelectorAll('button')].find(x => x.offsetParent !== null && x.getBoundingClientRect().width <= 40 && x.getBoundingClientRect().height <= 40 && x.getBoundingClientRect().x > 280 && x.getBoundingClientRect().x < 420 && x.getBoundingClientRect().y > 560)
      if (!b) return null
      const r = b.getBoundingClientRect()
      return {{ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }}
    }})()`)
    if (plus) {{
      await click([plus.x, plus.y], {{ label: 'open upload menu' }})
      await wait(2)
    }}
    const upItem = await js(String.raw`(() => {{
      const el = [...document.querySelectorAll('div,span,li')].find(x => x.offsetParent !== null && (x.innerText || '').trim() === '上传文件或图片')
      if (!el) return null
      const r = el.getBoundingClientRect()
      return {{ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }}
    }})()`)
    if (upItem) {{
      await click([upItem.x, upItem.y], {{ label: '上传文件或图片' }})
      await wait(2)
    }}
    const hasInput = await js(String.raw`!!document.querySelector('input[type="file"]')`)
    if (hasInput) {{
      await uploadFile('input[type="file"]', INPUT_IMAGE)
      await wait(4)
    }}
  }}
  const focused = await js(String.raw`(() => {{
    const els = [...document.querySelectorAll('[contenteditable="true"]')].filter(e => e.offsetParent !== null)
    const main = els.sort((a,b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height)[0] || els[0]
    if (!main) return false
    main.focus()
    return true
  }})()`)
  if (!focused) {{ cliLog('RESULT=NONE') }} else {{
    const PROMPT = {json.dumps(prompt)}
    await typeText(PROMPT, {{ delay: 3 }})
    await pressKey('Enter')
    const timeout = {float(self.generate_timeout)}
    const deadline = Date.now() + timeout * 1000
    let result = null
    while (Date.now() < deadline) {{
      try {{
        const u = await js(String.raw`(() => {{
          const imgs = [...document.querySelectorAll('img')].map(i => i.src)
          const hit = imgs.find(s => /rc_gen_image/.test(s) && /image_pre_watermark/.test(s))
          return hit || null
        }})()`)
        if (u) {{ result = u; break }}
      }} catch (e) {{}}
      await wait(5)
    }}
    cliLog('RESULT=' + (result || 'NONE'))
  }}
}}
"""

    async def _download(self, url: str, output_path: str) -> str:
        import httpx
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            with open(output_path, "wb") as fh:
                fh.write(resp.content)
        log.info(f"✅ 豆包封面已保存: {output_path}")
        return output_path