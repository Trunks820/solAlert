"""
Telegram 通知器
基于 python-telegram-bot 库直接调用 Bot API
"""
import asyncio
import logging
from typing import Optional
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TelegramError

from .base import BaseNotifier
from ..core.config import TELEGRAM_CONFIG

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Telegram通知器（基于 Bot API）"""
    
    def __init__(self, bot_token: str = None, enabled: bool = True):
        """
        初始化Telegram通知器
        
        Args:
            bot_token: Bot Token
            enabled: 是否启用
        """
        super().__init__(enabled)
        self.bot_token = bot_token or TELEGRAM_CONFIG.get('bot_token')
        
        # 创建 Bot 实例，配置更大的连接池和超时
        if self.bot_token:
            from telegram.request import HTTPXRequest
            # 配置 HTTPXRequest：更大的连接池，更长的超时
            request = HTTPXRequest(
                connection_pool_size=20,  # 连接池大小
                connect_timeout=30.0,      # 连接超时
                read_timeout=30.0,         # 读取超时
                write_timeout=30.0,        # 写入超时
                pool_timeout=10.0          # 池超时
            )
            self.bot = Bot(token=self.bot_token, request=request)
        else:
            self.bot = None
            
        logger.info("✅ Telegram通知器初始化成功（Bot API 模式）")
        logger.info(f"   Bot Token: {self.bot_token[:20]}..." if self.bot_token else "   ⚠️ 未配置 Bot Token")
    
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
        发送Telegram消息（通过 Bot API）
        
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
        
        if not self.bot:
            logger.error("❌ Bot 未初始化")
            return False
        
        try:
            # 直接调用 Bot API 发送消息
            import time
            from telegram.error import RetryAfter, TimedOut, NetworkError
            
            logger.debug(f"📤 [Bot API] 发送消息 -> {target}")
            
            start = time.monotonic()
            result = await self.bot.send_message(
                chat_id=target,
                text=message,
                parse_mode=parse_mode,
                message_thread_id=topic_id,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            cost = time.monotonic() - start
            
            if result:
                logger.info(
                    f"✅ [TelegramNotifier] 消息发送成功 -> {target} | "
                    f"message_id={result.message_id} | 耗时={cost:.2f}s | "
                    f"thread_id={topic_id or 'None'} | buttons={bool(reply_markup)}"
                )
                return True
            else:
                logger.error(f"❌ [TelegramNotifier] 发送失败 -> {target}")
                return False
                
        except RetryAfter as e:
            logger.warning(
                f"⏳ [TelegramNotifier] 被 Telegram 限流 -> {target} | "
                f"重试等待={e.retry_after}s"
            )
            return False
        except TimedOut as e:
            logger.error(
                f"⌛ [TelegramNotifier] 请求超时 -> {target} | "
                f"错误详情: {e}"
            )
            return False
        except NetworkError as e:
            logger.error(
                f"🌐 [TelegramNotifier] 网络错误 -> {target} | "
                f"错误详情: {e}"
            )
            return False
        except TelegramError as e:
            logger.error(
                f"❌ [TelegramNotifier] Telegram错误 -> {target} | "
                f"错误类型: {type(e).__name__} | 详情: {e}"
            )
            return False
        except Exception as e:
            import traceback
            logger.error(
                f"❌ [TelegramNotifier] 发送异常 -> {target} | "
                f"错误类型: {type(e).__name__} | 详情: {e}"
            )
            logger.error(f"   堆栈跟踪:\n{traceback.format_exc()}")
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
