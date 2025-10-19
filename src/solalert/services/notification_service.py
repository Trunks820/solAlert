"""
通知服务
统一管理各种通知渠道（微信、Telegram 等）
"""
import logging
from typing import Optional

from ..notifiers.wechat import WeChatNotifier

logger = logging.getLogger(__name__)


class NotificationService:
    """通知服务"""
    
    def __init__(self):
        """初始化通知服务"""
        self.wechat = WeChatNotifier()
    
    def send_alert(self, message: str, target: str = None) -> bool:
        """
        发送预警通知
        
        Args:
            message: 通知内容
            target: 目标（可选）
            
        Returns:
            是否发送成功
        """
        try:
            # 使用同步方法发送
            success = self.wechat.send_sync(target=target, message=message)
            
            if success:
                logger.info(f"✅ 预警通知已发送")
            else:
                logger.warning(f"⚠️  预警通知发送失败")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ 发送预警通知异常: {e}")
            return False
    
    async def send_alert_async(self, message: str, target: str = None) -> bool:
        """
        异步发送预警通知
        
        Args:
            message: 通知内容
            target: 目标（可选）
            
        Returns:
            是否发送成功
        """
        try:
            success = await self.wechat.send(target=target, message=message)
            
            if success:
                logger.info(f"✅ 预警通知已发送")
            else:
                logger.warning(f"⚠️  预警通知发送失败")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ 发送预警通知异常: {e}")
            return False

