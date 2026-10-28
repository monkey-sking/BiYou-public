"""ChatGPT 网页版出图（ego CLI 原生通道）— 2026-09-18 新增

为什么要有它：
  实测（2026-09-18）ChatGPT 免费版对「保留构图 + 叠加中文标题」这类封面编辑，
  出图质量和中文正确率明显好于 即梦 / gemini_cli ——
  生成的中文标题笔画完整、无乱码，且会自动把文字放进画面空白区。

  本机没有 chatgpt CLI，所以照 gemini_cli_service 的做法，用 **ego-browser CLI 原生通道**
  驱动 chatgpt.com（不碰 CDP 端口、不额外启动浏览器实例）。

登录：复用 ego profile 里的 ChatGPT 登录态（当前 = 蔡金昌 / 免费版）。
      免费版有每日出图额度上限，耗尽时页面对话会给出提示 → 抛 CreditsExhausted。

已验证流程（2026-09-18）：
  1) page.goto("https://chatgpt.com/")
  2) page.setInputFiles("input[type=file][accept='image/*'] >> nth=0", [源图])
  3) page.fill("#prompt-textarea", prompt) → keyboard.press("Enter")
  4) 轮询页面内新增的 <img src=.../backend-api/estuary/content...>（naturalWidth>300）
  5) 页面内 fetch(url, {credentials:"include"}) 取字节 → 写文件
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

project_root = str(Path(__file__).parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.services.llm.base import LLMBase
from core.utils.config import load_config
from core.utils.logger import log


class ChatgptCliError(Exception):
    pass


class LoginExpired(Exception):
    """ChatGPT 登录失效。"""


class CreditsExhausted(Exception):
    """当日出图额度用尽。"""


class ChatgptCliService(LLMBase):
    """用 ego CLI 驱动 chatgpt.com 生成/编辑图片。"""

    provider = "chatgpt_cli"
    CHATGPT_URL = "https://chatgpt.com/"

    def __init__(self, service_config: dict = None):
        self.config = load_config()
        self.service_config = service_config or {}
        providers = self.config.get("providers", {})
        c = providers.get("chatgpt_cli", {}) or {}
        self.generate_timeout = float(
            self.service_config.get("generate_timeout_seconds")
            or c.get("generate_timeout_seconds")
            or 300
        )
        # 唯一化 task_space：复用同一 space 反复 newPage 会触发 ego reconcilePages 崩溃
        base = self.service_config.get("task_space") or c.get("task_space") or "biyou-chatgpt"
        self.task_space = base + "-" + uuid.uuid4().hex[:6]
        self.ego_cli = os.path.expanduser(
            self.config.get("ego_cli") or c.get("ego_cli") or "~/.local/bin/ego-browser"
        )
        # 文本能力复用 Gemini（与 gemini_cli_service 一致，供 image_translator 调翻译词表）
        from core.services.llm.gemini_service import GeminiService

        self._text_service = GeminiService({"provider": "gemini_web", "model": "Flash"})

    async def generate_text(self, prompt: str, model: str = None) -> str:
        return await self._text_service.generate_text(prompt, model=model)

    async def _translate_cover_text(self, client, context_text: str) -> str:
        return await self._text_service._translate_cover_text(client, context_text)

    # ---------------------------------------------------------------- image

    async def generate_image(
        self,
        prompt: str,
        input_image_path: str,
        output_path: str,
        model: str = None,
    ) -> Optional[str]:
        if os.path.dirname(output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        input_image = input_image_path if os.path.exists(input_image_path or "") else None
        script = self._build_cli_script(prompt, input_image, output_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                self.ego_cli, "nodejs",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(input=script.encode("utf-8")),
                timeout=self.generate_timeout + 150,
            )
        except Exception as e:
            log.warning(f"⚠️ chatgpt_cli: ego CLI 执行失败: {e}")
            return None

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        login_expired = quota = False
        skipped = ""

        for stream in (stdout, stderr):
            for line in stream.splitlines():
                line = line.strip()
                if line.startswith("RESULT="):
                    v = line[len("RESULT="):]
                    if v == "LOGIN_EXPIRED":
                        login_expired = True
                    elif v == "QUOTA":
                        quota = True
                    elif v == "NO_IMAGE":
                        skipped = "未等到生成图"
                elif line.startswith("UPLOAD="):
                    log.info(f"📎 chatgpt_cli 上传: {line[len('UPLOAD='):]}")
                elif line.startswith("SENT="):
                    log.info(f"🧭 chatgpt_cli 发送: {line[len('SENT='):]}")
                elif line.startswith("IMG_URL="):
                    log.info(f"🧭 chatgpt_cli 生成图: {line[len('IMG_URL='):][:110]}")
                elif line.startswith("SAVED="):
                    log.info(f"✅ chatgpt_cli 已保存: {line[len('SAVED='):]}")
                elif line.startswith("T="):
                    log.info(f"⏱️ chatgpt_cli 耗时: {line[2:]}")
                elif line.startswith("DIAG="):
                    log.info(f"🧭 chatgpt_cli 诊断: {line[len('DIAG='):]}")

        if login_expired:
            raise LoginExpired("ChatGPT 登录已失效")
        if quota:
            raise CreditsExhausted("ChatGPT 当日出图额度已用尽")

        if not (os.path.exists(output_path) and os.path.getsize(output_path) > 20000):
            log.warning(f"⚠️ chatgpt_cli 未产出有效图: {skipped or 'unknown'}")
            return None

        # 同源校验：与源图相关度太低 = 跑偏；几乎一致 = 抓到参考图回声
        try:
            if input_image:
                from core.services.image_translator import cover_similarity

                sim = cover_similarity(output_path, input_image)
                log.info(f"🧭 chatgpt_cli 结果与源图相关度: {sim:.2f}")
                if sim > 0.97:
                    log.warning("⚠️ chatgpt_cli 结果与源图几乎一致（疑似参考图回声），判为失败")
                    return None
        except Exception:
            pass

        from PIL import Image

        with Image.open(output_path) as im:
            log.info(f"✅ ChatGPT(CLI) 封面已保存: {output_path} {im.width}x{im.height}")
        return output_path

    # ---------------------------------------------------------------- script

    def _build_cli_script(self, prompt: str, input_image: Optional[str], output_path: str) -> str:
        p = json.dumps(prompt, ensure_ascii=False)
        src = json.dumps(input_image) if input_image else "null"
        out = json.dumps(output_path)
        ts = json.dumps(self.task_space)
        url = json.dumps(self.CHATGPT_URL)
        return f"""
const fs = await import("node:fs/promises");
const PROMPT = {p};
const SRC = {src};
const OUT = {out};
const T0 = Date.now();
const task = await taskSpace({ts});
try {{
  const page = task.page("p1");
  await page.goto({url});
  await page.waitForLoadState();
  await page.waitForTimeout(9000);

  if (/\\/auth\\/|\\/login|auth0|signup/i.test(page.url ? page.url() : "")) {{
    console.log("RESULT=LOGIN_EXPIRED"); await task.finish({{keep:[]}}); process.exit(0);
  }}
  // 2026-09-19: ChatGPT 改版 —— #prompt-textarea 已不存在（实测 0 个，真实输入框是 contenteditable div）
  // 旧代码拿它当登录判据 → 每次误报 LOGIN_EXPIRED。改为宽选择器。
  const hasComposer = await page.evaluate(() => !!document.querySelector("#prompt-textarea, [data-testid='prompt-textarea'], [contenteditable='true'], textarea"));
  if (!hasComposer) {{ console.log("RESULT=LOGIN_EXPIRED"); await task.finish({{keep:[]}}); process.exit(0); }}

  const before = await page.evaluate(() =>
    [...document.querySelectorAll("img")].filter(i => i.naturalWidth > 300).map(i => i.src));

  if (SRC) {{
    try {{
      await page.setInputFiles("input[type=file][accept='image/*'] >> nth=0", [SRC]);
      console.log("UPLOAD=ok");
      await page.waitForTimeout(6000);
    }} catch (e) {{ console.log("UPLOAD=fail " + String(e).slice(0,80)); }}
  }}

  // 2026-09-19: 旧 page.fill("#prompt-textarea") 随改版失效 → 点击聚焦 + 键盘输入（同样用宽选择器）
  try {{
    const box = await page.evaluate(() => {{
      const el = document.querySelector("#prompt-textarea, [data-testid='prompt-textarea'], [contenteditable='true'], textarea");
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {{ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }};
    }});
    if (box) {{ await page.mouse.click(box.x, box.y); await page.waitForTimeout(700); }}
  }} catch (e) {{ console.log("FOCUS_ERR=" + String(e).slice(0, 80)); }}
  await page.keyboard.type(PROMPT, {{ delay: 3 }});
  await page.waitForTimeout(1500);
  await page.keyboard.press("Enter");
  console.log("SENT=ok");

  let found = null;
  const MAXI = 20;
  // 2026-09-26 修复: 原 `if (i >= 8) break;` 硬上限 = 最多等 120s，
  // 但 ChatGPT 出图实测常需 2-5 分钟 → 图还没生成完就放弃，
  // 表现为大量 "no result path"/"未等到生成图"（两天 84 次）。
  // 现改为按外层 generate_timeout(300s)+ 余量等待：20 次 × 15s = 300s。
  const HARD_STOP = 20;
  // 2026-09-23 修：before 在「上传参考图预览」之前抓取 → 预览是 after-before 的新图，
  // 会在 i=1 被当成生成结果抓到（回声）。新逻辑：上传后立即记 initial（含预览），
  // 只在 drafting=false 后抓取「不在 initial 里的新图」。
  const initial = await page.evaluate(() =>
    [...document.querySelectorAll("img")].filter(i => i.naturalWidth > 300).map(i => i.src));
  for (let i = 1; i <= MAXI; i++) {{
    await page.waitForTimeout(15000);
    const st = await page.evaluate((pre) => {{
      const imgs = [...document.querySelectorAll("img")].filter(
        (im) => im.naturalWidth > 300 && /estuary\\/content|backend-api/.test(im.src));
      const txt = (document.body.innerText || "").replace(/\\s+/g, " ");
      const fresh = imgs.filter(im => !pre.includes(im.src));
      return {{ n: imgs.length, freshN: fresh.length,
               src: fresh.length ? fresh[fresh.length - 1].src : null,
               drafting: /正在打草稿|正在生成|generating|Creating image/i.test(txt),
               tail: txt.slice(-200) }};
    }}, initial);
    console.log("DIAG=" + JSON.stringify({{i, n: st.n, freshN: st.freshN, drafting: st.drafting}}));
    // 只在生成完成、且出现「上传后新增」的图时取它
    if (!st.drafting && st.freshN > 0) {{ found = st.src; break; }}
    // 2026-09-26 修复: 实测 DIAG 出现 n=1 但 freshN=0（生成图 URL 与上传预览
    // 同源/被 initial 收录）→ 旧逻辑永远取不到，图明明生成也判 NO_IMAGE。
    // 兜底：非 drafting 且有图(n>0) 且已等足够轮次(i>=6, 90s)，取最后一张。
    if (!st.drafting && st.n > 0 && i >= 6) {{
      const last = await page.evaluate(() => {{
        const imgs = [...document.querySelectorAll("img")].filter(
          (im) => im.naturalWidth > 300 && /estuary\\/content|backend-api/.test(im.src));
        return imgs.length ? imgs[imgs.length - 1].src : null;
      }});
      if (last) {{ found = last; break; }}
    }}
    if (i >= HARD_STOP) break;   // 硬上限：300s（2026-09-26 从 120s 放宽）
    if (/达到上限|额度|limit reached|too many|升级到|quota/i.test(st.tail)) {{ console.log("RESULT=QUOTA"); await task.finish({{keep:[]}}); process.exit(0); }}
  }}

  if (!found) {{ console.log("RESULT=NO_IMAGE"); await task.finish({{keep:[]}}); process.exit(0); }}
  console.log("IMG_URL=" + found.slice(0, 110));

  const b64 = await page.evaluate(async (u) => {{
    const r = await fetch(u, {{ credentials: "include" }});
    if (!r.ok) return "";
    const buf = await r.arrayBuffer();
    const arr = new Uint8Array(buf);
    let bin = "";
    const CH = 32768;
    for (let i = 0; i < arr.length; i += CH) bin += String.fromCharCode.apply(null, arr.subarray(i, i + CH));
    return btoa(bin);
  }}, found);

  if (!b64) {{ console.log("RESULT=NO_IMAGE"); }}
  else {{
    await fs.writeFile(OUT, Buffer.from(b64, "base64"));
    console.log("SAVED=" + OUT);
    console.log("RESULT=ok");
  }}
  console.log("T=" + ((Date.now() - T0) / 1000).toFixed(1) + "s");
}} catch (e) {{
  console.log("RESULT=ERR " + String(e).slice(0, 160));
}} finally {{
  await task.finish({{keep: []}});
}}
"""
