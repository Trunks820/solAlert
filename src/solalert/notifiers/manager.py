"""
统一通知管理器
整合 Telegram 和微信通知功能
"""
import re
import logging
from typing import Optional, List, Tuple
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from .telegram import TelegramNotifier
from .wechat import WeChatNotifier
from ..core.config import TELEGRAM_CONFIG

logger = logging.getLogger(__name__)


class NotificationManager:
    """统一通知管理器"""
    
    def __init__(self):
        """初始化通知管理器"""
        logger.info("🚀 初始化通知管理器...")
        
        self.telegram = TelegramNotifier()
        self.wechat = WeChatNotifier()
        
        # 诊断信息
        if self.telegram.is_enabled():
            logger.info("✅ Telegram通知器初始化成功")
        else:
            logger.warning("⚠️ Telegram通知器未启用")
            
        if self.wechat.is_enabled():
            logger.info("✅ 微信通知器初始化成功")
        else:
            logger.warning("⚠️ 微信通知器未启用")
    
    async def send_telegram(
        self,
        target: str,
        message: str,
        parse_mode: str = ParseMode.HTML,
        topic_id: Optional[int] = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        """发送Telegram消息"""
        # 发送前日志
        logger.info(
            f"📤 [NotificationManager] 准备发送 Telegram 消息 -> {target} | "
            f"文本长度={len(message)} | buttons={bool(reply_markup)} | topic={topic_id}"
        )
        
        return await self.telegram.send(
            target=target,
            message=message,
            parse_mode=parse_mode,
            topic_id=topic_id,
            reply_markup=reply_markup
        )
    
    async def send_wechat(self, target: str, message: str) -> bool:
        """发送微信消息"""
        # 清理HTML标签
        clean_message = self.clean_html_tags(message)
        return await self.wechat.send(target, clean_message)
    
    async def send_to_both(
        self,
        telegram_target: str,
        wechat_target: str,
        message: str,
        parse_mode: str = ParseMode.HTML
    ) -> Tuple[bool, bool]:
        """
        同时发送到Telegram和微信
        
        Returns:
            (telegram_success, wechat_success)
        """
        telegram_success = await self.send_telegram(telegram_target, message, parse_mode)
        wechat_success = await self.send_wechat(wechat_target, message)
        return telegram_success, wechat_success
    
    async def send_alert(
        self,
        message: str,
        channels: Optional[List[str]] = None,
        parse_mode: str = ParseMode.HTML
    ) -> bool:
        """
        发送告警消息到默认渠道
        
        Args:
            message: 消息内容
            channels: 通知渠道列表 ['telegram', 'wechat']，默认全部
            parse_mode: Telegram解析模式
            
        Returns:
            是否至少一个渠道发送成功
        """
        if channels is None:
            channels = ['telegram', 'wechat']
        
        success_count = 0
        
        # 发送到Telegram
        if 'telegram' in channels:
            target_channel = str(TELEGRAM_CONFIG.get('target_channel_id'))
            if await self.send_telegram(target_channel, message, parse_mode):
                success_count += 1
        
        # 发送到微信
        if 'wechat' in channels:
            if await self.send_wechat(self.wechat.default_target, message):
                success_count += 1
        
        return success_count > 0
    
    @staticmethod
    def clean_html_tags(text: str) -> str:
        """
        清理HTML标签，转换为纯文本
        
        Args:
            text: 包含HTML标签的文本
            
        Returns:
            纯文本
        """
        if not text:
            return text
        
        # 移除 <pre> 标签
        text = re.sub(r'<pre>(.*?)</pre>', lambda m: m.group(1), text, flags=re.DOTALL)
        
        # 移除 <code> 标签
        text = re.sub(r'<code>(.*?)</code>', lambda m: m.group(1), text)
        
        # 移除其他HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 移除复制提示
        text = text.replace('(点我复制)', '').replace('(点击复制)', '')
        
        # 清理多余空行
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        
        return text.strip()
    
    @staticmethod
    def build_copyable_address(address: str, label: str = "地址") -> str:
        """生成可点击复制的地址行（Telegram HTML格式）"""
        return f"{label}: <code>{address}</code>"
    
    @staticmethod
    def get_timestamp() -> str:
        """获取格式化时间戳"""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    @staticmethod
    def create_inline_buttons(buttons: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
        """
        创建Telegram内联按钮
        
        Args:
            buttons: [(按钮文字, 链接URL), ...]
            
        Returns:
            InlineKeyboardMarkup对象
        """
        keyboard = [
            [InlineKeyboardButton(text, url=url) for text, url in buttons]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    def format_pump_alert(
        self,
        token_name: str,
        token_symbol: str,
        ca: str,
        market_cap: Optional[str] = None,
        price: Optional[str] = None,
        holders: Optional[int] = None,
        twitter_url: Optional[str] = None,
        launch_time: Optional[str] = None
    ) -> Tuple[str, InlineKeyboardMarkup]:
        """
        格式化Pump发射提醒消息
        
        Returns:
            (消息文本, 按钮markup)
        """
        lines = [
            "<b>🚀 新币发射提醒</b>",
            "",
            f"💰 代币: {token_name} ({token_symbol})",
        ]
        
        if market_cap:
            lines.append(f"📊 市值: {market_cap}")
        if price:
            lines.append(f"💵 价格: {price}")
        if holders:
            lines.append(f"👥 持有人: {holders}")
        
        lines.append(f"\n{self.build_copyable_address(ca, '合约地址')}")
        
        if launch_time:
            lines.append(f"\n⏰ 发射时间: {launch_time}")
        
        lines.append(f"📡 播报时间: {self.get_timestamp()}")
        
        message = "\n".join(lines)
        
        # 创建按钮
        buttons = [
            ("📊 GMGN", f"https://gmgn.ai/sol/token/{ca}"),
            ("🔍 Birdeye", f"https://birdeye.so/token/{ca}?chain=solana")
        ]
        
        if twitter_url:
            buttons.append(("🐦 Twitter", twitter_url))
        
        markup = self.create_inline_buttons(buttons)
        
        return message, markup


# 全局通知管理器实例
notification_manager = NotificationManager()


def get_notification_manager() -> NotificationManager:
    """获取通知管理器实例"""
    return notification_manager

