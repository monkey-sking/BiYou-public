"""
Compatibility helpers for gemini_webapi/httpx API drift.

Keep these shims in BiYou so pip reinstalls do not wipe local fixes.
"""

from __future__ import annotations

import functools
import inspect
import sys
from typing import Any, Callable

from core.utils.logger import log


def _signature_params(callable_obj: Callable[..., Any]) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(callable_obj).parameters)
    except (TypeError, ValueError):
        return {}


def _accepts_parameter(callable_obj: Callable[..., Any], name: str) -> bool:
    params = _signature_params(callable_obj)
    if name in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _explicitly_accepts_parameter(callable_obj: Callable[..., Any], name: str) -> bool:
    return name in _signature_params(callable_obj)


def _proxy_from_proxies(proxies: Any) -> Any:
    if not isinstance(proxies, dict):
        return proxies

    for key in ("https://", "https", "http://", "http", "all://", "all"):
        value = proxies.get(key)
        if value:
            return value

    for value in proxies.values():
        if value:
            return value
    return None


def _proxies_from_proxy(proxy: Any) -> Any:
    if proxy is None or isinstance(proxy, dict):
        return proxy
    return {"http": proxy, "https": proxy}


def normalize_httpx_async_client_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return kwargs accepted by the installed httpx.AsyncClient."""
    normalized = dict(kwargs)
    try:
        import httpx
    except Exception:
        return normalized

    async_client_class = getattr(
        httpx.AsyncClient,
        "_biyou_original_async_client",
        httpx.AsyncClient,
    )
    params = _signature_params(async_client_class)
    has_proxy = "proxy" in params
    has_proxies = "proxies" in params

    if has_proxy and not has_proxies and "proxies" in normalized and "proxy" not in normalized:
        proxy = _proxy_from_proxies(normalized.pop("proxies"))
        if proxy:
            normalized["proxy"] = proxy
        return normalized

    if has_proxies and not has_proxy and "proxy" in normalized and "proxies" not in normalized:
        normalized["proxies"] = _proxies_from_proxy(normalized.pop("proxy"))

    return normalized


def apply_httpx_proxy_compat() -> bool:
    """Teach httpx.AsyncClient to accept the old proxies= spelling."""
    try:
        import httpx
    except Exception:
        return False

    if getattr(httpx.AsyncClient, "_biyou_proxy_compat", False):
        compat_async_client = httpx.AsyncClient
        original_async_client = getattr(
            compat_async_client,
            "_biyou_original_async_client",
            compat_async_client,
        )
    else:
        original_async_client = httpx.AsyncClient

        class CompatAsyncClient(original_async_client):
            _biyou_proxy_compat = True
            _biyou_original_async_client = original_async_client

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **normalize_httpx_async_client_kwargs(kwargs))

        compat_async_client = CompatAsyncClient
        httpx.AsyncClient = compat_async_client

    applied = httpx.AsyncClient is compat_async_client

    # gemini_webapi imports "AsyncClient" directly into several modules via
    # `from httpx import AsyncClient`, which caches the class at import time.
    # If those modules were imported before this shim, patch every cached alias
    # so the httpx 0.28 `proxies=` -> `proxy=` normalization applies everywhere.
    for _mod_name in (
        "gemini_webapi.client",
        "gemini_webapi.types.image",
        "gemini_webapi.utils.rotate_1psidts",
        "gemini_webapi.utils.upload_file",
        # NOTE: gemini_webapi.utils.get_access_token is re-exported as a
        # *function* by gemini_webapi.utils.__init__, so `import
        # gemini_webapi.utils.get_access_token` yields the function, NOT the
        # module. Must reach the real module via sys.modules.
        "gemini_webapi.utils.get_access_token",
    ):
        _mod = sys.modules.get(_mod_name)
        if _mod is not None:
            _mod_async_client = getattr(_mod, "AsyncClient", None)
            if _mod_async_client is original_async_client:
                _mod.AsyncClient = compat_async_client
                applied = True

    return applied


def build_gemini_client_kwargs(
    client_class: type,
    cookies: dict[str, str],
    proxy: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "secure_1psid": cookies["secure_1psid"],
        "secure_1psidts": cookies.get("secure_1psidts"),
    }
    if not proxy:
        return kwargs

    init = getattr(client_class, "__init__", None)
    if init and _explicitly_accepts_parameter(init, "proxy"):
        kwargs["proxy"] = proxy
    elif init and _explicitly_accepts_parameter(init, "proxies"):
        kwargs["proxies"] = _proxies_from_proxy(proxy)
    else:
        kwargs["proxy"] = proxy
    return kwargs


def apply_gemini_client_init_compat(client_class: type) -> bool:
    """Accept both proxy= and proxies= for GeminiClient construction."""
    original_init = getattr(client_class, "__init__", None)
    if original_init is None or getattr(original_init, "_biyou_proxy_compat", False):
        return False

    @functools.wraps(original_init)
    def compat_init(self: Any, *args: Any, **kwargs: Any) -> None:
        params = _signature_params(original_init)
        has_proxy = "proxy" in params
        has_proxies = "proxies" in params

        if has_proxy and not has_proxies and "proxies" in kwargs and "proxy" not in kwargs:
            proxy = _proxy_from_proxies(kwargs.pop("proxies"))
            if proxy:
                kwargs["proxy"] = proxy
        elif has_proxies and not has_proxy and "proxy" in kwargs and "proxies" not in kwargs:
            kwargs["proxies"] = _proxies_from_proxy(kwargs.pop("proxy"))

        return original_init(self, *args, **kwargs)

    compat_init._biyou_proxy_compat = True
    compat_init.__wrapped__ = original_init
    client_class.__init__ = compat_init
    return True


def normalize_generate_content_kwargs(
    generate_content: Callable[..., Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Normalize BiYou's old generate_content kwargs for the installed API."""
    normalized = dict(kwargs)
    params = _signature_params(generate_content)
    has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    explicit = set(params)

    supports_files = "files" in explicit
    supports_image = "image" in explicit
    supports_model = "model" in explicit

    if supports_image and not supports_files and "files" in normalized and "image" not in normalized:
        files = normalized.pop("files")
        if isinstance(files, (list, tuple)):
            normalized["image"] = files[0] if files else None
        else:
            normalized["image"] = files

    if supports_files and not supports_image and "image" in normalized and "files" not in normalized:
        image = normalized.pop("image")
        normalized["files"] = [image] if image else None

    if "model" in normalized and (not supports_model or normalized["model"] is None):
        # gemini_webapi 1.21.0+ 支持 model 参数但不接受 None (TypeError)；
        # None → 删除, 让默认 Model.UNSPECIFIED 生效
        normalized.pop("model")

    if not has_var_kwargs:
        normalized = {key: value for key, value in normalized.items() if key in explicit}

    return normalized


async def generate_content_compat(client: Any, prompt: str, **kwargs: Any) -> Any:
    method = client.generate_content
    return await method(prompt, **normalize_generate_content_kwargs(method, kwargs))


def apply_gemini_generate_content_compat(client_class: type) -> bool:
    original_generate = getattr(client_class, "generate_content", None)
    if original_generate is None or getattr(original_generate, "_biyou_content_compat", False):
        return False

    @functools.wraps(original_generate)
    async def compat_generate_content(self: Any, prompt: str, **kwargs: Any) -> Any:
        normalized = normalize_generate_content_kwargs(original_generate, kwargs)
        return await original_generate(self, prompt, **normalized)

    compat_generate_content._biyou_content_compat = True
    compat_generate_content.__wrapped__ = original_generate
    client_class.generate_content = compat_generate_content
    return True


def apply_gemini_compat(client_class: type | None = None) -> bool:
    """Apply all safe compatibility shims available in this environment."""
    applied = apply_httpx_proxy_compat()

    if client_class is None:
        try:
            from gemini_webapi import GeminiClient
        except Exception:
            return applied
        client_class = GeminiClient

    applied = apply_gemini_client_init_compat(client_class) or applied
    applied = apply_gemini_generate_content_compat(client_class) or applied
    # 2026-09-26: access_token 补取 —— 修 400 Bad Request 根因。
    # gemini_webapi 的 get_access_token() 返回 access_token=None（其余字段正常），
    # 库 docstring 明确 "Without this token posting will fail with 400 bad request"。
    try:
        from core.utils.gemini_token_patch import install as _install_token_patch
        applied = _install_token_patch() or applied
    except Exception:
        pass
    if applied:
        log.info("✅ Gemini compatibility layer applied.")
    return applied
