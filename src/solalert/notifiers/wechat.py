"""
微信通知器
调用微信API发送消息
"""
import requests
import logging
from typing import Optional

from .base import BaseNotifier
from ..core.config import WECHAT_CONFIG

logger = logging.getLogger(__name__)


class WeChatNotifier(BaseNotifier):
    """微信通知器"""
    
    def __init__(self, api_url: str = None, api_key: str = None, enabled: bool = None):
        """
        初始化微信通知器
        
        Args:
            api_url: 微信API地址
            api_key: 微信API密钥
            enabled: 是否启用，如果为None则使用配置文件
        """
        if enabled is None:
            enabled = WECHAT_CONFIG.get('enabled', True)
        
        super().__init__(enabled)
        self.api_url = api_url or WECHAT_CONFIG.get('api_url')
        self.api_key = api_key or WECHAT_CONFIG.get('api_key')
        self.default_target = WECHAT_CONFIG.get('default_target')
        
        logger.info(f"微信通知器初始化: {'启用' if self.enabled else '禁用'}")
    
    async def send(self, target: str = None, message: str = "", **kwargs) -> bool:
        """
        发送微信消息（异步接口）
        
        Args:
            target: 目标群组或用户ID
            message: 消息内容
            
        Returns:
            是否发送成功
        """
        # 调用同步方法
        return self.send_sync(target, message)
    
    def send_sync(self, target: str = None, message: str = "") -> bool:
        """
        发送微信消息（同步方法）
        
        Args:
            target: 目标群组或用户ID
            message: 消息内容
            
        Returns:
            是否发送成功
        """
        if not self.enabled:
            self.log_disabled()
            return False
        
        if not target:
            target = self.default_target
        
        try:
            logger.info(f"正在发送微信消息到 {target}")
            logger.debug(f"消息内容: {message[:100]}...")
            
            # 构建API请求
            response = self._call_wechat_api(target, message)
            
            if response and response.get("success"):
                self.log_success(target, message[:50])
                return True
            else:
                error_msg = response.get("error", "未知错误") if response else "API无响应"
                logger.error(f"微信API调用失败: {error_msg}")
                return False
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"无法连接到微信服务: {e}")
            logger.warning("请确保微信API服务正在运行")
            return False
        except requests.exceptions.Timeout as e:
            logger.error(f"调用微信服务超时: {e}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"微信API调用异常: {e}")
            return False
        except Exception as e:
            self.log_failure(target, e)
            return False
    
    def _call_wechat_api(self, target: str, message: str) -> dict:
        """
        调用微信API发送文本消息
        
        Args:
            target: 目标ID
            message: 消息内容
            
        Returns:
            API响应字典
        """
        try:
            # 构建请求数据
            payload = {
                "target": target,
                "message": message,
                "api_key": self.api_key
            }
            
            # 发送POST请求
            response = requests.post(
                f"{self.api_url}/send_text",
                json=payload,
                timeout=10
            )
            
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"微信API调用失败: {e}")
            return {"success": False, "error": str(e)}
    
    def update_api_url(self, api_url: str):
        """更新微信API URL"""
        self.api_url = api_url
        logger.info(f"微信API URL已更新: {api_url}")

