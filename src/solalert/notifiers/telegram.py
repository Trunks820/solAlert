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
                
                # 诊断：打印当前所有任务
                try:
                    import asyncio
                    all_tasks = asyncio.all_tasks()
                    running_count = len([t for t in all_tasks if not t.done()])
                    logger.info(f"🔍 [诊断] 当前运行中的异步任务数: {running_count}/{len(all_tasks)}")
                except Exception as e:
                    logger.debug(f"任务统计失败: {e}")
                
                import time
                from telegram.error import BadRequest, Forbidden, TimedOut, NetworkError
                
                send_start = time.monotonic()
                max_retries = 2  # 最多重试2次
                base_retry_delay = 3  # 基础重试间隔3秒
                result = None
                last_error = None
                
                # 重试机制
                for attempt in range(max_retries):
                    attempt_start = time.monotonic()
                    try:
                        logger.info(f"🔄 [TelegramQueue] 尝试发送 ({attempt + 1}/{max_retries}) -> {target}")
                        
                        # 执行发送（带超时保护 - 使用更短的超时避免卡住）
                        try:
                            result = await asyncio.wait_for(
                                bot.send_message(
                                    chat_id=target,
                                    text=message,
                                    parse_mode=parse_mode,
                                    message_thread_id=topic_id,
                                    reply_markup=reply_markup,
                                    disable_web_page_preview=True
                                ),
                                timeout=10.0  # 单次尝试超时10秒（更激进的超时）
                            )
                        except asyncio.TimeoutError:
                            attempt_cost = time.monotonic() - attempt_start
                            logger.warning(f"⏱️ [TelegramQueue] 单次尝试超时 ({attempt + 1}/{max_retries}) -> {target} | 耗时={attempt_cost:.2f}s")
                            raise TimedOut(f"Request timed out after {attempt_cost:.2f}s")
                        
                        send_cost = time.monotonic() - send_start
                        logger.info(f"✅ [TelegramQueue] 消息发送成功 -> {target} | message_id={result.message_id} | 耗时={send_cost:.2f}s | 尝试={attempt + 1}")
                        
                        # 设置结果
                        if not future.done():
                            future.set_result(result)
                        break  # 成功则跳出重试循环
                        
                    except BadRequest as e:
                        send_cost = time.monotonic() - send_start
                        if "can't parse entities" in str(e).lower():
                            logger.warning(f"⚠️ [TelegramQueue] 解析错误，尝试纯文本模式 -> {target}")
                            try:
                                result = await asyncio.wait_for(
                                    bot.send_message(
                                        chat_id=target,
                                        text=message,
                                        message_thread_id=topic_id,
                                        reply_markup=reply_markup,
                                        disable_web_page_preview=True
                                    ),
                                    timeout=20.0
                                )
                                logger.info(f"✅ [TelegramQueue] 纯文本模式成功 -> {target} | message_id={result.message_id}")
                                if not future.done():
                                    future.set_result(result)
                                break
                            except Exception as e2:
                                logger.error(f"❌ [TelegramQueue] 纯文本模式也失败 -> {target}: {e2}")
                                last_error = e2
                        else:
                            logger.error(f"❌ [TelegramQueue] BadRequest -> {target}: {e} | 耗时={send_cost:.2f}s")
                            last_error = e
                            break  # BadRequest 不重试
                    
                    except Forbidden as e:
                        send_cost = time.monotonic() - send_start
                        logger.error(f"❌ [TelegramQueue] 权限错误 -> {target}: {e} | 耗时={send_cost:.2f}s")
                        last_error = e
                        break  # Forbidden 不重试
                    
                    except (asyncio.TimeoutError, TimedOut, NetworkError) as e:
                        send_cost = time.monotonic() - send_start
                        last_error = e
                        if attempt < max_retries - 1:
                            wait_time = base_retry_delay * (2 ** attempt)
                            logger.warning(f"⚠️ [TelegramQueue] 网络错误，{wait_time}秒后重试 ({attempt + 1}/{max_retries}) -> {target}: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ [TelegramQueue] 网络错误，已达最大重试 -> {target} | 耗时={send_cost:.2f}s")
                    
                    except Exception as e:
                        send_cost = time.monotonic() - send_start
                        logger.error(f"❌ [TelegramQueue] 未知错误 ({attempt + 1}/{max_retries}) -> {target}: {type(e).__name__}: {e} | 耗时={send_cost:.2f}s")
                        last_error = e
                        if attempt < max_retries - 1:
                            await asyncio.sleep(base_retry_delay)
                
                # 如果所有重试都失败，设置异常
                if result is None and not future.done():
                    total_cost = time.monotonic() - send_start
                    logger.error(f"❌ [TelegramQueue] 所有重试均失败 -> {target} | 总耗时={total_cost:.2f}s")
                    future.set_exception(last_error or Exception("发送失败"))
                
                self.queue.task_done()
                # 发送完一条后，稍微等待一下，避免过快
                await asyncio.sleep(0.2)
                    
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
        
        # 创建 Bot 实例，参考成功项目的配置
        if self.bot_token:
            from telegram.request import HTTPXRequest
            import httpx
            
            # 优化连接池配置（参考成功项目） - 使用更激进的超时避免卡住
            request = HTTPXRequest(
                connect_timeout=10.0,       # 连接超时10秒（更快失败）
                read_timeout=10.0,          # 读取超时10秒（更快失败）
                write_timeout=10.0,         # 写入超时10秒
                pool_timeout=30.0,          # 连接池超时30秒
                connection_pool_size=100,   # 连接池大小100
                http_version="1.1"          # 强制使用 HTTP/1.1（避免 HTTP/2 多路复用问题）
            )
            
            self.bot = Bot(token=self.bot_token, request=request)
            logger.info("✅ Telegram Bot 初始化成功 (连接池: 100, 超时: 10s, HTTP/1.1)")
        else:
            self.bot = None
            logger.error("❌ 未配置 Telegram Bot Token")
        
        # 初始化消息队列（队列将在首次发送时自动启动）
        self.queue = TelegramQueue()
        
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
                
                # 等待队列处理完成（带超时，考虑队列等待时间 + 发送时间）
                # 队列超时 = 60s(发送超时) + 30s(排队等待) = 90s
                result = await asyncio.wait_for(future, timeout=120.0)
                
            except asyncio.TimeoutError:
                cost = time.monotonic() - start
                logger.error(f"⏱️ [TelegramNotifier] 队列处理超时 -> {target} | 耗时={cost:.2f}s (超过120秒)")
                return False
            except TimeoutError as e:
                # 来自队列的超时异常
                cost = time.monotonic() - start
                logger.error(f"⏱️ [TelegramNotifier] 发送超时 -> {target} | {e} | 总耗时={cost:.2f}s")
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
