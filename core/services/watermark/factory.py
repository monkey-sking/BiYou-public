from core.utils.config import load_config
from .hybrid import HybridWatermarkService
from .local_rules_based import LocalRulesBasedWatermarkService
from .reverse_alpha import ReverseAlphaWatermarkService
# from .online_service import OnlineWatermarkService # Placeholder

class WatermarkServiceFactory:
    @staticmethod
    def get_service():
        config = load_config()
        service_type = config.get('watermark_service', 'hybrid').lower()
        
        if service_type in ('hybrid', 'auto'):
            return HybridWatermarkService()
        elif service_type == 'reverse_alpha':
            return ReverseAlphaWatermarkService()
        elif service_type == 'local_rules':
            return LocalRulesBasedWatermarkService()
        elif service_type == 'online':
            raise NotImplementedError("Online watermark service not implemented yet")
        else:
            # Default fallback
            return HybridWatermarkService()
