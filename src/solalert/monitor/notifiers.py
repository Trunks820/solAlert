"""
通知服务
支持Telegram和微信通知
"""
import aiohttp
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from ..core.logger import get_logger
from ..core.config import TELEGRAM_CONFIG, WECHAT_CONFIG

logger = get_logger(__name__)


class NotificationMessage:
    """通知消息"""
    
    def __init__(
        self,
        ca: str,
        token_name: str,
        token_symbol: str,
        triggered_events: List[Dict[str, Any]],
        token_data: Optional[Dict[str, Any]] = None,
        remark: str = ""
    ):
        self.ca = ca
        self.token_name = token_name
        self.token_symbol = token_symbol
        self.triggered_events = triggered_events
        self.token_data = token_data or {}
        self.remark = remark
    
    def format_message(self) -> str:
        """格式化消息文本（Telegram HTML格式）"""
        lines = [
            "<b>🚨 监控触发提醒</b>",
            "",
            f"💰 代币: {self.token_name} ({self.token_symbol})",
            f"合约地址: <code>{self.ca}</code>",
            ""
        ]
        
        # 添加Token当前信息
        if self.token_data:
            price = self.token_data.get('usdPrice')
            mcap = self.token_data.get('mcap')
            holders = self.token_data.get('holderCount')
            liquidity = self.token_data.get('liquidity')
            
            if price:
                lines.append(f"💵 当前价格: ${price:.10f}")
            if mcap:
                lines.append(f"📊 市值: ${mcap:,.2f}")
            if holders:
                lines.append(f"👥 持币人数: {holders:,}")
            if liquidity:
                lines.append(f"💧 流动性: ${liquidity:,.2f}")
            
            lines.append("")
        
        # 构建触发事件描述
        lines.append("<b>🎯 触发原因:</b>")
        for event in self.triggered_events:
            # event 是 TriggerEvent 对象
            lines.append(f"  {event.description}")
        
        lines.append("")
        
        if self.remark:
            lines.append(f"💡 备注: {self.remark}")
        
        lines.append(f"📡 触发时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(lines)


class TelegramNotifier:
    """Telegram通知服务"""
    
    def __init__(self, bot_token: str, chat_id: int):
        """
        初始化Telegram通知器
        
        Args:
            bot_token: Telegram Bot Token
            chat_id: 目标聊天ID
        """
        self.bot = Bot(token=bot_token)
        self.chat_id = chat_id
    
    def create_buttons(self, ca: str) -> InlineKeyboardMarkup:
        """
        创建Telegram内联按钮
        
        Args:
            ca: Token合约地址
            
        Returns:
            InlineKeyboardMarkup对象
        """
        buttons = [
            [
                InlineKeyboardButton("📊 GMGN", url=f"https://gmgn.ai/sol/token/{ca}"),
                InlineKeyboardButton("🔍 OKX", url=f"https://www.okx.com/web3/dex-swap#inputChain=501&inputCurrency={ca}&outputChain=501&outputCurrency=So11111111111111111111111111111111111111112")
            ]
        ]
        return InlineKeyboardMarkup(buttons)
    
    async def send_message(
        self, 
        message: str, 
        ca: Optional[str] = None,
        max_retries: int = 3
    ) -> bool:
        """
        发送Telegram消息
        
        Args:
            message: 消息文本（支持HTML格式）
            ca: Token合约地址（用于生成按钮）
            max_retries: 最大重试次数
            
        Returns:
            是否发送成功
        """
        for attempt in range(max_retries):
            try:
                # 准备按钮
                reply_markup = self.create_buttons(ca) if ca else None
                
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
                logger.info(f"✅ Telegram消息发送成功")
                return True
                
            except TelegramError as e:
                logger.warning(f"⚠️ Telegram发送失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error(f"❌ Telegram发送最终失败: {e}")
                    return False
                    
            except Exception as e:
                logger.error(f"❌ Telegram发送异常: {e}")
                return False
        
        return False


class WeChatNotifier:
    """微信通知服务（企业微信或个人微信API）"""
    
    def __init__(self, api_url: str, api_key: str, target: str):
        """
        初始化微信通知器
        
        Args:
            api_url: 微信API地址
            api_key: API密钥
            target: 目标接收者（群聊或个人）
        """
        self.api_url = api_url
        self.api_key = api_key
        self.target = target
    
    async def send_message(self, message: str, max_retries: int = 3) -> bool:
        """
        发送微信消息
        
        Args:
            message: 消息文本
            max_retries: 最大重试次数
            
        Returns:
            是否发送成功
        """

        # 这里先留空接口，等确定微信API格式后再实现
        logger.info("⏭️ 微信通知暂未实现")
        return True


class NotificationService:
    """通知服务管理器"""
    
    def __init__(
        self,
        telegram_enabled: bool = True,
        telegram_chat_id: int = -1002926135363,
        wechat_enabled: bool = False
    ):
        """
        初始化通知服务
        
        Args:
            telegram_enabled: 是否启用Telegram
            telegram_chat_id: Telegram聊天ID
            wechat_enabled: 是否启用微信
        """
        self.telegram_enabled = telegram_enabled
        self.wechat_enabled = wechat_enabled
        
        # 初始化Telegram
        if self.telegram_enabled:
            self.telegram = TelegramNotifier(
                bot_token=TELEGRAM_CONFIG['bot_token'],
                chat_id=telegram_chat_id
            )
        else:
            self.telegram = None
        
        # 初始化微信
        if self.wechat_enabled:
            self.wechat = WeChatNotifier(
                api_url=WECHAT_CONFIG['api_url'],
                api_key=WECHAT_CONFIG['api_key'],
                target=WECHAT_CONFIG['default_target']
            )
        else:
            self.wechat = None
    
    async def send_alert(
        self,
        notification_msg: NotificationMessage,
        notify_methods: List[str]
    ) -> Dict[str, bool]:
        """
        发送监控提醒
        
        Args:
            notification_msg: 通知消息对象
            notify_methods: 通知方式列表 ["telegram", "wechat"]
            
        Returns:
            发送结果 {"telegram": True, "wechat": False}
        """
        message_text = notification_msg.format_message()
        results = {}
        
        # 发送Telegram（传递ca以生成按钮）
        if "telegram" in notify_methods and self.telegram:
            results["telegram"] = await self.telegram.send_message(
                message_text, 
                ca=notification_msg.ca
            )
        
        # 发送微信
        if "wechat" in notify_methods and self.wechat:
            results["wechat"] = await self.wechat.send_message(message_text)
        
        # 统计结果
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)
        
        if success_count == total_count:
            logger.info(f"✅ 通知发送完成: {success_count}/{total_count} 成功")
        else:
            logger.warning(f"⚠️ 通知发送部分失败: {success_count}/{total_count} 成功")
        
        return results


# 需要导入asyncio
import asyncio

