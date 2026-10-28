"""
gemini_webapi access_token 注入补丁（2026-09-26）
=================================================
背景：gemini_webapi 1.0.1 的 `get_access_token()` 会遍历多组 cookie 尝试，
返回**第一组尝试**的结果 —— 实测该组常返回 `access_token=None`
（build_label/session_id 都有值，唯独 SNlM0e 缺失），
导致 `client.access_token = None`。

库自己的 docstring 写明：
    "Get SNlM0e value as access token. Without this token posting
     will fail with 400 bad request."

这就是 BiYou 长期 `400 Bad Request` 的根因 —— 不是 cookie 失效
（实测 cookie 直连 Google 返回 200 且含登录态），而是 token 没被正确取到。

实测证据（手动 curl_cffi 请求 https://gemini.google.com/app）：
    SNlM0e: AOvx0lKUZ0XENpoQMx0IgAPdHzZE:1790365049877   ← 明明能拿到
    cfb2h : boq_assistant-bard-web-server_20260923.22_p0
    FdrFJe: 8671182112600875166                          ← 正常正数 session_id

解法：init 之后检查 `access_token`，为空则用同一套 cookie 自行请求
gemini.google.com/app 并正则提取 SNlM0e/cfb2h/FdrFJe 回填。
"""

import re
import logging

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_PATTERNS = {
    "access_token": r'"SNlM0e":\s*"(.*?)"',
    "build_label": r'"cfb2h":\s*"(.*?)"',
    "session_id": r'"FdrFJe":\s*"(.*?)"',
    "language": r'"TuX5cc":\s*"(.*?)"',
    "push_id": r'"qKIAYe":\s*"(.*?)"',
}


def _cookie_dict(raw):
    """把各种形式的 cookie 归一化成 {name: value}。

    2026-09-26: curl_cffi 的 Cookies 对象**不能直接迭代出带 .name/.value 的对象**
    （实测迭代返回空）—— 必须用 `jar` / `get_dict()` / 逐项 get 兜底。
    """
    out = {}
    if raw is None:
        return out
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items()}

    # curl_cffi / requests 的 Cookies：优先 get_dict()
    for getter in ("get_dict", "get_dict_all"):
        fn = getattr(raw, getter, None)
        if callable(fn):
            try:
                d = fn()
                if isinstance(d, dict) and d:
                    return dict(d)
            except Exception:
                pass

    # 尝试底层 jar
    jar = getattr(raw, "jar", None)
    if jar is not None:
        try:
            for c in jar:
                if getattr(c, "name", None):
                    out[c.name] = c.value
            if out:
                return out
        except Exception:
            pass

    # 直接迭代（部分版本可用）
    try:
        for c in raw:
            n = getattr(c, "name", None)
            if n:
                out[n] = getattr(c, "value", None)
        if out:
            return out
    except Exception:
        pass

    # 最后兜底：itermulti / items
    for meth in ("items", "itermulti"):
        fn = getattr(raw, meth, None)
        if callable(fn):
            try:
                for item in fn():
                    if isinstance(item, tuple) and len(item) >= 2:
                        out[item[0]] = item[1]
                    elif hasattr(item, "name"):
                        out[item.name] = item.value
                if out:
                    return out
            except Exception:
                pass
    return out


async def fetch_access_token(cookies, proxy=None, verify=True, timeout=60.0):
    """用给定 cookie 请求 gemini.google.com/app，提取 token 等字段。

    返回 dict（可能为空），键：access_token/build_label/session_id/language/push_id
    """
    try:
        from curl_cffi.requests import AsyncSession
    except Exception as e:
        log.debug(f"[gemini-token-patch] curl_cffi 不可用: {e}")
        return {}

    ck = _cookie_dict(cookies)
    if not ck:
        return {}

    # 2026-09-26: 只送核心认证 cookie —— 实测带全量 cookie（含 AEC/NID/COMPASS 等
    # 追踪 cookie）时 Google 返回的页面**不含 SNlM0e**（取不到 token），
    # 只带 __Secure-1PSID + __Secure-1PSIDTS 时能正常拿到。
    _CORE = ("__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-3PSID",
             "__Secure-3PSIDTS", "SID", "HSID", "SSID", "APISID", "SAPISID")
    jar = {k: v for k, v in ck.items()
           if k in _CORE and v}
    if not jar:
        jar = {k: v for k, v in ck.items() if v}

    kwargs = {"impersonate": "chrome", "allow_redirects": True, "verify": verify}
    if proxy:
        try:
            kwargs["proxy"] = proxy
        except Exception:
            pass

    sess = None
    try:
        sess = AsyncSession(**kwargs)
        for k, v in jar.items():
            try:
                sess.cookies.set(k, v, domain=".google.com")
            except Exception:
                pass
        # 2026-09-26: 新建 session 偶发 "TLS connect error: invalid library"，
        # 重试一次并把 verify 打开；仍失败则放弃（不阻断主流程）。
        last_err = None
        for _attempt in range(2):
            try:
                r = await sess.get("https://gemini.google.com/app", timeout=timeout)
                last_err = None
                break
            except Exception as _e:
                last_err = _e
                if _attempt == 0:
                    await asyncio.sleep(1.0)
        if last_err is not None:
            log.warning(f"[gemini-token-patch] 取 token 网络异常: {last_err}")
            return {}
        if r.status_code != 200:
            log.warning(f"[gemini-token-patch] 取 token 失败 HTTP {r.status_code}")
            return {}
        text = r.text
        out = {}
        for name, pat in _PATTERNS.items():
            m = re.search(pat, text)
            if m:
                out[name] = m.group(1)
        if out.get("access_token"):
            log.info("[gemini-token-patch] ✅ 成功获取 access_token "
                     f"({len(out['access_token'])}ch), "
                     f"session_id={out.get('session_id')}")
        else:
            log.warning("[gemini-token-patch] ⚠️ 页面未含 SNlM0e（可能未登录）")
        return out
    except Exception as e:
        log.warning(f"[gemini-token-patch] 取 token 异常: {e}")
        return {}
    finally:
        if sess is not None:
            try:
                await sess.close()
            except Exception:
                pass


async def ensure_access_token(client, proxy=None):
    """若 client.access_token 为空，则自行取回并注入。

    返回 True 表示 token 可用。

    2026-09-26: cookie 来源优先级 —— 先用 client._biyou_raw_cookies（构造时传入的
    原始 cookie，干净），再退回 client.cookies。实测 client.cookies 在 init 后
    已混入 AEC/NID/COMPASS 等追踪 cookie，带它们请求时页面不含 SNlM0e。
    """
    if getattr(client, "access_token", None):
        return True

    proxy = proxy if proxy is not None else getattr(client, "proxy", None)

    candidates = []
    raw = getattr(client, "_biyou_raw_cookies", None)
    if raw:
        candidates.append(raw)
    candidates.append(getattr(client, "cookies", None))
    candidates.append(getattr(client, "_cookies", None))

    for cand in candidates:
        if not cand:
            continue
        info = await fetch_access_token(cand, proxy=proxy)
        if info.get("access_token"):
            client.access_token = info["access_token"]
            if info.get("build_label"):
                client.build_label = info["build_label"]
            if info.get("session_id"):
                client.session_id = info["session_id"]
            if info.get("language"):
                client.language = info["language"]
            if info.get("push_id"):
                client.push_id = info["push_id"]
            log.info("[gemini-token-patch] 🔧 access_token 已注入 client")
            return True
    return False


def install():
    """monkey-patch GeminiClient.init，init 后自动补 token。幂等。"""
    try:
        from gemini_webapi import GeminiClient
    except Exception as e:
        log.debug(f"[gemini-token-patch] 无法 import GeminiClient: {e}")
        return False

    if getattr(GeminiClient, "_hermes_token_patched", False):
        return True

    _orig_init = GeminiClient.__init__

    def _patched_ctor(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        # 保存构造时传入的原始 cookie（init() 之后会被混入追踪 cookie）
        try:
            src = kwargs.get("cookies")
            if src is None and args:
                src = args[0] if isinstance(args[0], (dict,)) else None
            if src:
                self._biyou_raw_cookies = src
        except Exception:
            pass

    if not getattr(GeminiClient, "_hermes_ctor_patched", False):
        GeminiClient.__init__ = _patched_ctor
        GeminiClient._hermes_ctor_patched = True

    _orig_init_async = GeminiClient.init

    async def _patched_init(self, *args, **kwargs):
        await _orig_init_async(self, *args, **kwargs)
        try:
            ok = await ensure_access_token(self)
            if not ok:
                log.warning("[gemini-token-patch] token 补取失败，posting 可能 400")
        except Exception as e:
            log.warning(f"[gemini-token-patch] init 补 token 异常: {e}")

    GeminiClient.init = _patched_init
    GeminiClient._hermes_token_patched = True
    log.info("[gemini-token-patch] ✅ GeminiClient.init 已打补丁")
    return True


if __name__ == "__main__":  # 自测
    import asyncio
    import json
    logging.basicConfig(level=logging.INFO)

    async def main():
        d = json.load(open("${HOME}/biyou/config/gemini_cookies.json"))
        ck = {"__Secure-1PSID": d["secure_1psid"],
              "__Secure-1PSIDTS": d["secure_1psidts"]}
        info = await fetch_access_token(ck, proxy="http://127.0.0.1:7890")
        print(json.dumps({k: (v[:40] + "..." if k == "access_token" and v else v)
                          for k, v in info.items()}, ensure_ascii=False, indent=2))

    asyncio.run(main())
