"""
Telegram 通知器
基于 python-telegram-bot 库直接调用 Bot API
支持消息队列，串行化发送避免并发冲突
"""
import asyncio
import logging
from typing import Optional
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TelegramError

from .base import BaseNotifier
from ..core.config import TELEGRAM_CONFIG

logger = logging.getLogger(__name__)


class TelegramQueue:
    """Telegram 消息队列（单例模式）"""
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.queue = asyncio.Queue()
        self.worker_task = None
        self._running = False
        logger.info("🔧 [TelegramQueue] 消息队列已初始化")
    
    async def start_worker(self):
        """启动队列工作线程"""
        if self._running:
            return
        self._running = True
        self.worker_task = asyncio.create_task(self._process_queue())
        logger.info("🚀 [TelegramQueue] 队列工作线程已启动")
    
    async def stop_worker(self):
        """停止队列工作线程"""
        self._running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 [TelegramQueue] 队列工作线程已停止")
    
    async def _process_queue(self):
        """处理队列中的消息"""
        logger.info("👷 [TelegramQueue] 开始处理消息队列...")
        while self._running:
            try:
                # 从队列中取出一个任务
                task_data = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                
                bot, target, message, parse_mode, topic_id, reply_markup, future = task_data
                
                logger.info(f"📨 [TelegramQueue] 从队列取出消息 -> {target} | 队列剩余: {self.queue.qsize()}")
                
                try:
                    # 执行发送
                    result = await bot.send_message(
                        chat_id=target,
                        text=message,
                        parse_mode=parse_mode,
                        message_thread_id=topic_id,
                        reply_markup=reply_markup,
                        disable_web_page_preview=True
                    )
                    # 设置结果
                    if not future.done():
                        future.set_result(result)
                except Exception as e:
                    # 设置异常
                    if not future.done():
                        future.set_exception(e)
                finally:
                    self.queue.task_done()
                    # 发送完一条后，稍微等待一下，避免过快
                    await asyncio.sleep(0.1)
                    
            except asyncio.TimeoutError:
                # 队列空闲，继续等待
                continue
            except Exception as e:
                logger.error(f"❌ [TelegramQueue] 队列处理异常: {e}")
                await asyncio.sleep(1.0)
    
    async def enqueue(self, bot, target, message, parse_mode, topic_id, reply_markup):
        """将消息加入队列"""
        future = asyncio.Future()
        await self.queue.put((bot, target, message, parse_mode, topic_id, reply_markup, future))
        queue_size = self.queue.qsize()
        logger.info(f"➕ [TelegramQueue] 消息已加入队列 -> {target} | 队列长度: {queue_size}")
        return future


class TelegramNotifier(BaseNotifier):
    """Telegram通知器（基于 Bot API + 消息队列）"""
    
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
        
        # 初始化消息队列
        self.queue = TelegramQueue()
        
        # 启动队列工作线程（在事件循环中）
        try:
            asyncio.create_task(self.queue.start_worker())
        except RuntimeError:
            # 如果还没有事件循环，稍后启动
            logger.warning("⚠️ [TelegramNotifier] 事件循环未就绪，队列将在首次使用时启动")
            
        logger.info("✅ Telegram通知器初始化成功（Bot API + 消息队列模式）")
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
            # 确保队列工作线程已启动
            if not self.queue._running:
                await self.queue.start_worker()
            
            # 直接调用 Bot API 发送消息（通过队列）
            import time
            from telegram.error import RetryAfter, TimedOut, NetworkError
            
            logger.info(f"🚀 [TelegramNotifier] 准备发送消息 -> {target} | 消息长度={len(message)}")
            
            start = time.monotonic()
            
            # 将消息加入队列，等待队列处理完成
            try:
                future = await self.queue.enqueue(
                    self.bot, target, message, parse_mode, topic_id, reply_markup
                )
                
                # 等待队列处理完成（带超时）
                result = await asyncio.wait_for(future, timeout=90.0)
                
            except asyncio.TimeoutError:
                cost = time.monotonic() - start
                logger.error(f"⏱️ [TelegramNotifier] 队列处理超时 -> {target} | 耗时={cost:.2f}s (超过90秒)")
                return False
            
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
