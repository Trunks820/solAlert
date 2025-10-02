"""
数据采集器基类
定义统一的采集接口
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """数据采集器抽象基类"""
    
    def __init__(self, name: str):
        """
        初始化采集器
        
        Args:
            name: 采集器名称
        """
        self.name = name
        self.is_running = False
    
    @abstractmethod
    async def start(self):
        """启动采集器"""
        pass
    
    @abstractmethod
    async def stop(self):
        """停止采集器"""
        pass
    
    def log_info(self, message: str):
        """记录信息日志"""
        logger.info(f"[{self.name}] {message}")
    
    def log_error(self, message: str, exception: Optional[Exception] = None):
        """记录错误日志"""
        if exception:
            logger.error(f"[{self.name}] {message}: {exception}")
        else:
            logger.error(f"[{self.name}] {message}")
    
    def log_success(self, message: str):
        """记录成功日志"""
        logger.info(f"[{self.name}] ✅ {message}")
    
    def log_warning(self, message: str):
        """记录警告日志"""
        logger.warning(f"[{self.name}] ⚠️  {message}")

