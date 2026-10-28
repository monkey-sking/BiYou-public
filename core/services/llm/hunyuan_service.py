"""Hunyuan (腾讯混元) browser-driven image generation via ego-browser CLI.

Drives aistudio.tencent.com/chat/HunyuanDefault?modelId=Hunyuan-Image-3.0-Instruct
with the logged-in 微信/QQ session in the shared ego browser. Chat-style image
edit: upload reference image -> type prompt -> Enter -> result COS URLs appear
directly in the DOM (no download-capture needed).

Login state: a visible "登录" button in the header means the session expired.
Raises LoginExpired so the cover pipeline can fall back.
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

HUNYUAN_CHAT_URL = (
    "https://aistudio.tencent.com/chat/HunyuanDefault?modelId=Hunyuan-Image-3.0-Instruct"
)
RESULT_IMAGE_RE = re.compile(
    r"https://hy-model-ap-prod-1258344703\.cos\.ap-guangzhou\.myqcloud\.com/modelapp/"
    r"default/\d+/[^\s\"']+\.(?:jpg|jpeg|png|webp)"
)


class LoginExpired(Exception):
    """Raised when the provider session has expired / not logged in."""


class HunyuanService(LLMBase):
    """腾讯混元 cover generation via the shared ego browser session."""

    provider = "hunyuan"

    def __init__(self, service_config: dict = None):
        self.config = load_config()
        self.service_config = service_config or {}
        self.provider = "hunyuan"
        self.use_browser = True

        providers = self.config.get("providers", {})
        hy = providers.get("hunyuan", {})
        self.generate_timeout = float(
            self.service_config.get("generate_timeout_seconds")
            or hy.get("generate_timeout_seconds")
            or 180
        )
        self.task_space = (
            self.service_config.get("task_space") or hy.get("task_space") or "biyou-hunyuan"
        )
        self.ego_cli = os.path.expanduser(
            self.config.get("ego_cli") or hy.get("ego_cli") or "~/.local/bin/ego-browser"
        )

        # Translation still runs on Gemini.
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
        """Drive 混元 image-edit chat to produce a cover and save to output_path."""
        if os.path.dirname(output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 2026-08-26 (4th cookie-loss 防护): ego CDP 不可达时绝不调 ego-browser CLI。
        try:
            sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
            from browser_registry import ego_cli_reachable
            if not ego_cli_reachable():
                log.warning("⏱️ ego CDP 不可达，跳过 hunyuan 出图（不调 CLI，避免 cookie Reset）")
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
                timeout=300,
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
            raise LoginExpired("混元登录已失效，切换到其它服务")

        if not result_url:
            log.warning(f"⚠️ 混元未获取到结果图。CLI stderr: {stderr[-300:]}")
            return None

        return await self._download(result_url, output_path)

    # ------------------------------------------------------------------ #

    def _build_cli_script(self, prompt: str, input_image: Optional[str], output_path: str) -> str:
        return f"""const task = await useOrCreateTaskSpace({json.dumps(self.task_space)})
await openOrReuseTab({json.dumps(HUNYUAN_CHAT_URL)}, {{ wait: true, timeout: 40 }})
await wait(3)
const body0 = await js(String.raw`document.body.innerText`)
const hasLogin = await js(String.raw`(() => {{
  const b = [...document.querySelectorAll('button,span,div,a')].find(x => x.offsetParent !== null && (x.innerText || '').trim() === '登录' && x.children.length === 0)
  return !!b
}})()`)
if (hasLogin) {{
  cliLog('RESULT=LOGIN_EXPIRED')
}} else {{
  const INPUT_IMAGE = {json.dumps(input_image)}
  if (INPUT_IMAGE) {{
    const hasInput = await js(String.raw`!!document.querySelector('input[type="file"][accept*=".jpg"]')`)
    if (hasInput) {{
      await uploadFile('input[type="file"][accept*=".jpg"]', INPUT_IMAGE)
      await wait(3)
    }}
  }}
  const focused = await js(String.raw`(() => {{
    const els = [...document.querySelectorAll('textarea,[contenteditable="true"]')].filter(e => e.offsetParent !== null)
    const main = els.find(e => e.getBoundingClientRect().y > 400) || els[0]
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
          const hit = imgs.find(s => /hy-model-ap-prod-1258344703\\.cos\\.ap-guangzhou\\.myqcloud\\.com\\/modelapp/.test(s) && /\\.(jpg|jpeg|png|webp)/.test(s))
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
        log.info(f"✅ 混元封面已保存: {output_path}")
        return output_path