"""
通知器基类
定义统一的通知接口
"""
from abc import ABC, abstractmethod
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BaseNotifier(ABC):
    """通知器抽象基类"""
    
    def __init__(self, enabled: bool = True):
        """
        初始化通知器
        
        Args:
            enabled: 是否启用该通知器
        """
        self.enabled = enabled
        self.name = self.__class__.__name__
    
    @abstractmethod
    async def send(self, target: str, message: str, **kwargs) -> bool:
        """
        发送通知（异步）
        
        Args:
            target: 通知目标（群组ID、用户ID等）
            message: 通知消息内容
            **kwargs: 其他参数
            
        Returns:
            是否发送成功
        """
        pass
    
    def is_enabled(self) -> bool:
        """检查通知器是否启用"""
        return self.enabled
    
    def log_success(self, target: str, message_preview: str = ""):
        """记录发送成功日志"""
        preview = message_preview[:50] + "..." if len(message_preview) > 50 else message_preview
        logger.info(f"✅ [{self.name}] 发送成功 -> {target}: {preview}")
    
    def log_failure(self, target: str, error: Exception):
        """记录发送失败日志"""
        logger.error(f"❌ [{self.name}] 发送失败 -> {target}: {error}")
    
    def log_disabled(self):
        """记录通知器未启用日志"""
        logger.debug(f"⚠️ [{self.name}] 通知器未启用")

