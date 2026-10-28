"""Jimeng (即梦 AI) browser-driven image generation via the ego-browser CLI.

Uses the logged-in 抖音/字节 session in the shared ego browser (native
ego-browser channel, same as launch_browser.sh). No official API — this
drives the web UI directly, which is the only working path since
reverse-engineered clients (jimeng-api) currently break with "login expired".

Credits: daily free 积分 are shown in the sidebar as "<N> 开会员".
When credits drop below a threshold the service raises CreditsExhausted so
the cover pipeline can fall back to another provider.
"""
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

JIMENG_CREATE_URL = "https://jimeng.jianying.com/ai-tool/generate"


class CreditsExhausted(Exception):
    """Raised when jimeng daily credits are too low to generate."""


class LoginExpired(Exception):
    """Raised when the jimeng session has expired / not logged in."""


class JimengService(LLMBase):
    """即梦 AI cover generation via the shared ego browser session."""

    provider = "jimeng"

    def __init__(self, service_config: dict = None):
        self.config = load_config()
        self.service_config = service_config or {}
        self.provider = "jimeng"
        self.use_browser = True

        providers = self.config.get("providers", {})
        jm = providers.get("jimeng", {})
        self.min_credits = int(
            self.service_config.get("min_credits")
            or jm.get("min_credits")
            or 5
        )
        self.generate_timeout = float(
            self.service_config.get("generate_timeout_seconds")
            or jm.get("generate_timeout_seconds")
            or 240
        )
        self.task_space = (
            self.service_config.get("task_space") or jm.get("task_space") or "biyou-jimeng"
        )
        self.ego_cli = os.path.expanduser(
            self.config.get("ego_cli") or jm.get("ego_cli") or "~/.local/bin/ego-browser"
        )

        # Translation still runs on Gemini (jimeng has no cheap text API).
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
        """Drive jimeng web UI to edit/generate a cover and save to output_path."""
        if os.path.dirname(output_path):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 2026-08-26 (4th cookie-loss 防护): ego CDP 不可达时绝不调 ego-browser CLI。
        # 进程在跑 != 服务可用；CDP 端口未监听时 CLI 会拉起短命实例污染 cookie 库 → Reset。
        try:
            sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
            from browser_registry import ego_cli_reachable
            if not ego_cli_reachable():
                log.warning("⏱️ ego CDP 不可达，跳过 jimeng 出图（不调 CLI，避免 cookie Reset）")
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
        shot_file = None
        clip = None
        shot_chunks = {}
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
                    elif value.startswith("file:"):
                        shot_file = value[len("file:"):]
                    elif value.startswith("http"):
                        result_url = value
                elif line.startswith("SHOTB64_") and ":" in line:
                    try:
                        head, data = line[len("SHOTB64_"):].split(":", 1)
                        idx = int(head.split("/")[0])
                        shot_chunks[idx] = data
                    except Exception:
                        pass
                elif line.startswith("CLIP="):
                    try:
                        clip = json.loads(line[len("CLIP="):])
                    except Exception:
                        clip = None
                elif line.startswith("UPLOAD="):
                    log.info(f"📎 即梦参考图上传: {line[len('UPLOAD='):]}")
                elif line.startswith("DIAG="):
                    log.warning(f"🧭 即梦诊断: {line[len('DIAG='):]}")
                elif line.startswith("CREDITS="):
                    log.info(f"💯 即梦剩余积分: {line[len('CREDITS='):]}")
        if login_expired:
            raise LoginExpired("即梦登录已失效，切换到其它服务")
        if exhausted:
            raise CreditsExhausted(
                f"即梦积分不足 (剩余 {self.min_credits} 以下)，切换到其它服务"
            )

        if shot_chunks:
            try:
                import base64
                b64 = "".join(shot_chunks[i] for i in sorted(shot_chunks))
                raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
                return self._save_from_bytes(raw, output_path, clip)
            except Exception as e:
                log.warning(f"⚠️ 即梦截图重组失败: {e}")

        if shot_file and os.path.exists(shot_file):
            try:
                return self._save_from_screenshot(shot_file, output_path, clip)
            finally:
                try:
                    os.remove(shot_file)
                except Exception:
                    pass

        if not result_url:
            log.warning(f"⚠️ 即梦未获取到结果图。CLI stderr: {stderr[-300:]}")
            return None

        return await self._download(result_url, output_path)

    def _save_from_bytes(self, raw: bytes, output_path: str, clip: dict) -> str:
        """把整页截图（PNG bytes）按图片矩形裁剪成封面。"""
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if clip and clip.get("w"):
            dpr = float(clip.get("dpr") or 1)
            x = max(0, int(float(clip["x"]) * dpr))
            y = max(0, int(float(clip["y"]) * dpr))
            x2 = min(img.width, x + int(float(clip["w"]) * dpr))
            y2 = min(img.height, y + int(float(clip["h"]) * dpr))
            if x2 - x > 200 and y2 - y > 120:
                img = img.crop((x, y, x2, y2))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if output_path.lower().endswith((".jpg", ".jpeg")):
            img.save(output_path, "JPEG", quality=95)
        else:
            img.save(output_path, "PNG")
        log.info(f"✅ 即梦封面已保存（预览截图裁剪）: {output_path} {img.width}x{img.height}")
        return output_path

    def _save_from_screenshot(self, shot_path: str, output_path: str, clip: dict) -> str:
        """把整页截图按图片矩形裁剪出来（dreamina CDN 直连 403，截图是唯一稳定取图方式）。"""
        from PIL import Image

        img = Image.open(shot_path)
        if clip and clip.get("w"):
            dpr = float(clip.get("dpr") or 1)
            x = max(0, int(float(clip["x"]) * dpr))
            y = max(0, int(float(clip["y"]) * dpr))
            w = int(float(clip["w"]) * dpr)
            h = int(float(clip["h"]) * dpr)
            x2, y2 = min(img.width, x + w), min(img.height, y + h)
            if x2 - x > 200 and y2 - y > 120:
                img = img.crop((x, y, x2, y2))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if output_path.lower().endswith((".jpg", ".jpeg")):
            img.save(output_path, "JPEG", quality=95)
        else:
            img.save(output_path, "PNG")
        log.info(f"✅ 即梦封面已保存（预览截图裁剪）: {output_path} {img.width}x{img.height}")
        return output_path

    # ------------------------------------------------------------------ #

    def _build_cli_script(self, prompt: str, input_image: Optional[str], output_path: str) -> str:
        """新版即梦（Agent 对话式 UI）CLI 脚本 — 2026-09-14 适配。

        旧版（≤2026-09-13）：左侧生成面板 + input[type=file][accept*=image] 上传。
        新版：整站改成 Agent 对话流 —— 上传入口变成 [class*=reference-upload]（类名带构建哈希），
              页面里**不再存在** input[type=file]，必须走 Playwright 式
              page.waitForFileChooser() + fileChooser.setFiles()。
              编辑框仍是 .tiptap.ProseMirror，结果图仍在 dreamina-sign/heycan-hgt 域。
        """
        script = self._JIMENG_CLI_TEMPLATE
        for key, val in (
            ("__TASK_SPACE__", json.dumps(self.task_space)),
            ("__URL__", json.dumps(JIMENG_CREATE_URL)),
            ("__INPUT_IMAGE__", json.dumps(input_image)),
            ("__PROMPT__", json.dumps(prompt)),
            ("__MIN_CREDITS__", str(int(self.min_credits))),
            ("__TIMEOUT__", str(float(self.generate_timeout))),
        ):
            script = script.replace(key, val)
        return script


    _JIMENG_CLI_TEMPLATE = r'''const task = await taskSpace(__TASK_SPACE__)
const page = await task.newPage()
try {
  await page.goto(__URL__, { waitUntil: "domcontentloaded" })
  await wait(14)   // SPA 首屏（Agent 对话界面）加载较慢

  // ── 1. 关掉"全新即梦网页版"引导层，否则点不到上传 ──
  const dismissed = await page.evaluate(String.raw`(() => {
    const texts = ["开启即梦", "开始创作", "我知道了", "跳过", "稍后再说"]
    let hit = ""
    ;[...document.querySelectorAll("button,[role=button],[class*=btn],[class*=close]")].forEach(b => {
      const t = (b.innerText || "").trim()
      if (!hit && texts.includes(t)) { b.click(); hit = t }
    })
    return hit
  })()`)
  cliLog("DISMISS=" + dismissed)
  await wait(2)

  // ── 2. 登录态 + 积分 ──
  const bodyText = await page.evaluate(String.raw`document.body.innerText`)
  const cm = bodyText.match(/(\d+)\s*开会员/)
  const credits = cm ? parseInt(cm[1]) : 0
  cliLog("CREDITS=" + credits)
  const hasLoginMenu = await page.evaluate(String.raw`(() => {
    const items = [...document.querySelectorAll(".lv-menu-item")]
    return !!items.find(m => m.offsetParent !== null && m.innerText.trim() === "登录")
  })()`)
  if (hasLoginMenu) {
    cliLog("RESULT=LOGIN_EXPIRED")
  } else if (credits < __MIN_CREDITS__) {
    cliLog("RESULT=EXHAUSTED")
  } else {
    const INPUT_IMAGE = __INPUT_IMAGE__

    // ── 3. 上传参考图（2026-09-14 实测结论）──
    //    新版 Agent 界面里没有 input[type=file]；点 reference-upload 会弹出系统选择器，
    //    但 ego CLI 的 fileChooser 抓不到节点（"Either nodeId, backendNodeId or objectId"）。
    //    唯一可行 = CDP 拖放：Input.dispatchDragEvent 带 files 直接投喂文件。
    if (INPUT_IMAGE) {
      let uploaded = "no-target"
      const TARGETS = ["[class*=reference-upload]", "[class*=reference-group-content]", ".tiptap.ProseMirror"]
      const pickTarget = (sel) => page.evaluate(`(() => {
        const el = document.querySelector(${JSON.stringify(sel)})
        if (!el) return null
        const r = el.getBoundingClientRect()
        if (r.width < 8 || r.height < 8) return null
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
      })()`)
      const readRefs = () => page.evaluate(String.raw`(() => {
        const hasRef = !!document.querySelector("[class*=reference-item] img, [class*=reference-item] canvas, [class*=reference-item] video")
        return { hasRef: hasRef, n: document.querySelectorAll("[class*=reference-item]").length }
      })()`)
      const dismissAgain = () => page.evaluate(String.raw`(() => {
        const texts = ["开启即梦", "开始创作", "我知道了", "跳过", "稍后再说"]
        let hit = ""
        ;[...document.querySelectorAll("button,[role=button],[class*=btn],[class*=close]")].forEach(b => {
          const t = (b.innerText || "").trim()
          if (!hit && texts.includes(t)) { b.click(); hit = t }
        })
        return hit
      })()`)
      try {
        // 2026-09-19: 原实现只投一次、投不中就放弃（实测被引导层挡过一次就白跑）。
        // 改为多目标 × 两轮重试，每轮之间重新关一次引导层。
        for (let round = 0; round < 2; round++) {
          for (const sel of TARGETS) {
            const tgt = await pickTarget(sel)
            if (!tgt) continue
            const data = { items: [], files: [INPUT_IMAGE], dragOperationsMask: 1 }
            await page.cdp("Input.dispatchDragEvent", { type: "dragEnter", x: tgt.x, y: tgt.y, data })
            await page.cdp("Input.dispatchDragEvent", { type: "dragOver", x: tgt.x, y: tgt.y, data })
            await page.cdp("Input.dispatchDragEvent", { type: "drop", x: tgt.x, y: tgt.y, data })
            await wait(4)
            const st = await readRefs()
            if (st.hasRef) { uploaded = "drop-ok(" + sel + ",n=" + st.n + ")"; break }
          }
          if (String(uploaded).indexOf("drop-ok") === 0) break
          const d = await dismissAgain()
          if (d) cliLog("DISMISS_RETRY=" + d)
          await wait(2)
        }
        if (String(uploaded).indexOf("drop-ok") !== 0) {
          const st = await readRefs()
          uploaded = "drop-no-thumb(n=" + st.n + ")"
        }
      } catch (e) {
        uploaded = "err:" + String((e && e.message) || e).slice(0, 160)
      }
      cliLog("UPLOAD=" + uploaded)
      await wait(3)
    }

    // ── 4. 输入提示词并发送 ──
    // 2026-09-19: 实测编辑器有时要等页面稳定后才出现（首次探测为空 → FOCUS_FAIL=1 白跑）
    // → 改为最多 8 次重试 + 鼠标点击兜底
    let focused = false
    for (let i = 0; i < 8 && !focused; i++) {
      focused = await page.evaluate(String.raw`(() => {
        const els = [...document.querySelectorAll(".tiptap.ProseMirror,[contenteditable=true]")]
          .filter(e => e.offsetParent !== null)
        const main = els.find(e => e.getBoundingClientRect().y > 150) || els[0]
        if (!main) return false
        main.focus()
        main.click()
        return true
      })()`)
      if (!focused) {
        const pt = await page.evaluate(String.raw`(() => {
          const els = [...document.querySelectorAll(".tiptap.ProseMirror,[contenteditable=true]")]
            .filter(e => e.offsetParent !== null)
          const main = els.find(e => e.getBoundingClientRect().y > 150) || els[0]
          if (!main) return null
          const r = main.getBoundingClientRect()
          return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
        })()`)
        if (pt) {
          try { await page.mouse.click(pt.x, pt.y, { label: "聚焦输入框(兜底)" }); focused = true } catch (e) {}
        }
      }
      if (!focused) await wait(1.5)
    }
    if (!focused) {
      cliLog("RESULT=NONE")
      cliLog("FOCUS_FAIL=1")
    } else {
      // 2026-09-16: 记录发送前页面已有的图(含参考图/旧对话残留) → 之后只认"新出现"的图。
      // 之前只挑"最大的图"，会抓到别的对话残留 → 张冠李戴(被相关性校验拦下)。
      const BASELINE = await page.evaluate(String.raw`[...document.querySelectorAll("img")].map(i => i.src)`)
      const baseJson = JSON.stringify(BASELINE || [])
      const PROMPT = __PROMPT__
      await page.keyboard.type(PROMPT, { delay: 3 })
      await wait(1)
      // 发送（2026-09-14 实测）：新版 Agent 界面的提交按钮在 [class*=submit-button-container] 里，
      // 空输入时 disabled；输入后才可点。找不到才退化为回车。
      let sent = "none"
      for (let i = 0; i < 12; i++) {
        try {
          sent = await page.evaluate(String.raw`(() => {
            const cands = [...document.querySelectorAll("[class*=submit-button-container] button, button.lv-btn-primary, [class*=submit] button")]
              .filter(e => e.offsetParent !== null && e.disabled !== true && e.getBoundingClientRect().width > 6)
            if (cands.length) { cands[cands.length - 1].click(); return "button" }
            return "none"
          })()`)
        } catch (e) {}
        if (sent !== "none") break
        await wait(2)
      }
      if (sent === "none") {
        await page.keyboard.press("Enter")
        cliLog("SENT=enter")
      } else {
        cliLog("SENT=" + sent)
      }

      // ── 5. 等结果图 → 点开预览 → 截图取图（2026-09-14 实测）──
      //    dreamina-sign CDN 对直连/跨域 fetch 一律 403，fileChooser 也拿不到节点，
      //    因此改为：点开结果图预览 → 对图片区域做 CDP 截图 → 本地裁剪，稳定可靠。
      const timeout = __TIMEOUT__
      const deadline = Date.now() + timeout * 1000
      let shotOk = false
      let lastClickAt = 0
      while (Date.now() < deadline && !shotOk) {
        try {
          // 结果图 = 带 aigc_resize_mark 的 dreamina 图（参考图缩略图没有这个标记）
          const found = await page.evaluate(`(() => {
            const BASE = new Set(${baseJson})
            const imgs = [...document.querySelectorAll("img")]
              .filter(i => /dreamina-sign|heycan-hgt|byteimg/.test(i.src || ""))
              .filter(i => !/passport|avatar|logo|icon/i.test(i.src || ""))
              .filter(i => !BASE.has(i.src || ""))
            if (!imgs.length) return null
            const marked = imgs.filter(i => /aigc_resize_mark/.test(i.src || ""))
            const pool = marked.length ? marked : imgs
            const im = pool.sort((a, b) => (b.naturalWidth || 0) - (a.naturalWidth || 0))[0]
            const r = im.getBoundingClientRect()
            return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                     nw: im.naturalWidth || 0, marked: marked.length }
          })()`)
          if (found && found.nw >= 100 && Date.now() - lastClickAt > 20000) {
            lastClickAt = Date.now()
            await page.mouse.click(found.x, found.y, { label: "打开结果图预览" })
            await wait(6)
          }
          // 预览里找 1080 大图（点开后才有），找不到就继续等
          for (let k = 0; k < 4 && !shotOk; k++) {
            const rect = await page.evaluate(`(() => {
              const BASE2 = new Set(${baseJson})
              const imgs = [...document.querySelectorAll("img")]
                .filter(i => /dreamina-sign|byteimg/.test(i.src || ""))
                .filter(i => !BASE2.has(i.src || ""))
                .filter(i => (i.naturalWidth || 0) > 500)
              if (!imgs.length) return null
              const im = imgs.sort((a, b) => b.naturalWidth - a.naturalWidth)[0]
              const r = im.getBoundingClientRect()
              return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
                       nw: im.naturalWidth, nh: im.naturalHeight, dpr: window.devicePixelRatio || 1 }
            })()`)
            if (rect && rect.w > 200) {
              cliLog("CLIP=" + JSON.stringify(rect))
              // 截图偶发 CDP 超时 → 重试（实测第 2 次就能成功）
              // 2026-09-19: PNG 整页截图在本机 jimeng 页面上反复超时（SHOT_ERR×12 → RESULT=NONE），
              // 改用 jpeg(quality 85)：体积小一个量级，超时概率大幅下降；Python 侧用 PIL 打开，格式无关。
              let shot = null
              for (let a = 0; a < 5 && !shot; a++) {
                try { shot = await page.cdp("Page.captureScreenshot", { format: "jpeg", quality: 85 }) }
                catch (e) { cliLog("SHOT_ERR=" + String((e && e.message) || e).slice(0, 80)); await wait(3) }
              }
              if (shot && shot.data && shot.data.length > 5000) {
                // ego CLI 沙箱没有 require/fs → 用分块 base64 走 stdout，Python 侧重组
                const CH = 8000
                const n = Math.ceil(shot.data.length / CH)
                cliLog("SHOT_LEN=" + shot.data.length)
                for (let i = 0; i < n; i++) cliLog("SHOTB64_" + i + "/" + n + ":" + shot.data.substr(i * CH, CH))
                shotOk = true
              }
            }
            if (!shotOk) await wait(3)
          }
        } catch (e) {}
        if (!shotOk) await wait(3)
      }
      if (!shotOk) {
        // 失败诊断：把页面上所有图片域名/尺寸和正文尾部 dump 出来（便于下轮定位改版）
        try {
          const diag = await page.evaluate(String.raw`(() => {
            const srcs = [...document.querySelectorAll("img")].map(i => (i.src || "").slice(0, 80))
            const hosts = [...new Set(srcs.map(s => (s.match(/^https?:\/\/([^/]+)/) || [])[1]).filter(Boolean))]
            const body = (document.body.innerText || "").replace(/\s+/g, " ")
            return { hosts: hosts.slice(0, 10), nImg: srcs.length, tail: body.slice(-160) }
          })()`)
          cliLog("DIAG=" + JSON.stringify(diag))
        } catch (e) {}
      }
      cliLog("RESULT=" + (shotOk ? "OK" : "NONE"))
    }
  }
} finally {
  await task.finish({ keep: [] })   // 收尾必关标签（2026-09-14 用户反馈）
}
    '''


    async def _download(self, url: str, output_path: str) -> str:
        import httpx
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.content
        # jimeng full-res URLs are .webp; decode and re-encode to output format.
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
        log.info(f"✅ 即梦封面已保存: {output_path}")
        return output_path