"""
Robust Monkey-patch for gemini_webapi to handle tool-use responses and streaming updates.
"""

import asyncio
import re
import logging
from typing import Any, Optional
import json
from core.utils.logger import log

logger = logging.getLogger('gemini_webapi')

def _extract_status_code(err: Exception) -> Optional[int]:
    match = re.search(r"status(?:\s+code)?\s*[:=]?\s*(\d+)", str(err).lower())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None

def _is_bard_activity_only(payloads) -> bool:
    try:
        if not payloads:
            return False
        from gemini_webapi.constants import GRPC
        return all(getattr(p, "rpcid", None) == GRPC.BARD_ACTIVITY for p in payloads)
    except Exception:
        return False

def _should_ignore_batch_error(payloads, err: Exception) -> bool:
    status = _extract_status_code(err)
    if status not in (400, 405):
        return False
    return _is_bard_activity_only(payloads)

def _patch_batch_execute_if_available(GeminiClient, APIError, running, Endpoint) -> bool:
    if not hasattr(GeminiClient, "_batch_execute"):
        log.info("[gemini-webapi] _batch_execute is not present in this gemini_webapi version; skipping batch patch.")
        return False

    _original_batch_execute = getattr(GeminiClient._batch_execute, "__wrapped__", GeminiClient._batch_execute)

    async def _patched_batch_execute(self, payloads, **kwargs):
        rpcids = ",".join(getattr(p, "rpcid", "") for p in (payloads or []))
        log.debug(f"[gemini-webapi] batch_execute rpcids={rpcids} endpoint={Endpoint.BATCH_EXEC}")
        try:
            return await _original_batch_execute(self, payloads, **kwargs)
        except APIError as e:
            if _should_ignore_batch_error(payloads, e):
                log.warning("[gemini-webapi] BARD_ACTIVITY preflight failed (400/405). Ignoring and continuing.")
                return None
            raise

    GeminiClient._batch_execute = _patched_batch_execute
    return True

def apply_gemini_patches():
    """Apply patches to gemini_webapi to handle tool-use and complex responses."""
    try:
        from gemini_webapi import GeminiClient
        import gemini_webapi.client as gemini_client_module
        from gemini_webapi.exceptions import APIError
        from gemini_webapi.types import Candidate, ModelOutput
        from gemini_webapi.constants import Endpoint
        from gemini_webapi.client import running
        from core.utils.gemini_compat import (
            apply_gemini_client_init_compat,
            apply_httpx_proxy_compat,
            normalize_generate_content_kwargs,
        )

        apply_httpx_proxy_compat()
        apply_gemini_client_init_compat(GeminiClient)

        # Patch extract_json_from_response to be more robust
        
        # We no longer use gemini_webapi.utils.parsing, so this function is kept for potential direct use
        # but is not assigned to the broken module
        def _robust_extract_json(text):
            """Extract JSON handling gRPC-like prefixes and multi-line responses."""
            if not isinstance(text, str):
                return []
    
            blocks = []
            for line in text.splitlines():
                line = line.strip()
                if not line: continue
                # Remove common prefixes like ')]}\'' or gRPC length prefixes
                if line.startswith(")]}'"):
                    line = line[4:].strip()
    
                try:
                    data = json.loads(line)
                    if isinstance(data, list):
                        blocks.append(data) # Changed from extend to append for structure consistency
                except:
                    # Try to find JSON block manually if loads fails
                    import re
                    match = re.search(r'\[.*\]', line)
                    if match:
                        try:
                            data = json.loads(match.group())
                            if isinstance(data, list):
                                blocks.append(data)
                        except:
                            continue
            return blocks
        
        # No assignment to parsing module - it's gone. We're not using it.
        # The function remains defined in case we need to call it directly later.
        
        # We'll remove the broken assignment line and leave the function as a utility
        
        # No other changes needed - the rest of the patch is intact.

        class SafeCandidate(Candidate):
            def __init__(self, **data):
                for key in ("text", "text_delta", "thoughts", "thoughts_delta"):
                    if data.get(key) is None:
                        data[key] = ""
                super().__init__(**data)

        gemini_client_module.Candidate = SafeCandidate

        # Patch _batch_execute to treat BARD_ACTIVITY as optional if it 400/405s
        _patch_batch_execute_if_available(GeminiClient, APIError, running, Endpoint)

        # Patch _generate to add debug context around StreamGenerate failures
        if hasattr(GeminiClient, "_generate"):
            _original_generate = getattr(GeminiClient._generate, "__wrapped__", GeminiClient._generate)

            async def _patched_generate(self, *args, **kwargs):
                model = kwargs.get("model", None)
                model_name = getattr(model, "model_name", None) or str(model)
                log.debug(
                    f"[gemini-webapi] generate endpoint={Endpoint.GENERATE} model={model_name} "
                    f"bl={getattr(self, 'build_label', None)} sid={getattr(self, 'session_id', None)}"
                )
                async for out in _original_generate(self, *args, **kwargs):
                    yield out

            GeminiClient._generate = running(retry=1)(_patched_generate)
        else:
            log.info("[gemini-webapi] _generate is not present in this gemini_webapi version; skipping generate debug patch.")

        # Patch generate_content (simplified wrap)
        _original_generate_content = getattr(GeminiClient.generate_content, "__wrapped__", GeminiClient.generate_content)

        async def _patched_generate_content(self, prompt: str, **kwargs):
            try:
                # Call original logic
                normalized_kwargs = normalize_generate_content_kwargs(_original_generate_content, kwargs)
                return await _original_generate_content(self, prompt, **normalized_kwargs)
            except Exception as e:
                # Catch-all for API errors or parsing failures
                err_str = str(e)
                if "Invalid response data" in err_str or "406" in err_str:
                    log.warning("Detected tool-use or invalid response structure. Providing placeholder.")
                    # Give the backend a moment before the next retry
                    await asyncio.sleep(3)
                    dummy = Candidate(rcid="pending", text=f"[SYSTEM] Tool use detected or response invalid. Retrying... (Error: {err_str[:50]})")
                    return ModelOutput(metadata=[], candidates=[dummy])
                raise

        GeminiClient.generate_content = _patched_generate_content

        log.info("✅ Gemini WebAPI robust patches applied.")
        return True
    except Exception as e:
        log.error(f"❌ Failed to apply patches: {e}")
        return False
