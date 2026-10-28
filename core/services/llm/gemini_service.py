import os
import sys
import json
import asyncio
from typing import Optional
from datetime import datetime
from core.services.llm.base import LLMBase
from core.utils.config import load_config
from core.utils.i18n import print_t
from core.utils.logger import log
from core.utils.decorators import async_retry
from core.utils.gemini_models import (
    get_gemini_web_model_specs,
    normalize_gemini_model_key,
)
from core.utils.gemini_compat import generate_content_compat

# Import dependencies
try:
    from gemini_webapi import GeminiClient
    from core.utils.cookie import get_gemini_client as get_web_client
except ImportError:
    GeminiClient = None
    get_web_client = None

try:
    from gemini_webapi.constants import Model as WebModel
except ImportError:
    WebModel = None


genai = None
_official_import_attempted = False


def _load_official_genai():
    """Lazy import official Gemini SDK only when explicitly needed."""
    global genai, _official_import_attempted
    if _official_import_attempted:
        return genai

    _official_import_attempted = True
    try:
        import warnings
        warnings.filterwarnings("ignore", category=FutureWarning)
        import google.generativeai as _genai
        genai = _genai
    except ImportError:
        genai = None
    return genai


# ── Chat ID Tracking ──
CHAT_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "logs", "gemini_chats.jsonl")


def build_resource_link_extraction_prompt(title: str, description: str) -> str:
    """Prompt used by the resource-link extractor for Gemini-compatible chat models."""
    return f"""Extract external resource links from this YouTube video metadata.

Classify each URL into exactly one of:
- video_required: links viewers need to follow the video, download source files, join the related project/community, access assets, code, tools, project files, Patreon files, Discord files, GitHub repos, Asset Store/Fab/marketplace pages, cloud-drive files.
- promotional: sponsor, affiliate, merch, newsletter, general social links, unrelated channel promotion, or generic creator promotion.

Return ONLY valid JSON in this exact shape:
{{"links":[{{"url":"https://example.com","label":"short human label","classification":"video_required","reason":"why this link is needed"}}]}}

If no URLs exist, return {{"links":[]}}.

Video title:
{title}

YouTube description:
{description}
"""

def _log_chat_id(response):
    """记录 BiYou 产生的 Gemini 对话 ID，便于后续批量清理"""
    try:
        if response and hasattr(response, 'metadata') and response.metadata:
            cid = response.metadata[0] if len(response.metadata) > 0 else None
            if cid:
                os.makedirs(os.path.dirname(CHAT_LOG_FILE), exist_ok=True)
                entry = {"cid": cid, "time": datetime.now().isoformat(), "date": datetime.now().strftime("%Y-%m-%d"), "source": "webapi"}
                with open(CHAT_LOG_FILE, "a") as f:
                    f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # 不影响主流程

_auto_cleaned = False

async def _auto_clean_old_chats(client):
    """启动时自动删除前一天及更早的 BiYou 对话，只保留今天的"""
    global _auto_cleaned
    if _auto_cleaned or not client:
        return
    _auto_cleaned = True
    
    try:
        if not os.path.exists(CHAT_LOG_FILE):
            return
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        with open(CHAT_LOG_FILE, "r") as f:
            entries = [json.loads(line.strip()) for line in f if line.strip()]
        
        old = [e for e in entries if e.get("date", "") < today]
        keep = [e for e in entries if e.get("date", "") >= today]
        
        if not old:
            return
        
        cids = list(dict.fromkeys(e["cid"] for e in old))
        log.info(f"🧹 自动清理 {len(cids)} 个旧 BiYou 对话 (今天之前的)...")
        
        deleted = 0
        for cid in cids:
            try:
                await client.delete_chat(cid)
                deleted += 1
            except Exception:
                pass
        
        # 更新记录文件，只保留今天的
        with open(CHAT_LOG_FILE, "w") as f:
            for e in keep:
                f.write(json.dumps(e) + "\n")
        
        log.info(f"🧹 自动清理完成: 删除 {deleted}/{len(cids)} 个旧对话")
    except Exception as e:
        log.debug(f"自动清理跳过: {e}")

class GeminiService(LLMBase):
    """Gemini Implementation of LLMBase supporting both WebAPI and Official API"""
    
    def __init__(self, service_config: dict = None):
        self.config = load_config()
        
        self.service_config = service_config or {}
        
        # Determine provider and model from service config or global default
        if self.service_config:
            self.provider = self.service_config.get('provider', 'gemini_web')
            self.default_model_key = normalize_gemini_model_key(
                self.service_config.get('model', 'Thinking'),
                default='Thinking',
            )
            self.use_browser = bool(self.service_config.get('use_browser', False))
        else:
            # Fallback to old global config if available, or defaults
            self.provider = self.config.get('llm_provider', 'gemini_web')
            self.default_model_key = normalize_gemini_model_key(
                self.config.get('gemini_model', 'Thinking'),
                default='Thinking',
            )
            self.use_browser = False

        # Resolve API Key based on provider
        providers = self.config.get('providers', {})
        provider_config = providers.get(self.provider, {})
        
        # API Key can be in provider config or global (legacy)
        self.api_key = provider_config.get('api_key') or self.config.get('gemini_api_key', '')
        # 官方 API 文本模型（config 可覆盖；2026-08-24 新账号用 gemini-3.6-flash）
        self.official_text_model = provider_config.get('text_model', '')
        
        # Initialize official API if needed
        if self.provider == 'gemini_official':
            official_genai = _load_official_genai()
            if not official_genai:
                print("❌ 'google-generativeai' not installed. Please install it.")
            elif not self.api_key:
                print("❌ API Key not provided for Gemini Official API.")
            else:
                official_genai.configure(api_key=self.api_key)

        # Keep one canonical alias set across config, gemini-webapi, and Gemini Web UI.
        # The current Gemini网页 exposes "快速 / 思考 / Pro(3.1 Pro)".
        # `gemini-webapi` still names the Pro enum `G_3_0_PRO`, but its embedded
        # mode id matches the live Web UI option. We normalize on `Flash / Thinking / Pro`.
        self.web_model_specs = get_gemini_web_model_specs()
        self.web_model_map = {
            key: spec.get('enum') or spec.get('webapi_name')
            for key, spec in self.web_model_specs.items()
        }
        if WebModel:
            self.web_model_map['Unspecified'] = WebModel.UNSPECIFIED

    def _get_web_model(self, model_key: str = None):
        """Resolve model key to Gemini Web Model enum or string"""
        key = normalize_gemini_model_key(model_key or self.default_model_key, default='Pro')
        
        # 1. Try Map
        if key in self.web_model_map and self.web_model_map[key]:
            return self.web_model_map[key]
            
        # 2. Try Default Fallback Candidates (Enum members)
        if WebModel:
            candidates = [
                'ADVANCED_FLASH', 'ADVANCED_PRO', 'ADVANCED_THINKING',
                'PLUS_FLASH', 'PLUS_PRO', 'PLUS_THINKING',
                'BASIC_FLASH', 'BASIC_PRO', 'BASIC_THINKING',
                'G_3_0_PRO',
                'G_3_0_FLASH',
                'G_3_0_FLASH_THINKING',
                'G_2_0_FLASH', 'GEMINI_2_0_FLASH_EXP',
                'G_1_5_PRO', 'GEMINI_1_5_PRO',
                'GEMINI_PRO',
            ]
            for c in candidates:
                if hasattr(WebModel, c):
                    return getattr(WebModel, c)
        
        # 3. Try to resolve via Model.from_name (for gemini-3.0-pro etc)
        try:
            return WebModel.from_name(key)
        except:
            pass
            
        # 4. Allow direct usage of explicit model strings when the package accepts them.
        if isinstance(key, str) and (key.startswith("gemini-") or key == "Thinking"):
             return key
             
        return None

    def _get_official_model_name(self, model_key: str = None) -> str:
        """Resolve model name for official API"""
        key = normalize_gemini_model_key(model_key or self.default_model_key, default='Pro')
        # Official Gemini API defaults.
        # As of March 23, 2026, Gemini 2.5 Pro / Flash are the current stable text
        # models. Gemini 1.5 model ids below were removed from docs and are no longer
        # valid defaults.
        mapping = {
            'Thinking': 'gemini-2.5-pro',
            'Pro': 'gemini-2.5-pro',
            'Flash': 'gemini-2.5-flash',
            'gemini-2.5-pro': 'gemini-2.5-pro',
            'gemini-2.5-flash': 'gemini-2.5-flash',
            # 2026-08-24: 新账号 2.5-flash 已下架(404)，官方推荐 3.6-flash
            'gemini-3.6-flash': 'gemini-3.6-flash',
            '3.6-flash': 'gemini-3.6-flash',
        }
        # config 指定 text_model 时优先（2026-08-24: 新账号必须用 gemini-3.6-flash）；
        # 但用户显式传了 gemini-* 模型名时尊重显式值。
        if self.official_text_model and not (isinstance(key, str) and key.startswith('gemini-')):
            return self.official_text_model
        return mapping.get(key, key if isinstance(key, str) and key.startswith('gemini-') else 'gemini-2.5-pro')

    def _get_image_model_candidates(self, model_key: str = None) -> list[str]:
        primary = normalize_gemini_model_key(
            model_key or self.default_model_key or "Thinking",
            default="Thinking",
        )
        fallback_orders = {
            "Thinking": ["Thinking", "Flash", "Pro"],
            "Flash": ["Flash", "Thinking", "Pro"],
            "Pro": ["Pro", "Flash", "Thinking"],
        }
        candidates = []
        for candidate in fallback_orders.get(primary, [primary, "Flash", "Pro", "Thinking"]):
            normalized = normalize_gemini_model_key(candidate, default=primary)
            if normalized not in candidates:
                candidates.append(normalized)
        return candidates

    @staticmethod
    def _pick_random_account():
        """Pick a random available Gemini account, spreading load."""
        import random
        try:
            from core.utils import browser_gemini
            available = [
                a for a, s in browser_gemini._account_pool.items()
                if browser_gemini._account_is_available(s)
            ]
            if available:
                chosen = random.choice(available)
                if chosen != browser_gemini._account_index:
                    browser_gemini._account_index = chosen
                    log.info(f"🎲 Random account pick: authuser={chosen}")
                return chosen
        except Exception:
            pass
        return None

    @async_retry(max_retries=1, base_delay=2, exceptions=(Exception,))
    async def generate_text(self, prompt: str, model: str = None) -> str:
        """Generate text using selected provider.

        2026-08-24: gemini_official 失败(无 key/404/429/网络等)自动回退 gemini_web，
        保证文本任务不中断。
        """
        self._pick_random_account()
        log.info(f"Generating text with {self.provider}...")
        if self.provider == 'gemini_official' and genai:
            try:
                return await self._generate_text_official(prompt, model)
            except Exception as e:
                log.warning(f"⚠️ gemini_official 文本失败，回退 gemini_web: {str(e)[:140]}")
                return await self._generate_text_web(prompt, model)
        else:
            return await self._generate_text_web(prompt, model)

        
    async def _try_browser_generation(self, prompt: str, input_image_path: str, output_path: str, model: str = None) -> Optional[str]:
        """Helper to attempt browser-based generation"""
        # 2026-08-26 (4th cookie-loss 防护): 先确认 ego CDP 端口可达，否则
        # browser_gemini 的 connect_over_cdp 会失败；这里提前跳过避免噪音。
        try:
            sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
            from browser_registry import ego_cli_reachable
            if not ego_cli_reachable():
                log.warning("⏱️ ego CDP 不可达，跳过 Gemini 浏览器出图（不调 CLI）")
                return None
        except Exception:
            pass
        try:
            from core.utils.browser_gemini import generate_image_browser
            browser_model = normalize_gemini_model_key(
                model or self.default_model_key or 'Thinking',
                default='Thinking',
            )
            browser_timeout = float(self.service_config.get('browser_timeout_seconds', 150))
            log.info(f"🌐 Browser-based generation with model: {browser_model}...")
            result = await asyncio.wait_for(
                generate_image_browser(
                    prompt=prompt,
                    input_image_path=input_image_path,
                    output_path=output_path,
                    model=browser_model
                ),
                timeout=browser_timeout,
            )
            if result == "TEXT_ONLY_RESPONSE":
                log.warning("⚠️ Browser-based generation returned a text-only Gemini response; no image artifact was produced.")
                return None
            if result == "REQUEST_FAILED":
                log.warning("⚠️ Browser-based generation hit an explicit Gemini image-edit failure page.")
                return None
            if result == "SEND_NOT_STARTED":
                log.warning("⚠️ Browser-based generation never dispatched the prompt in Gemini.")
                return None
            if result and os.path.exists(result):
                log.info(f"✅ Browser-based generation succeeded: {result}")
                return result
            if result:
                log.warning(f"⚠️ Browser-based generation returned a non-file result: {result}")
            else:
                log.warning("⚠️ Browser-based generation finished without producing an image file.")
        except asyncio.TimeoutError:
            log.warning("⚠️ Browser-based generation timed out before producing an image artifact.")
        except ValueError as e:
            if str(e).startswith("MODEL_UNAVAILABLE:"):
                raise
            log.warning(f"⚠️ Browser-based generation failed: {e}.")
        except Exception as e:
            log.warning(f"⚠️ Browser-based generation failed: {e}.")
        return None

    @async_retry(max_retries=1, base_delay=5, exceptions=(Exception,))
    async def generate_image(self, prompt: str, input_image_path: str, output_path: str, model: str = None) -> Optional[str]:
        """Generate/Edit image using selected provider.
        
        Strategy (configurable via 'use_browser' flag):
        - use_browser=False (default): Web API First -> Browser Fallback
        - use_browser=True: Browser First -> Web API Fallback
        """
        log.info(f"Generating image with {self.provider}...")
        self._pick_random_account()

        if self.provider == 'gemini_official' and genai:
            return await self._generate_image_official(prompt, input_image_path, output_path, model)
        
        strategy_label = "Browser -> Web API" if self.use_browser else "Web API -> Browser"
        log.info(f"🧭 Image generation strategy order: {strategy_label}")
        strategies = (
            [
                ('Browser', self._try_browser_generation),
                ('Web API', lambda p, i, o, m: self._generate_image_web(p, i, o, model=m)),
            ]
            if self.use_browser else
            [
                ('Web API', lambda p, i, o, m: self._generate_image_web(p, i, o, model=m)),
                ('Browser', self._try_browser_generation),
            ]
        )

        model_candidates = self._get_image_model_candidates(model)
        log.info(f"🧠 Image generation model order: {' -> '.join(model_candidates)}")
        last_error = None

        for candidate_model in model_candidates:
            log.info(f"🎯 Trying image model candidate: {candidate_model}")
            skip_to_next_model = False

            for name, func in strategies:
                try:
                    log.info(f"🚀 Attempting Strategy: {name} ({candidate_model})...")
                    result = await func(prompt, input_image_path, output_path, candidate_model)
                    if result:
                        return result
                    log.warning(
                        f"⚠️ Strategy {name} ({candidate_model}) produced no usable image output."
                    )
                except Exception as e:
                    last_error = e
                    if str(e).startswith("MODEL_UNAVAILABLE:"):
                        log.warning(
                            f"⚠️ Strategy {name} reported {candidate_model} unavailable. Falling back to the next model candidate."
                        )
                        skip_to_next_model = True
                        break
                    log.warning(f"⚠️ Strategy {name} ({candidate_model}) failed: {e}. Moving to next...")

            if skip_to_next_model:
                continue

        if last_error:
            log.warning(f"⚠️ All image generation strategies exhausted. Last error: {last_error}")
        else:
            log.warning("⚠️ All image generation strategies exhausted without a usable image output.")
        return None

    # ---------------------------------------------------------
    # Web API Implementation (Legacy)
    # ---------------------------------------------------------
    async def _generate_text_web(self, prompt: str, model: str = None) -> str:
        # Priority 1: Legacy gemini-webapi
        if get_web_client:
            try:
                client = await get_web_client()
                if client:
                    await _auto_clean_old_chats(client)
                    effective_model = normalize_gemini_model_key(
                        model or self.default_model_key or "Pro",
                        default='Pro',
                    )
                    selected_model = self._get_web_model(model or effective_model)
                    print_t('cover_model', effective_model, "Web API")
                    
                    response = await generate_content_compat(client, prompt, model=selected_model)
                    _log_chat_id(response)
                    
                    log_content = f"📄 Text Response (Preview): {str(response.text)[:100]}..."
                    if hasattr(response, 'thoughts'):
                        log_content += f"\n🧠 Text Thoughts: {str(response.thoughts)[:200]}..."
                    else:
                        log_content += f"\n❓ No 'thoughts' attribute in text response."
                    log.info(log_content)

                    if response.text:
                        # Strip Gemini web UI conversational prefixes
                        text = response.text.strip()
                        for prefix in ["Gemini 说", "Gemini says", "Gemini wrote", "Gemini：", "Gemini: "]:
                            if text.startswith(prefix):
                                text = text[len(prefix):].strip()
                                break
                        return text
            except Exception as e:
                log.warning(f"⚠️ Web API Text Gen failed: {e}.")

        # Priority 2: Browser fallback. Text generation needs a working path even when
        # gemini-webapi is being rate-limited or SSL-blocked by Google risk checks.
        if self.provider == 'gemini_web':
            try:
                from core.utils.browser_gemini import generate_text_browser
                browser_model = normalize_gemini_model_key(
                    model or self.default_model_key or "Flash",
                    default='Flash',
                )
                log.info(f"🌐 Falling back to browser-based text generation with model: {browser_model}...")
                result = await generate_text_browser(prompt, model=browser_model)
                if result:
                    # Strip Gemini web UI conversational prefixes
                    text = result.strip()
                    for prefix in ["Gemini 说", "Gemini says", "Gemini wrote", "Gemini：", "Gemini: "]:
                        if text.startswith(prefix):
                            text = text[len(prefix):].strip()
                            break
                    return text
            except Exception as e:
                log.warning(f"⚠️ Browser Text Gen failed: {e}.")
        log.error("❌ Web API failed for text.")
        return ""


    async def _generate_image_web(self, prompt: str, input_image_path: str, output_path: str, model: str = None) -> Optional[str]:
        """Try gemini-webapi only. Browser fallback is orchestrated by generate_image()."""
        if get_web_client:
            try:
                web_timeout = float(self.service_config.get('web_timeout_seconds', 45))
                effective_model = normalize_gemini_model_key(
                    model or self.default_model_key or "Thinking",
                    default='Thinking',
                )
                print_t('cover_model', effective_model, "Web API")
                client = await get_web_client()
                if client:
                    await _auto_clean_old_chats(client)
                    # Use configured model (from config.yaml -> services.cover.model)
                    use_model = self._get_web_model(model or effective_model)
                    
                    files_to_upload = [input_image_path] if input_image_path else []
                    
                    log.info(f"Generating image with model: {use_model}, files: {len(files_to_upload)}...")

                    # ── 2026-09-26 关键修复 ──
                    # gemini_webapi 1.0.1 的 generate_content payload 硬编码为
                    # [prompt] / [prompt,0,None,[[[url,1]]]]，**没有 model 字段**
                    # → BiYou 的 Flash/Thinking/Pro 档位全部无效（永远走默认档）。
                    #
                    # 曾经尝试用 `_generate_image_nano_banana` 手写 payload 注入模型
                    # （idx17/idx49），但 2026-09-26 实测该路径**已彻底失效**：
                    # 参考图上传成功后，手写 payload 必定被 Google 400 拒
                    # （错误从 upload 400 变成 StreamGenerate 400，说明上传 OK、
                    #   payload 格式不被接受）。这套逆向格式随 Google 前端改版而过时。
                    #
                    # 现在：**直接走库原生 generate_content**（含文件上传，
                    # 走已修复的 upload_file 补丁 + access_token 补丁）。
                    # 模型档位丢失是可接受的代价 —— 出图成功率远比档位重要。
                    # 若日后需要档位，应在 gemini_webapi 侧升级而非继续手写 payload。
                    response = None

                    # Standard path（库原生，唯一路径）
                    if response is None:
                        response = await asyncio.wait_for(
                            generate_content_compat(
                                client,
                                prompt,
                                files=files_to_upload,
                                model=use_model
                            ),
                            timeout=web_timeout
                        )
                    _log_chat_id(response)

                    if response.images:
                        out_dir = os.path.dirname(output_path)
                        if out_dir:
                            os.makedirs(out_dir, exist_ok=True)
                            
                        save_dir = out_dir or "."
                        
                        out_name = os.path.basename(output_path)
                        await response.images[0].save(path=save_dir, filename=out_name, verbose=False)
                        
                        log_content = f"📄 Response Text Preview: {str(response.text)[:200]}..."
                        log.info(log_content)
                        return output_path
                    text_preview = str(getattr(response, 'text', '') or '').strip()
                    if text_preview:
                        log.warning(f"⚠️ Web API returned text-only response instead of image: {text_preview[:200]}...")
                        # 2026-08-15: Gemini image quota exhausted message. Detect limit text and
                        # rotate the browser account pool so the Browser fallback starts on a fresh account.
                        _limit_hints = [
                            "can create more images", "limit resets", "check your usage",
                            "as soon as your limit", "usage in settings", "达到上限",
                            "can't create more images", "can't generate more images",
                            "limit reached", "exceeded your quota",
                        ]
                        tl = text_preview.lower()
                        if any(h in tl for h in _limit_hints):
                            log.warning("⚠️ Web API image quota detected — rotating Gemini account for browser fallback.")
                            try:
                                from core.utils import browser_gemini
                                browser_gemini._mark_account_state(
                                    browser_gemini._account_index,
                                    valid=True,
                                    status="limited_until",
                                    cooldown_until=None,
                                    reason="Web API image quota exhausted",
                                )
                                if browser_gemini.rotate_gemini_account():
                                    log.info(f"🔄 Rotated to Gemini authuser={browser_gemini._account_index} for browser fallback.")
                            except Exception as e:
                                log.warning(f"⚠️ Account rotation on Web API limit failed: {e}.")
                    else:
                        log.warning("⚠️ Web API returned no images and no useful text response.")
            except asyncio.TimeoutError:
                log.warning(f"⚠️ Web API Image Gen timed out after {web_timeout:.0f}s.")
            except Exception as e:
                log.warning(f"⚠️ Web API Image Gen failed: {e}.")

        return None

    async def _generate_image_nano_banana(self, client, prompt: str, model: str, files: list):
        """
        Helper method to generate image using the 'Nano Banana Pro' payload structure.
        This manually constructs the request to inject key indices (idx17, idx49) that fix Chinese rendering.
        """
        try:
            from gemini_webapi.constants import Endpoint
            # 2026-09-26 修复: gemini_webapi 1.0.1 的 utils.upload_file 是坏的
            # ① @validate_call 声明 proxies: dict 但传字符串 -> ValidationError
            # ② 函数体引用的 Endpoint/Headers/AsyncClient 未 import -> NameError
            # => 参考图上传 100% 失败 ("1 validation error for upload_file")，
            #    导致 Nano Banana Pro 手动 payload 通道（中文渲染修复路径）全灭。
            # 改用补丁版（行为等价 + 可用的实现）。
            from core.utils.gemini_upload_patch import upload_file
            from gemini_webapi.utils import parse_file_name, extract_json_from_response
            from gemini_webapi.types import Candidate, ModelOutput
            import orjson
            import json

            # 1. Upload File first (we only handle 1 file for now for this patch)
            file_url_list = []
            if files:
                # Re-use client's proxy/auth for upload
                # 2026-09-26 修复: 库属性名是 `proxies`（client.py:120 `self.proxies = proxy`），
                # 原代码取 client.proxy → 永远是 None，proxy 配置被静默丢弃。
                proxy = getattr(client, 'proxies', None) or getattr(client, 'proxy', None)
                uploaded_url = await upload_file(files[0], proxy)
                log.info(f"File uploaded for patch: {uploaded_url[:50]}...")
                file_url_list = [[uploaded_url], parse_file_name(files[0])]

            # 2. Build Payload
            # Reverting to the 3-element structure that successfully reached the backend (albeit garbled previously).
            # We suspect missing f.sid was the cause of the fallback.
            
            # File Struct: [[ [url], filename ]]
            file_struct = [[file_url_list[0], file_url_list[1]]] if file_url_list else []
            
            # Inner Payload: [prompt, 0, None, [file_struct]]
            inner_payload = [
                prompt,
                0,
                None,
                [file_struct] if file_struct else []
            ]
            
            # Apply Patch: Pad and Inject if Thinking model
            if "thinking" in str(model).lower():
                # Pad to reach idx17
                while len(inner_payload) < 17:
                    inner_payload.append(None)
                inner_payload.append([[1]])  # idx17 = [[1]]
                
                # Pad to reach idx49 (Wait, idx49 is in REQUEST payload in my original patch?)
                # Correct: idx49 is in the ENVELOPE (request_payload) for the 69-element version.
                # For 3-element version, where does it go?
                # Original patch had it appended to inner_payload?
                # Let's check original code: "inner_payload.append(14)  # idx49"
                # Yes, I will restore that.
                
                while len(inner_payload) < 49:
                    inner_payload.append(None)
                inner_payload.append(14)  # idx49 = 14
            
            # Request Envelope: [inner, None, None]
            request_payload = [
                inner_payload,
                None,
                None,
            ]
            
            # Wrap in f.req
            import json
            data = {
                "at": client.access_token,
                "f.req": json.dumps([
                    None,
                    json.dumps(request_payload),
                ])
            }
            
            # 3. Send Request
            # CRITICAL FIX: Add Session ID (f.sid) to params
            # Manual params construction since client.params might be internal or missing
            request_params = {
                "_reqid": getattr(client, "_reqid", "1234"), # Fallback or access internal? 
                # Actually client.py generates _reqid per request usually.
                # Let's just use minimal params: rt=c
                "rt": "c",
            }
            if client.session_id:
                request_params["f.sid"] = client.session_id
                
            log.info(f"🍌 Sending Nano Banana Payload (Model: {model}) with f.sid={client.session_id[:10] if client.session_id else 'None'}...")
            
            response = await client.client.post(
                Endpoint.GENERATE.value,
                headers=client.client.headers, # Use client headers!
                params=request_params, # Use params with f.sid!
                data=data,
                timeout=300.0,
            )
            
            # (Duplicate block removed)
            
            response.raise_for_status()
            
            # Debug: Dump raw response to file
            try:
                with open("/tmp/nano_banana_debug.txt", "w") as f:
                    f.write(response.text)
                log.info("🍌 Raw response dumped to /tmp/nano_banana_debug.txt")
            except:
                pass

            # 4. Parse Response to mimic ModelOutput
            # We need to extract images and text to return a compatible object
            response_json = extract_json_from_response(response.text)
            
            candidates = []
            images_found = []
            
            # Minimal parsing logic just to extract images
            for part in response_json:
                if isinstance(part, list) and len(part) > 2:
                    part_body = part[2] if isinstance(part, list) and len(part) > 2 else None
                    if part_body:
                        try:
                            if isinstance(part_body, str):
                                part_json = json.loads(part_body)
                            else:
                                part_json = part_body
                            
                            # Extract Images (idx4)
                            idx4 = part_json[4] if isinstance(part_json, list) and len(part_json) > 4 else None
                            if idx4:
                                for candidate_data in idx4:
                                    # Try various paths for image URL
                                    # Path 1: Standard [12, 7, 0, 0, 0, 3, 3]
                                    img_url = candidate_data[12][7][0][0][0][3][3] if isinstance(candidate_data, list) and len(candidate_data) > 12 and isinstance(candidate_data[12], list) and len(candidate_data[12]) > 7 and isinstance(candidate_data[12][7], list) and len(candidate_data[12][7]) > 0 and isinstance(candidate_data[12][7][0], list) and len(candidate_data[12][7][0]) > 0 and isinstance(candidate_data[12][7][0][0], list) and len(candidate_data[12][7][0][0]) > 0 and isinstance(candidate_data[12][7][0][0][0], list) and len(candidate_data[12][7][0][0][0]) > 3 and isinstance(candidate_data[12][7][0][0][0][3], list) and len(candidate_data[12][7][0][0][0][3]) > 3 else None
                                    
                                    # Path 2: Checking for base64 or other structures
                                    if not img_url:
                                         img_url = candidate_data[12][0][0][3][3] if isinstance(candidate_data, list) and len(candidate_data) > 12 and isinstance(candidate_data[12], list) and len(candidate_data[12]) > 0 and isinstance(candidate_data[12][0], list) and len(candidate_data[12][0]) > 0 and isinstance(candidate_data[12][0][0], list) and len(candidate_data[12][0][0]) > 0 and isinstance(candidate_data[12][0][0][3], list) and len(candidate_data[12][0][0][3]) > 3 else None
                                    
                                    if img_url:
                                        # Create Image object compatible with gemini_webapi
                                        class StartImage:
                                            def __init__(self, url, title):
                                                self.url = url
                                                self.title = title
                                                
                                            async def save(self, path=None, filename=None, verbose=True):
                                                import aiofiles
                                                log.info(f"🍌 Downloading from: {self.url[:50]}...")
                                                
                                                # Prepare headers
                                                headers = {
                                                    "User-Agent": client.client.headers.get("User-Agent", "Mozilla/5.0"),
                                                    "Referer": "https://gemini.google.com/",
                                                }
                                                
                                                # Try Python Client first
                                                for attempt in range(3):
                                                    try:
                                                        if self.url.startswith("data:image"):
                                                            import base64
                                                            header, encoded = self.url.split(",", 1)
                                                            data = base64.b64decode(encoded)
                                                            if path and filename:
                                                                full_path = os.path.join(path, filename)
                                                                async with aiofiles.open(full_path, "wb") as f:
                                                                    await f.write(data)
                                                                return full_path
                                                            return None

                                                        image_resp = await client.client.get(self.url, headers=headers, follow_redirects=True)
                                                        if image_resp.status_code == 200:
                                                            if path and filename:
                                                                full_path = os.path.join(path, filename)
                                                                async with aiofiles.open(full_path, "wb") as f:
                                                                    await f.write(image_resp.content)
                                                                return full_path
                                                        log.warning(f"🍌 Py Download attempt {attempt+1} failed: {image_resp.status_code}")
                                                        await asyncio.sleep(1)
                                                    except Exception as e:
                                                        log.warning(f"🍌 Py Download attempt {attempt+1} error: {e}")
                                                
                                                # Fallback: CURL (sometimes works better with complex auth/IP checks or specific TLS fingerprints?)
                                                log.info("🍌 Attempting Curl Fallback...")
                                                if path and filename:
                                                    full_path = os.path.join(path, filename)
                                                    
                                                    # Extract cookies from client
                                                    cookie_header = ""
                                                    if hasattr(client.client, "cookies"):
                                                        # httpx.Cookies to string
                                                        cookie_header = "; ".join([f"{k}={v}" for k, v in client.client.cookies.items()])
                                                    
                                                    # Added -L to follow redirects (Crucial for lh3 -> work.fife redirects)
                                                    # Added --cookie for auth
                                                    cmd = f"curl -L -s -o '{full_path}' '{self.url}'"
                                                    if cookie_header:
                                                        # Escape double quotes if necessary, but usually cookie values are safe-ish
                                                        cmd += f" --cookie '{cookie_header}'"
                                                    
                                                    proc = await asyncio.create_subprocess_shell(cmd)
                                                    await proc.communicate()
                                                    if proc.returncode == 0 and os.path.exists(full_path) and os.path.getsize(full_path) > 1000:
                                                        log.info(f"✅ Curl Download succeeded: {full_path}")
                                                        return full_path
                                                    else:
                                                        if os.path.exists(full_path):
                                                            size = os.path.getsize(full_path)
                                                            log.warning(f"⚠️ Curl downloaded file is too small ({size} bytes). Likely an error page.")
                                                
                                                raise Exception(f"Failed to download image from {self.url[:30]}... (Py+Curl)")
                                        
                                        images_found.append(StartImage(url=img_url, title=prompt))
                                        log.info(f"🍌 Found generated image: {img_url[:60]}...")
                                        
                        except Exception as parse_err:
                            log.warning(f"Error parsing part: {parse_err}")
                            pass
                            
            # Regex fallback: Look for gg-dl URLs in the entire response text if JSON parsing missed it
            if not images_found:
                 import re
                 # Specific regex for the double-underscore gg-dl links seen in the dump
                 # https://lh3.googleusercontent.com/gg-dl/AOI_...
                 urls = re.findall(r'"(https://lh3\.googleusercontent\.com/gg-dl/[^"]+)"', response.text)
                 for u in urls:
                     if u not in [img.url for img in images_found]:
                         images_found.append(StartImage(url=u, title=prompt))
                         log.info(f"🍌 Found image via REGEX (gg-dl): {u[:60]}...")

            # Create a synthetic ModelOutput
            candidates.append(Candidate(rcid="patch", text="[Nano Banana Pro] Image generation successful."))
            
            # Use a custom class that mimics ModelOutput but allows setting images
            class PatchModelOutput:
                def __init__(self, metadata, candidates, images=None, text=""):
                    self.metadata = metadata
                    self.candidates = candidates
                    self.images = images or []
                    self.text = text
                    
            model_output = PatchModelOutput(
                metadata=[], 
                candidates=candidates, 
                images=images_found,
                text=candidates[0].text
            )
            
            if not images_found:
                log.warning("🍌 Nano Banana request succeeded but no images found in response.")
                
            return model_output

        except Exception as e:
            log.error(f"❌ Nano Banana Patch failed: {e}")
            raise e

    # ---------------------------------------------------------
    # Official API Implementation
    # ---------------------------------------------------------
    async def _generate_text_official(self, prompt: str, model: str = None) -> str:
        model_name = self._get_official_model_name(model)
        try:
            # Official API is synchronous primarily, but we can wrap or use async if available in newer versions
            # For now, running sync in thread or just sync is fine for text
            # google-generativeai v0.3+ supports async via specialized clients, 
            # but standard GenerativeModel is sync. Let's use to_thread for non-blocking.
            
            def _run():
                log.debug(f"Calling Gemini Official with prompt length: {len(prompt)}")
                m = genai.GenerativeModel(model_name)
                response = m.generate_content(prompt)
                return response.text
                
            return await asyncio.to_thread(_run)
        except Exception as e:
            print(f"❌ Gemini Official text generation failed: {e}")
            raise

    async def _translate_cover_text(self, client, context_text: str) -> str:
        """
        Uses Flash model (via Chat) to extract and translate keywords for the cover.
        Returns a formatted string of mandatory translations.
        """
        try:
            # Use Flash model for intelligence (Brain)
            # gemini_webapi 1.0.1+ 移除 constants.Model → None 走默认模型
            try:
                from gemini_webapi.constants import Model
            except ImportError:
                Model = None
            chat_model = getattr(Model, 'G_3_0_FLASH', None) if Model else None
            
            prompt = f"""
Role: Professional Translator & Layout Planner.
Task: Analyze the following Video Title/Context and extract key English terms that should be on the cover.
Then, provide their direct Chinese translations.

Context: "{context_text}"

Requirements:
1. First infer the video's professional domain and follow that domain's own standard terminology.
2. Preserve proper nouns, brand names, program names, framework names, feature names, and acronyms in English when they are normally kept in original form in that domain.
3. If a term has a standard Chinese translation in that field, use that exact translation consistently. Do not replace domain terminology with platform slang.
4. For fitness content, keep canonical method names such as HIIT, Tabata, EMOM, and AMRAP in original form unless the source itself already uses a standard Chinese name. Only localize supporting descriptors like "standing", "no jumping", "low impact", and "fat burn".
5. Identify **ALL** Numbers, Units (MIN, HR), and Action Verbs.
6. Translate them into concise Chinese only when translation is appropriate for the domain and still fits a thumbnail.
7. **CRITICAL**: Translate "X MIN" to "X分钟".
8. Format Output as a JSON map: {{"English": "Chinese"}}

Example:
Context: "40 MIN Kickboxing HIIT"
Output: {{"40 MIN": "40分钟", "Kickboxing": "自由搏击", "HIIT": "高强间歇"}}
            """
            
            response = await generate_content_compat(client, prompt, model=chat_model)
            _log_chat_id(response)
            if response and response.text:
                import json
                import re
                
                # Extract JSON
                match = re.search(r'\{.*\}', response.text, re.DOTALL)
                if match:
                    json_str = match.group(0)
                    translations = json.loads(json_str)

                    # Format into instruction
                    # 2026-09-20 修复「生成封面被当成翻译任务」：
                    # 原文案是 "MANDATORY TRANSLATIONS (Overrides all else): You MUST write ..."
                    # —— "Overrides all else"(覆盖一切) + "You MUST write" 把 Gemini 的意图
                    # 从「出图」拉到「执行翻译规则」，于是它回一段文字方案、不出图，
                    # 脚本等图超时(实测 343s) → gen-no。抽象词条(如 38 YEARS→38年)尤其易触发。
                    # 改为：明确限定这些词只用于"图上可见文字"，且不改变出图这个主任务。
                    instruction = (
                        "\n**TEXT SUBSTITUTION GLOSSARY (applies to visible text in the image ONLY):**\n"
                        "This glossary does NOT change the main task: you must still return an edited IMAGE, "
                        "never a text-only answer.\n"
                    )
                    for en, cn in translations.items():
                        instruction += f"- If the image contains the visible text '{en}', render it as '{cn}'.\n"

                    log.info(f"🧠 Flash Translation Plan: {json_str}")
                    return instruction

            
            return ""
        except Exception as e:
            log.warning(f"⚠️ Translation Step Failed: {e}")
            return ""

    async def _generate_image_official(self, prompt: str, input_image_path: str, output_path: str, model: str = None) -> Optional[str]:
        # Note: Image editing/generation via Official API has different capabilities/endpoints
        # Currently, Gemini Pro Vision is multimodel input (Text+Image -> Text).
        # It does NOT natively support "Edit Image" (Text+Image -> Image) in the same way the Web App does (Imagen 3 integration).
        # The official API for Image Generation (Imagen) is separate.
        # Fallback to Web API if this feature is not supported or warn user.
        
        print("⚠️ Warning: Official Gemini API currently mainly supports Text/Multimodal -> Text.")
        print("   Image Editing/Generation might not be fully supported in the standard free tier API in the same way as the Web App.")
        print("   Falling back to Web API for image generation task...")
        
        return await self._generate_image_web(prompt, input_image_path, output_path, model)
