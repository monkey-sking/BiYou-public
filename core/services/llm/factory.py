from typing import Optional
from core.services.llm.base import LLMBase
from core.services.llm.gemini_service import GeminiService
from core.utils.config import load_config

class LLMFactory:
    """Factory to get the configured LLM service"""
    _service_cache: dict[tuple[str, str], LLMBase] = {}

    @staticmethod
    def build_for_name(provider: str, service_config: Optional[dict] = None) -> LLMBase:
        """按 provider 名字显式构造服务(绕过缓存与 config 默认), 用于快速降级/切换。
        2026-08-20: ImageTranslationService 用它跳过连续失败的 jimeng 直接走 fallback。"""
        service_config = service_config or {}
        if provider == 'jimeng':
            from core.services.llm.jimeng_service import JimengService
            return JimengService(service_config)
        if provider == 'tongyi':
            from core.services.llm.tongyi_service import TongyiService
            return TongyiService(service_config)
        if provider == 'hunyuan':
            from core.services.llm.hunyuan_service import HunyuanService
            return HunyuanService(service_config)
        if provider == 'doubao':
            from core.services.llm.doubao_service import DoubaoService
            return DoubaoService(service_config)
        if provider == 'gemini_web_v2':
            from core.services.llm.gemini_service_v2 import GeminiServiceV2
            return GeminiServiceV2(service_config)
        if provider == 'gemini_cli':
            # 2026-09-14: Gemini 网页版走 ego CLI 原生通道（不依赖已废弃的 CDP 39222）
            from core.services.llm.gemini_cli_service import GeminiCliService
            return GeminiCliService(service_config)
        if provider == 'chatgpt_cli':
            # 2026-09-18: ChatGPT 网页版出图，同样走 ego CLI 原生通道。
            # 实测中文标题质量优于 jimeng/gemini_cli。
            from core.services.llm.chatgpt_cli_service import ChatgptCliService
            return ChatgptCliService(service_config)
        return GeminiService(service_config)

    @staticmethod
    def get_service(task_type: str = 'translation') -> LLMBase:
        """
        Get the configured LLM service instance for a specific task.
        
        Args:
            task_type (str): 'translation', 'cover', 'analysis', etc.
        """
        config = load_config()
        
        # Get service specific config
        # Default to translation if task not found or old config structure
        services = config.get('services', {})
        service_config = services.get(task_type, {})
        
        # Fallback for old config structure (flat keys) if 'services' missing
        if not service_config:
            # Fallback: Check if task_type exists at root level (Handling flat config structure)
            service_config = config.get(task_type, {})
            
        provider = service_config.get('provider', config.get('llm_provider', 'gemini_web'))
        model = service_config.get('model', config.get('gemini_model', 'Thinking'))
        use_browser = service_config.get('use_browser')
        cache_key = (task_type, f"{provider}:{model}:browser={use_browser}")
        cached = LLMFactory._service_cache.get(cache_key)
        if cached:
            return cached

        if provider == 'jimeng':
            # 即梦 AI 浏览器路径 (每日免费积分, 积分不足时由上层回退)
            from core.services.llm.jimeng_service import JimengService
            service = JimengService(service_config)
        elif provider == 'tongyi':
            # 通义万相浏览器路径 (每日签到免费灵感值, 积分不足时由上层回退)
            from core.services.llm.tongyi_service import TongyiService
            service = TongyiService(service_config)
        elif provider == 'hunyuan':
            # 腾讯混元浏览器路径 (微信登录, 图像指令编辑)
            from core.services.llm.hunyuan_service import HunyuanService
            service = HunyuanService(service_config)
        elif provider == 'doubao':
            # 豆包/Seedream 浏览器路径 (飞书/抖音登录, 对话式图像编辑)
            from core.services.llm.doubao_service import DoubaoService
            service = DoubaoService(service_config)
        elif provider == 'gemini_web_v2':
            # gemini_webapi 2.0.0 独立后端 (子进程隔离, vendor/gemini_webapi2)
            from core.services.llm.gemini_service_v2 import GeminiServiceV2
            service = GeminiServiceV2(service_config)
        elif provider == 'gemini_cli':
            # 2026-09-14: Gemini 网页版走 ego CLI 原生通道（替代废弃的 CDP 39222 路线）
            from core.services.llm.gemini_cli_service import GeminiCliService
            service = GeminiCliService(service_config)
        else:
            service = GeminiService(service_config)
        LLMFactory._service_cache[cache_key] = service
        return service
