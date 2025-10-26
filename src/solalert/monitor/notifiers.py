"""
通知服务（已废弃，请使用 src/solalert/notifiers/manager.py）
支持Telegram和微信通知
"""
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ..core.logger import get_logger
from ..core.config import TELEGRAM_CONFIG, WECHAT_CONFIG
# 使用新的 NotificationManager
from ..notifiers.manager import get_notification_manager

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
            f"💰 <b>代币</b>: {self.token_name} ({self.token_symbol})",
            f"📝 <b>合约地址</b>: <code>{self.ca}</code>",
            ""
        ]
        
        # 添加Token当前信息（优化显示）
        if self.token_data:
            price = self.token_data.get('usdPrice') or self.token_data.get('price')
            mcap = self.token_data.get('mcap') or self.token_data.get('marketCap')
            holders = self.token_data.get('holderCount') or self.token_data.get('holders')
            liquidity = self.token_data.get('liquidity')
            volume_24h = self.token_data.get('volume24h') or self.token_data.get('volume_24h')
            price_change_24h = self.token_data.get('priceChange24h') or self.token_data.get('price_24h')
            
            # 价格和涨跌幅
            if price is not None:
                price_str = f"${price:.10f}" if price < 0.0001 else f"${price:.6f}"
                if price_change_24h is not None:
                    change_emoji = "📈" if price_change_24h > 0 else "📉" if price_change_24h < 0 else "➡️"
                    lines.append(f"💵 <b>当前价格</b>: {price_str} {change_emoji} {price_change_24h:+.2f}%")
                else:
                    lines.append(f"💵 <b>当前价格</b>: {price_str}")
            
            # 市值
            if mcap:
                if mcap >= 1_000_000:
                    lines.append(f"📊 <b>市值</b>: ${mcap / 1_000_000:.2f}M")
                elif mcap >= 1_000:
                    lines.append(f"📊 <b>市值</b>: ${mcap / 1_000:.2f}K")
                else:
                    lines.append(f"📊 <b>市值</b>: ${mcap:,.2f}")
            
            # 24小时交易量
            if volume_24h:
                if volume_24h >= 1_000_000:
                    lines.append(f"📈 <b>24H交易量</b>: ${volume_24h / 1_000_000:.2f}M")
                elif volume_24h >= 1_000:
                    lines.append(f"📈 <b>24H交易量</b>: ${volume_24h / 1_000:.2f}K")
                else:
                    lines.append(f"📈 <b>24H交易量</b>: ${volume_24h:,.2f}")
            
            # 持币人数
            if holders:
                lines.append(f"👥 <b>持币人数</b>: {holders:,}")
            
            # 流动性
            if liquidity:
                if liquidity >= 1_000_000:
                    lines.append(f"💧 <b>流动性</b>: ${liquidity / 1_000_000:.2f}M")
                elif liquidity >= 1_000:
                    lines.append(f"💧 <b>流动性</b>: ${liquidity / 1_000:.2f}K")
                else:
                    lines.append(f"💧 <b>流动性</b>: ${liquidity:,.2f}")
            
            lines.append("")
        
        # 构建触发事件描述
        lines.append("<b>🎯 触发原因</b>:")
        for event in self.triggered_events:
            # event 是 TriggerEvent 对象
            lines.append(f"  {event.description}")
        
        lines.append("")
        
        if self.remark:
            lines.append(f"💡 <b>备注</b>: {self.remark}")
            lines.append("")
        
        lines.append(f"📡 <b>触发时间</b>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(lines)


class TelegramNotifier:
    """Telegram通知服务（已重构为使用 Bot API）"""
    
    def __init__(self, bot_token: str, chat_id: int):
        """
        初始化Telegram通知器
        
        Args:
            bot_token: Telegram Bot Token（保留兼容性）
            chat_id: 目标聊天ID
        """
        self.chat_id = chat_id
        # 使用新的 NotificationManager
        self._notification_manager = get_notification_manager()
        logger.info(f"✅ TelegramNotifier 初始化成功（Bot API 模式），Chat ID: {chat_id}")
    
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
        发送Telegram消息（通过 Bot API）
        
        Args:
            message: 消息文本（支持HTML格式）
            ca: Token合约地址（用于生成按钮）
            max_retries: 最大重试次数（保留兼容性，实际由 NotificationManager 处理）
            
        Returns:
            是否发送成功
        """
        try:
            # 准备按钮
            reply_markup = self.create_buttons(ca) if ca else None
            
            # 使用新的 NotificationManager 发送消息
            result = await self._notification_manager.send_telegram(
                target=str(self.chat_id),
                message=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            
            if result:
                logger.info(f"✅ Telegram消息发送成功 (Bot API)")
                return True
            else:
                logger.error(f"❌ Telegram发送失败")
                return False
                
        except Exception as e:
            logger.error(f"❌ Telegram发送异常: {e}")
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
