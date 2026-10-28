from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class BaseService(ABC):
    """
    所有服务的基类
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    async def initialize(self) -> None:
        """初始化服务 (如加载模型、登录等)"""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """检查服务是否可用"""
        pass
