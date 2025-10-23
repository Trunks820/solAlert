"""
Telegram 通知器
基于 python-telegram-bot 实现
"""
import asyncio
import logging
from typing import Optional
from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TimedOut, NetworkError, BadRequest, Forbidden
from telegram.request import HTTPXRequest

from .base import BaseNotifier
from ..core.config import TELEGRAM_CONFIG

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Telegram通知器"""
    
    # 类级别的信号量，限制并发请求数
    _semaphore = None
    
    def __init__(self, bot_token: str = None, enabled: bool = True):
        """
        初始化Telegram通知器
        
        Args:
            bot_token: Bot Token，如果为None则使用配置文件中的
            enabled: 是否启用
        """
        super().__init__(enabled)
        self.bot_token = bot_token or TELEGRAM_CONFIG.get('bot_token')
        self.bot: Optional[Bot] = None
        
        # 初始化信号量（限制同时最多5个并发请求）
        if TelegramNotifier._semaphore is None:
            TelegramNotifier._semaphore = asyncio.Semaphore(5)
        
        self._init_bot()
    
    def _init_bot(self):
        """初始化Telegram机器人"""
        try:
            if self.bot_token:
                logger.info(f"正在初始化Telegram机器人，Token长度: {len(self.bot_token)}")
                
                # 检查是否启用代理
                proxy_config = TELEGRAM_CONFIG.get('proxy', {})
                proxy_enabled = proxy_config.get('enabled', False)
                
                # 禁用 SSL 验证警告
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                
                if proxy_enabled:
                    # 配置代理
                    proxy_type = proxy_config.get('type', 'socks5')
                    proxy_host = proxy_config.get('host', '127.0.0.1')
                    proxy_port = proxy_config.get('port', 1081)
                    proxy_url = f"{proxy_type}://{proxy_host}:{proxy_port}"
                    
                    logger.info(f"✅ Telegram 使用代理: {proxy_url}")
                    
                    # 创建带代理的 HTTPXRequest（优化超时和连接池配置）
                    request = HTTPXRequest(
                        proxy=proxy_url,           # 使用新的 proxy 参数
                        connect_timeout=15.0,      # 连接超时（增加到15秒，应对代理慢启动）
                        read_timeout=25.0,         # 读取超时（增加到25秒，应对API响应慢）
                        write_timeout=15.0,        # 写入超时（增加到15秒）
                        pool_timeout=60.0,         # 连接池超时（60秒）
                        connection_pool_size=20,   # 连接池大小（20个连接）
                        http_version="1.1"
                    )
                    
                    self.bot = Bot(token=self.bot_token, request=request)
                    logger.info(f"✅ Telegram机器人初始化成功（代理:{proxy_url} | 池:20 | 读超时:25s）")
                else:
                    # 不使用代理（服务器环境，使用默认配置）
                    self.bot = Bot(token=self.bot_token)
                    logger.info("✅ Telegram机器人初始化成功（未使用代理）")
            else:
                logger.error("❌ 未配置Telegram Bot Token")
                self.bot = None
                self.enabled = False
        except Exception as e:
            logger.error(f"❌ 初始化Telegram机器人失败: {e}")
            self.bot = None
            self.enabled = False
    
    async def send(
        self,
        target: str,
        message: str,
        parse_mode: str = ParseMode.HTML,
        topic_id: Optional[int] = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        **kwargs
    ) -> bool:
        """
        发送Telegram消息
        
        Args:
            target: 目标chat_id（群组ID或用户ID）
            message: 消息内容
            parse_mode: 解析模式（HTML/Markdown）
            topic_id: 论坛主题ID（可选）
            reply_markup: 按钮markup（可选）
            
        Returns:
            是否发送成功
        """
        if not self.enabled or not self.bot:
            self.log_disabled()
            return False
        
        max_retries = 3  # 增加重试次数，应对网络抖动和代理拥堵
        base_retry_delay = 2  # 适当延迟，给代理恢复时间
        
        # 使用信号量限制并发（最多5个同时发送）
        async with self._semaphore:
            for attempt in range(max_retries):
                try:
                    # 简化日志，只在第一次尝试或重试时输出
                    if attempt == 0:
                        logger.debug(f"📤 发送TG消息 -> {target}")
                    else:
                        logger.info(f"📤 重试发送 ({attempt + 1}/{max_retries}) -> {target}")
                    
                    send_kwargs = {
                        'chat_id': target,
                        'text': message,
                        'parse_mode': parse_mode,
                        'disable_web_page_preview': True,
                        'reply_markup': reply_markup
                    }
                    
                    if topic_id:
                        send_kwargs['message_thread_id'] = topic_id
                    
                    result = await self.bot.send_message(**send_kwargs)
                    
                    self.log_success(target, message[:100])
                    logger.debug(f"✅ 消息ID: {result.message_id}")
                    return True
                    
                except BadRequest as e:
                    if "chat not found" in str(e).lower():
                        logger.error(f"错误: 找不到聊天 {target}")
                        return False
                    elif "can't parse entities" in str(e).lower():
                        # 尝试纯文本模式
                        logger.info("尝试使用纯文本模式重新发送...")
                        try:
                            plain_kwargs = {
                                'chat_id': target,
                                'text': message,
                                'disable_web_page_preview': True,
                                'reply_markup': reply_markup
                            }
                            if topic_id:
                                plain_kwargs['message_thread_id'] = topic_id
                            result = await self.bot.send_message(**plain_kwargs)
                            logger.info(f"✅ 使用纯文本模式发送成功 消息ID: {result.message_id}")
                            return True
                        except Exception as e2:
                            logger.error(f"纯文本模式也失败: {e2}")
                            return False
                    else:
                        self.log_failure(target, e)
                        return False
                        
                except Forbidden:
                    logger.error(f"Telegram权限错误: 机器人没有权限发送消息到 {target}")
                    return False
                    
                except (TimedOut, NetworkError) as e:
                    error_msg = str(e)
                    error_type = type(e).__name__
                    
                    # 判断是代理问题还是 API 问题
                    if "connection" in error_msg.lower() or "proxy" in error_msg.lower():
                        issue_type = "代理连接"
                    elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                        issue_type = "API响应超时"
                    else:
                        issue_type = "网络"
                    
                    if attempt < max_retries - 1:
                        wait_time = base_retry_delay * (2 ** attempt)
                        logger.warning(
                            f"⚠️ {issue_type}问题 [{error_type}] 第{attempt+1}次 | "
                            f"{wait_time}秒后重试 | {error_msg[:100]}"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"❌ [TelegramNotifier] {issue_type}失败 [{error_type}] -> {target} | "
                            f"已重试{max_retries}次 | {error_msg[:200]}"
                        )
                        return False
                        
                except Exception as e:
                    import traceback
                    logger.error(f"❌ [TelegramNotifier] 未知错误 -> {target}: {type(e).__name__} - {e}")
                    logger.error(f"   详细错误: {traceback.format_exc()}")
                    return False
            
            return False
    
    async def send_to_forum_topic(
        self,
        topic_id: int,
        message: str,
        parse_mode: str = ParseMode.HTML,
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
        parse_mode: str = ParseMode.HTML,
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

