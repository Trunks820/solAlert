"""
Telegram 通知器
基于 HTTP API (http://kakarot8.fun:8000) 实现
"""
import asyncio
import logging
from typing import Optional
from telegram import InlineKeyboardMarkup

from .base import BaseNotifier
from ..core.config import TELEGRAM_CONFIG
from ..api.telegram_api import TelegramAPI

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Telegram通知器（基于HTTP API）"""
    
    def __init__(self, bot_token: str = None, enabled: bool = True):
        """
        初始化Telegram通知器
        
        Args:
            bot_token: Bot Token（保留参数以兼容旧代码，但不再使用）
            enabled: 是否启用
        """
        super().__init__(enabled)
        logger.info("✅ Telegram通知器初始化成功（HTTP API 模式）")
        logger.info(f"   API Base URL: {TelegramAPI.API_BASE_URL}")
        logger.info(f"   别名映射: {list(TelegramAPI.get_chat_aliases().keys())}")
    
    async def send(
        self,
        target: str,
        message: str,
        parse_mode: str = "HTML",
        topic_id: Optional[int] = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        **kwargs
    ) -> bool:
        """
        发送Telegram消息（通过 HTTP API）
        
        Args:
            target: 目标chat_id（群组ID/用户ID/别名）
            message: 消息内容
            parse_mode: 解析模式（HTML/Markdown）
            topic_id: 论坛主题ID（可选）
            reply_markup: 按钮markup（HTTP API 暂不支持，会被忽略）
            
        Returns:
            是否发送成功
        """
        if not self.enabled:
            self.log_disabled()
            return False
        
        try:
            # 调用 HTTP API 发送消息
            result = await TelegramAPI.send_message(
                chat_id=target,
                message=message,
                parse_mode=parse_mode,
                topic_id=topic_id,
                reply_markup=reply_markup,  # 会在 TelegramAPI 内部记录警告
                max_retries=3
            )
            
            if result.get('success'):
                self.log_success(target, message[:100])
                return True
            else:
                error_msg = result.get('error', 'unknown')
                logger.error(f"❌ [TelegramNotifier] 发送失败 -> {target}: {error_msg}")
                return False
                
        except Exception as e:
            import traceback
            logger.error(f"❌ [TelegramNotifier] 发送异常 -> {target}: {type(e).__name__} - {e}")
            logger.error(f"   详细错误: {traceback.format_exc()}")
            return False
    
    async def send_to_forum_topic(
        self,
        topic_id: int,
        message: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        """
        发送消息到论坛主题
        
        Args:
            topic_id: 论坛主题ID
            message: 消息内容
            parse_mode: 解析模式
            reply_markup: 按钮markup
            
        Returns:
            是否发送成功
        """
        forum_group_id = str(TELEGRAM_CONFIG.get('forum_group_id'))
        return await self.send(
            target=forum_group_id,
            message=message,
            parse_mode=parse_mode,
            topic_id=topic_id,
            reply_markup=reply_markup
        )
    
    async def send_to_channel(
        self,
        message: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        """
        发送消息到默认频道
        
        Args:
            message: 消息内容
            parse_mode: 解析模式
            reply_markup: 按钮markup
            
        Returns:
            是否发送成功
        """
        target_channel_id = str(TELEGRAM_CONFIG.get('target_channel_id'))
        return await self.send(
            target=target_channel_id,
            message=message,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
