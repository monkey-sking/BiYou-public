import unittest
import sys
from types import ModuleType
from unittest.mock import patch


class TestGeminiCompat(unittest.TestCase):
    def test_httpx_proxy_patch_updates_loaded_gemini_webapi_alias(self):
        import asyncio
        import httpx
        from core.utils.gemini_compat import apply_httpx_proxy_compat

        original_httpx_async_client = httpx.AsyncClient
        self.addCleanup(setattr, httpx, "AsyncClient", original_httpx_async_client)
        # Reproduce an older dependency caching httpx's original class.
        gemini_client_module = ModuleType("gemini_webapi.client")
        gemini_client_module.AsyncClient = httpx.AsyncClient
        with patch.dict(sys.modules, {"gemini_webapi.client": gemini_client_module}):
            apply_httpx_proxy_compat()

        self.assertIs(gemini_client_module.AsyncClient, httpx.AsyncClient)
        client = gemini_client_module.AsyncClient(proxies={"https": "http://127.0.0.1:7890"})
        self.addCleanup(lambda: asyncio.run(client.aclose()))
        self.assertTrue(getattr(gemini_client_module.AsyncClient, "_biyou_proxy_compat", False))

    def test_httpx_patch_supports_dependency_without_cached_alias(self):
        import httpx
        from core.utils.gemini_compat import apply_httpx_proxy_compat
        original = httpx.AsyncClient
        self.addCleanup(setattr, httpx, "AsyncClient", original)
        module = ModuleType("gemini_webapi.client")
        with patch.dict(sys.modules, {"gemini_webapi.client": module}):
            self.assertTrue(apply_httpx_proxy_compat())
        self.assertFalse(hasattr(module, "AsyncClient"))

    def test_httpx_028_normalizes_proxies_to_proxy(self):
        from core.utils.gemini_compat import normalize_httpx_async_client_kwargs

        kwargs = normalize_httpx_async_client_kwargs(
            {
                "headers": {"x-test": "1"},
                "proxies": {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
            }
        )

        self.assertNotIn("proxies", kwargs)
        self.assertEqual(kwargs["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(kwargs["headers"], {"x-test": "1"})

    def test_gemini_client_kwargs_use_supported_proxy_name(self):
        from core.utils.gemini_compat import build_gemini_client_kwargs

        class ProxyClient:
            def __init__(self, secure_1psid=None, secure_1psidts=None, proxy=None, **kwargs):
                pass

        kwargs = build_gemini_client_kwargs(
            ProxyClient,
            {"secure_1psid": "sid", "secure_1psidts": "sidts"},
            "http://127.0.0.1:7890",
        )

        self.assertEqual(kwargs["secure_1psid"], "sid")
        self.assertEqual(kwargs["secure_1psidts"], "sidts")
        self.assertEqual(kwargs["proxy"], "http://127.0.0.1:7890")
        self.assertNotIn("proxies", kwargs)

    def test_generate_content_kwargs_convert_files_model_for_image_api(self):
        from core.utils.gemini_compat import normalize_generate_content_kwargs

        async def image_api(prompt, image=None):
            return None

        kwargs = normalize_generate_content_kwargs(
            image_api,
            {"files": ["/tmp/source.png"], "model": "ignored"},
        )

        self.assertEqual(kwargs, {"image": "/tmp/source.png"})

    def test_generate_content_kwargs_preserve_current_files_model_api(self):
        from core.utils.gemini_compat import normalize_generate_content_kwargs

        async def files_api(prompt, files=None, model=None, **kwargs):
            return None

        kwargs = normalize_generate_content_kwargs(
            files_api,
            {"files": ["/tmp/source.png"], "model": "Flash", "temporary": True},
        )

        self.assertEqual(
            kwargs,
            {"files": ["/tmp/source.png"], "model": "Flash", "temporary": True},
        )


if __name__ == "__main__":
    unittest.main()
