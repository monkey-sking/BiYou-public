"""Patch for broken `gemini_webapi.utils.upload_file` (1.0.1).

上游 bug（2026-09-26 定位）：
  `gemini_webapi/utils.py::upload_file` 有 `@validate_call`，签名声明
  `proxies: Union[dict, None]`，但：

  1) 类型不匹配：传字符串 proxy（如 "http://127.0.0.1:7890"）会被 pydantic 拒绝
     -> ValidationError: Input should be a valid dictionary
  2) 函数体引用的 `Endpoint` / `Headers` / `AsyncClient` 在 utils 模块里**根本不存在**
     -> NameError: name 'Endpoint' is not defined

  两者叠加 => 该函数在任何情况下都不可用。而 BiYou 的 Nano Banana Pro
  手动 payload 通道（gemini_service._generate_image_patched）依赖它上传参考图，
  失败表现为 "1 validation error for upload_file"，封面出图 100% 失败。

本补丁提供一个行为等价、可用的 upload_file 实现，并 monkey-patch 到
`gemini_webapi.utils.upload_file`（以及 gemini_webapi 顶层若导出）。

行为与上游意图一致：
  - 接受 bytes 或文件路径
  - 支持 proxies 为 dict（{"http": ..., "https": ...}）或字符串（自动包装）
  - POST 到 constants.Endpoint.UPLOAD，带 constants.Headers.UPLOAD
  - 返回 response.text（文件标识串）
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Union

log = logging.getLogger(__name__)

_INSTALLED = False


def _normalize_proxies(proxies: Any) -> Optional[dict]:
    """字符串 -> httpx 需要的 dict；dict 原样返回；None -> None。"""
    if proxies is None or isinstance(proxies, dict):
        return proxies
    if isinstance(proxies, str):
        if not proxies.strip():
            return None
        return {"http://": proxies, "https://": proxies}
    return None


async def upload_file_fixed(
    file: Union[bytes, str],
    proxies: Any = None,
    **kwargs: Any,
) -> str:
    """可用版 upload_file。

    签名刻意宽松(**kwargs)，因为 gemini_webapi 内部不同版本/通道会用不同调用约定：
      - client.py:312  `upload_file(image, self.proxies)`        -> proxies 字符串
      - 其它通道       `upload_file(image, client=<session obj>)` -> 传 client 对象
    之前只接 proxies，遇到 client= 就 TypeError，把整条通道打成 "no result path"。
    这里统一兼容：proxies 走代理；client= 时尝试从中取代理配置，取不到就不走代理。
    """
    import httpx
    from gemini_webapi.constants import Endpoint, Headers

    # 兼容 client=<session> 调用约定：抽取其代理设置（拿不到则忽略）
    if proxies is None and "client" in kwargs:
        _cli = kwargs.pop("client", None)
        for attr in ("proxies", "proxy"):
            _p = getattr(_cli, attr, None)
            if isinstance(_p, str) and _p.strip():
                proxies = _p
                break

    if isinstance(file, str):
        if not os.path.isfile(file):
            raise FileNotFoundError(f"upload_file: 文件不存在: {file}")
        with open(file, "rb") as fh:
            file = fh.read()

    # 2026-09-26 关键修复：必须用 curl_cffi（带浏览器 TLS 指纹），不能用 httpx。
    # Google 的 content-push.googleapis.com/upload 会拒绝无正确 TLS 指纹的请求
    # → 400 Bad Request（这正是 BiYou 长期上传失败的原因）。
    # gemini_webapi 1.0.1 全链路用 curl_cffi.requests.AsyncSession(impersonate="chrome")。
    #
    # 优先级：
    #   1) 复用调用方传入的 client/session（client.py:312 传的是 curl_cffi session）
    #   2) 自己建一个 curl_cffi AsyncSession
    #   3) 最后才退回 httpx
    _sess = kwargs.pop("client", None) if "client" in kwargs else None
    if _sess is not None and not hasattr(_sess, "post"):
        _sess = None

    _proxy_url = None
    if _sess is not None:
        # curl_cffi AsyncSession 通常没有 proxies/proxy 属性 —— 需要从别处取。
        for attr in ("proxies", "proxy", "_proxy", "proxies_url"):
            _p = getattr(_sess, attr, None)
            if isinstance(_p, str) and _p.strip():
                _proxy_url = _p
                break
        # 2026-09-26: 库内部调用是 upload_file(file, client=self.client, ...)，
        # 而 self.client 是 curl_cffi session（不带 proxy 属性）→ 代理会丢。
        # 兜底：从 kwargs 里的 client_obj（GeminiClient 实例）取 self.proxies。
        if _proxy_url is None:
            _gc = kwargs.pop("_gemini_client", None)
            for attr in ("proxies", "proxy"):
                _p = getattr(_gc, attr, None)
                if isinstance(_p, str) and _p.strip():
                    _proxy_url = _p
                    break
    if _proxy_url is None and isinstance(proxies, str) and proxies.strip():
        _proxy_url = proxies
    elif _proxy_url is None and isinstance(proxies, dict):
        _proxy_url = (proxies.get("https://") or proxies.get("http://")
                      or next(iter(proxies.values()), None))
    # 最后兜底：BiYou 全局代理配置
    if _proxy_url is None:
        try:
            from core.utils.config import CONFIG
            _p = CONFIG.get("proxy")
            if isinstance(_p, str) and _p.strip():
                _proxy_url = _p
        except Exception:
            pass

    # ── 路径 A: curl_cffi（首选，带 TLS 指纹）──
    try:
        import curl_cffi.requests as _cr

        if _sess is not None:
            _c = _sess
            _own = False
        else:
            _kw = {"impersonate": "chrome", "allow_redirects": True}
            if _proxy_url:
                _kw["proxy"] = _proxy_url
            _c = _cr.AsyncSession(**_kw)
            _own = True
        try:
            # curl_cffi 需要 CurlMime 对象（不是 dict）+ 必须带 Push-ID header。
            # 参考 gemini_webapi 1.0.1 自带实现：
            #   mime_part = CurlMime(); mime_part.addpart(name="file", ...)
            #   headers = {**Headers.REFERER.value, **Headers.UPLOAD.value, "Push-ID": push_id}
            import mimetypes as _mt
            try:
                from curl_cffi import CurlMime as _CurlMime
            except Exception:
                from curl_cffi.requests import CurlMime as _CurlMime

            _filename = kwargs.get("filename") or "image.jpg"
            _ctype = _mt.guess_type(_filename)[0] or "application/octet-stream"
            _mp = _CurlMime()
            _mp.addpart(name="file", content_type=_ctype,
                        filename=_filename, data=file)

            _hdrs = {}
            try:
                _hdrs.update(getattr(Headers, "REFERER").value)
            except Exception:
                pass
            try:
                _hdrs.update(getattr(Headers, "UPLOAD").value)
            except Exception:
                pass
            # Push-ID：优先用调用方/客户端给的，其次 push_id 参数，最后库内默认值
            _push = (kwargs.get("push_id")
                     or getattr(_sess, "push_id", None)
                     or "feeds/mcudyrk2a4khkz")
            _hdrs["Push-ID"] = _push

            try:
                response = await _c.post(
                    Endpoint.UPLOAD.value,
                    headers=_hdrs,
                    multipart=_mp,
                    allow_redirects=True,
                )
            finally:
                try:
                    _mp.close()
                except Exception:
                    pass
            if getattr(response, "status_code", 0) >= 400:
                raise RuntimeError(
                    f"upload HTTP {response.status_code}: {str(response.text)[:200]}")
            return response.text
        finally:
            if _own:
                try:
                    await _c.close()
                except Exception:
                    pass
    except ImportError:
        log.debug("curl_cffi 不可用，退回 httpx")
    except Exception as _ce:
        log.warning(f"curl_cffi upload 失败({_ce})，尝试 httpx 兜底")

    # ── 路径 B: httpx 兜底 —— 注意：可能因缺 TLS 指纹被 Google 400 拒 ──
    import httpx

    # 兼容 httpx 新旧参数名（0.28 起 proxies= -> proxy=）
    # httpx 0.28: proxy 接受 str/URL/Proxy；传 dict 会 AttributeError。
    # 因此这里挑一个具体 URL 传进去。
    client_kwargs: dict[str, Any] = {}
    norm = _normalize_proxies(proxies)
    if norm:
        import inspect as _inspect
        params = _inspect.signature(httpx.AsyncClient.__init__).parameters
        if "proxy" in params:
            # 新版：需要单个 URL 字符串
            url = norm.get("https://") or norm.get("http://") or next(iter(norm.values()))
            client_kwargs["proxy"] = url
        else:
            client_kwargs["proxies"] = norm

    async with httpx.AsyncClient(**client_kwargs) as client:
        response = await client.post(
            url=Endpoint.UPLOAD.value,
            headers=Headers.UPLOAD.value,
            files={"file": file},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text


def install() -> bool:
    """monkey-patch gemini_webapi 的 upload_file。幂等。

    关键：gemini_webapi 内部 client.py 用模块级名字 `upload_file(...)` 调用
    （见 client.py:312 `await upload_file(image, self.proxies)`），
    因此必须替换 `gemini_webapi.utils.upload_file` 才能覆盖库内部调用路径。
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import gemini_webapi

        # 1) 主入口：utils 模块属性（库内部 from ... import 后的名字其实绑在 client 模块上）
        import gemini_webapi.utils as _u

        _u.upload_file = upload_file_fixed

        # 2) client 模块里是 `from .utils import upload_file` 复制的引用，
        #    必须显式改掉 client 模块上的那个名字，否则库内部仍走坏函数。
        try:
            import gemini_webapi.client as _c

            if hasattr(_c, "upload_file"):
                _c.upload_file = upload_file_fixed
        except Exception:
            pass

        # 3) 顶层若导出则一并替换
        if hasattr(gemini_webapi, "upload_file"):
            gemini_webapi.upload_file = upload_file_fixed

        _INSTALLED = True
        log.info("✅ gemini_webapi upload_file 补丁已安装 (utils + client)")
        return True
    except Exception as exc:  # pragma: no cover
        log.warning(f"⚠️ upload_file 补丁安装失败: {exc}")
        return False


# 供 BiYou 代码直接 import 使用（无需依赖 monkey-patch 时序）
upload_file = upload_file_fixed

# import 即安装，避免调用方忘记
install()
