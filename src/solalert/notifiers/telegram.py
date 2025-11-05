"""
Telegram 通知器 (HTTP API 版本)
使用 HTTP API (kakarot8.fun:8000) 发送消息
"""
import asyncio
import logging
from typing import Optional
from telegram import InlineKeyboardMarkup

from .base import BaseNotifier
from ..core.config import TELEGRAM_CONFIG
from ..api.telegram_api import TelegramAPI

logger = logging.getLogger('solalert.notifiers.telegram')


class TelegramNotifier(BaseNotifier):
    """Telegram通知器（基于 HTTP API）"""
    
    def __init__(self, bot_token: str = None, enabled: bool = True):
        """
        初始化Telegram通知器
        
        Args:
            bot_token: Bot Token (兼容参数，HTTP API不需要)
            enabled: 是否启用
        """
        super().__init__(enabled)
        
        # HTTP API 配置
        self.api_url = TelegramAPI.API_BASE_URL
        self.api_key = TelegramAPI.API_KEY
        
        logger.info("✅ Telegram通知器初始化成功（HTTP API 模式）")
        logger.info(f"   API URL: {self.api_url}")
        logger.info(f"   API Key: {self.api_key[:10]}..." if self.api_key else "   ⚠️ 未配置 API Key")
    
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
            reply_markup: 按钮markup
            
        Returns:
            是否发送成功
        """
        if not self.enabled:
            self.log_disabled()
            return False
        
        try:
            logger.info(f"🚀 [TelegramHTTP] 发送消息 -> {target} | 消息长度={len(message)}")
            
            # 调用 HTTP API
            result = await TelegramAPI.send_message(
                chat_id=target,
                message=message,
                parse_mode=parse_mode,
                topic_id=topic_id,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            
            if result.get('success'):
                logger.info(
                    f"✅ [TelegramHTTP] 消息发送成功 -> {target} | "
                    f"message_id={result.get('message_id')}"
                )
                return True
            else:
                logger.error(
                    f"❌ [TelegramHTTP] 发送失败 -> {target} | "
                    f"错误: {result.get('error')}"
                )
                return False
                
        except Exception as e:
            logger.error(
                f"❌ [TelegramHTTP] 发送异常 -> {target} | "
                f"错误: {e}"
            )
            return False
    
    async def send_message(
        self,
        message: str,
        ca: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        发送消息（兼容旧接口）
        
        Args:
            message: 消息内容
            ca: 代币地址（用于生成按钮，可选）
            **kwargs: 其他参数
            
        Returns:
            是否发送成功
        """
        # 默认发送到 default 频道
        target = kwargs.pop('target', 'default')
        
        # 如果提供了 ca，可以在这里生成按钮（根据需要）
        reply_markup = kwargs.pop('reply_markup', None)
        
        return await self.send(
            target=target,
            message=message,
            reply_markup=reply_markup,
            **kwargs
        )
    
    def format_token_address(self, address: str, symbol: str = None) -> str:
        """
        格式化代币地址为可点击链接
        
        Args:
            address: 代币地址
            symbol: 代币符号（可选）
            
        Returns:
            HTML 格式的链接
        """
        display_text = symbol if symbol else f"{address[:6]}...{address[-4:]}"
        return f'<a href="https://solscan.io/token/{address}">{display_text}</a>'

