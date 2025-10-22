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
                    
                    logger.info(f"使用代理: {proxy_url}")
                    
                    # 创建带代理的 HTTPXRequest（增大连接池，禁用SSL验证）
                    import httpx
                    http_client = httpx.AsyncClient(
                        proxy=proxy_url,
                        verify=False,  # 禁用SSL验证
                        timeout=httpx.Timeout(
                            connect=30.0,
                            read=30.0,
                            write=30.0,
                            pool=30.0
                        ),
                        limits=httpx.Limits(
                            max_connections=50,
                            max_keepalive_connections=20
                        )
                    )
                    request = HTTPXRequest(http_version="1.1", client=http_client)
                    
                    self.bot = Bot(token=self.bot_token, request=request)
                    logger.info("✅ Telegram机器人初始化成功（已启用代理，SSL验证已禁用）")
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
        
        max_retries = 2
        base_retry_delay = 3
        
        for attempt in range(max_retries):
            try:
                if topic_id:
                    logger.info(f"正在发送Telegram消息到论坛主题 {target}:{topic_id}, 尝试 {attempt + 1}/{max_retries}")
                else:
                    logger.info(f"正在发送Telegram消息到 {target}, 尝试 {attempt + 1}/{max_retries}")
                
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
                logger.info(f"消息ID: {result.message_id}")
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
                if attempt < max_retries - 1:
                    wait_time = base_retry_delay * (2 ** attempt)
                    logger.warning(f"网络错误，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    self.log_failure(target, e)
                    return False
                    
            except Exception as e:
                self.log_failure(target, e)
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

