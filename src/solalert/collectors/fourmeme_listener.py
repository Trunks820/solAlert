"""
Four.meme Telegram 监听器
监听 Telegram 频道 @fourmemedeployers 的消息并解析入库
"""
import asyncio
import socks
import re
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events
from typing import Optional, Dict
import logging

from .base import BaseCollector
from ..core.config import TELEGRAM_CONFIG
from ..core.database import get_db
from ..repositories.token_repo import TokenRepository

logger = logging.getLogger(__name__)

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


class FourMemeParser:
    """Four.meme 消息解析器"""
    
    @staticmethod
    def parse_fourmeme_message(message_text: str) -> Optional[Dict]:
        """
        解析 Four.meme 部署消息
        
        消息格式:
        🤖 Token Address : 0x6edAF61aB352aa59b04EfCF0E391A324B36d4444
        Name : 公信宝
        Symbol : 公信宝
        Creator : 0xCC3c84f7e61E65Df999A0a0E38D4c18df0A16ab7
        ...
        
        Args:
            message_text: Telegram消息文本
            
        Returns:
            解析后的数据字典或None
        """
        try:
            lines = message_text.strip().split('\n')
            
            token_name = None
            token_symbol = None
            ca = None
            twitter_url = None
            
            for line in lines:
                line = line.strip()
                
                # 提取 Token Address (CA) - 第一个字段
                if 'Token Address' in line and ':' in line:
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        ca_text = parts[1].strip()
                        ca_match = re.search(r'0x[a-fA-F0-9]{40}', ca_text)
                        if ca_match:
                            ca = ca_match.group(0)
                
                # 提取 Token Name
                if line.startswith('Name') and ':' in line:
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        token_name = parts[1].strip()
                
                # 提取 Token Symbol
                if line.startswith('Symbol') and ':' in line:
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        token_symbol = parts[1].strip()
                
                # 提取 Twitter URL
                if 'twitter' in line.lower() or 'x.com' in line.lower():
                    twitter_match = re.search(r'https?://(?:www\.)?(twitter\.com|x\.com)/[^\s\)]+', line)
                    if twitter_match:
                        twitter_url = twitter_match.group(0).strip()
            
            # 必须有 CA 才返回数据
            if ca:
                return {
                    'ca': ca,
                    'token_name': token_name,
                    'token_symbol': token_symbol,
                    'twitter_url': twitter_url,
                    'raw_message': message_text
                }
            else:
                return None
                
        except Exception as e:
            logger.error(f"解析Four.meme消息失败: {e}")
            return None


class FourMemeListener(BaseCollector):
    """Four.meme Telegram 监听器"""
    
    def __init__(self):
        super().__init__("FourMemeListener")
        
        # Telegram配置
        self.api_id = TELEGRAM_CONFIG['api_id']
        self.api_hash = TELEGRAM_CONFIG['api_hash']
        self.channel_id = 2178358910  # @fourmemedeployers
        
        # 代理配置 - 从配置文件读取
        proxy_config = TELEGRAM_CONFIG.get('proxy', {})
        if proxy_config.get('enabled'):
            self.proxy = (
                socks.SOCKS5,
                proxy_config.get('host', '127.0.0.1'),
                proxy_config.get('port', 1081)
            )
        else:
            self.proxy = None
        
        self.client = None
        self.parser = FourMemeParser()
        self.db = get_db()
        self.token_repo = TokenRepository()
        self._event_builder = None
        self._poll_task = None
        self.latest_msg_id: Optional[int] = None
    
    async def start(self):
        """启动实时监听"""
        self.is_running = True
        self.log_info("🚀 启动 Four.meme Telegram 实时监听器")
        
        try:
            # 初始化Telegram客户端（使用独立的session文件）
            self.client = TelegramClient(
                'fourmeme_listener_session',  # 使用独立的session文件名
                self.api_id,
                self.api_hash,
                proxy=self.proxy
            )
            
            await self.client.start()
            self.log_success("已连接到 Telegram")
            
            # 获取频道信息
            entity = await self.client.get_entity(self.channel_id)
            chat_title = getattr(entity, 'title', 'Unknown')
            self.log_info(f"开始监听频道: {chat_title} (ID: {self.channel_id})")
            
            # 重置最新消息游标
            self.latest_msg_id = None
            
            # 🆕 启动时先采集当天的历史消息
            await self._collect_today_history(entity)
            
            # 注册实时事件处理器并启动补偿轮询
            self._event_builder = events.NewMessage(chats=[self.channel_id])
            self.client.add_event_handler(self._handle_new_message, self._event_builder)

            if self._poll_task is None or self._poll_task.done():
                self._poll_task = asyncio.create_task(self._poll_new_messages(entity))

            self.log_success("实时监听已启动，等待新的 Four.meme 部署消息...")
            
            # 保持运行
            await self.client.run_until_disconnected()
            
        except KeyboardInterrupt:
            self.log_info("用户停止监听")
        except Exception as e:
            self.log_error("实时监听失败", e)
        finally:
            await self.stop()
    
    async def _collect_today_history(self, entity):
        """采集当天的历史消息"""
        try:
            self.log_info("📚 开始采集当天历史消息...")

            today_start = datetime.now(BEIJING_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

            total_count = 0
            token_count = 0
            saved_count = 0

            async for message in self.client.iter_messages(entity, limit=100):
                msg_time = message.date.astimezone(BEIJING_TZ)
                if msg_time < today_start:
                    break

                total_count += 1

                token_found = await self._process_fourmeme_message(
                    message_id=message.id,
                    message_text=message.message or "",
                    message_date=message.date,
                    source="历史补偿"
                )

                if token_found is not False:  # token_found 可能是 True 或 False
                    token_count += 1
                    if token_found is True:  # 只有 True 表示成功入库
                        saved_count += 1

            self.log_success(
                f"历史消息采集完成: 检查{total_count}条，发现{token_count}个Token，"
                f"新入库{saved_count}条，重复{token_count - saved_count}条"
            )

        except Exception as e:
            self.log_error("采集历史消息失败", e)

    async def _poll_new_messages(self, entity):
        """补偿性轮询机制（防止遗漏）"""
        self.log_info("启动补偿轮询机制（每30秒检查一次新消息）")

        while self.is_running:
            try:
                await asyncio.sleep(30)

                if not self.is_running:
                    break

                async for message in self.client.iter_messages(entity, limit=5):
                    if self.latest_msg_id and message.id <= self.latest_msg_id:
                        break

                    await self._process_fourmeme_message(
                        message_id=message.id,
                        message_text=message.message or "",
                        message_date=message.date,
                        source="补偿轮询"
                    )

            except Exception as e:
                self.log_error(f"补偿轮询异常: {e}")

    async def _handle_new_message(self, event):
        """实时消息事件处理器"""
        try:
            message = event.message
            await self._process_fourmeme_message(
                message_id=message.id,
                message_text=message.message or "",
                message_date=message.date,
                source="实时推送"
            )

        except Exception as e:
            self.log_error(f"处理实时消息失败: {e}")

    async def _process_fourmeme_message(
        self,
        message_id: int,
        message_text: str,
        message_date,
        source: str = "未知"
    ) -> bool:
        """
        处理 Four.meme 消息（可复用逻辑）
        
        Returns:
            是否发现Token
        """
        if not message_text:
            return False

        # 更新最新消息 ID
        if self.latest_msg_id is None or message_id > self.latest_msg_id:
            self.latest_msg_id = message_id

        # 解析消息
        parsed_data = self.parser.parse_fourmeme_message(message_text)

        if not parsed_data:
            return False

        # 提取数据
        ca = parsed_data['ca']
        token_name = parsed_data.get('token_name')
        token_symbol = parsed_data.get('token_symbol')
        twitter_url = parsed_data.get('twitter_url')

        # 转换为北京时间（去掉时区信息）
        launch_time = message_date.astimezone(BEIJING_TZ).replace(tzinfo=None)

        # 生成 tg_msg_id
        tg_msg_id = f"fourmeme_tg_{message_id}"

        try:
            # 插入数据库（使用 INSERT IGNORE 自动根据 ca 去重）
            sql = """
            INSERT IGNORE INTO token_launch_history
            (ca, token_name, token_symbol, twitter_url, source, launch_time, tg_msg_id, highest_market_cap, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            params = (
                ca,
                token_name,
                token_symbol,
                twitter_url,
                'fourmeme',  # source 标记为 fourmeme
                launch_time,
                tg_msg_id,
                0,  # 初始市值为0
                datetime.now()
            )

            rowcount = self.db.execute_update(sql, params)

            if rowcount > 0:
                twitter_info = f" [Twitter: {twitter_url}]" if twitter_url else " [无Twitter]"
                self.log_info(
                    f"🟢 [{source}] 新Four.meme Token已入库: {token_name or 'Unknown'} "
                    f"({token_symbol or 'N/A'}) | CA: {ca[:10]}...{twitter_info}"
                )

                # 如果有Twitter链接，同时保存到Twitter账号管理表（自动识别类型）
                if twitter_url:
                    self.token_repo.insert_twitter_account(twitter_url)

                return True
            else:
                # 数据已存在（被 IGNORE 跳过）
                self.log_info(
                    f"⚪ [{source}] 重复数据已跳过: {token_name or 'Unknown'} "
                    f"({token_symbol or 'N/A'}) | CA: {ca[:10]}..."
                )
                return False

        except Exception as e:
            self.log_error(f"❌ 插入Four.meme Token失败: {e} | CA: {ca}")
            return False
    
    async def stop(self):
        """停止监听器"""
        self.log_info("⏹️  正在停止 Four.meme 监听器...")
        self.is_running = False

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        if self.client:
            if self._event_builder:
                self.client.remove_event_handler(self._handle_new_message, self._event_builder)

            await self.client.disconnect()
            self.log_success("Four.meme 监听器已停止")
    
    async def collect_history(self, days: int = None, limit: int = None):
        """
        采集历史消息
        
        Args:
            days: 采集最近几天的消息（None=全部历史）
            limit: 最多采集多少条消息（None=不限制）
        """
        if days is None and limit is None:
            self.log_info(f"🔍 开始采集所有历史消息...")
        elif days is not None:
            self.log_info(f"🔍 开始采集最近 {days} 天的历史消息...")
        else:
            self.log_info(f"🔍 开始采集历史消息（最多{limit}条）...")
        
        try:
            # 初始化客户端（使用独立的session文件）
            self.client = TelegramClient(
                'fourmeme_listener_session',  # 使用独立的session文件名
                self.api_id,
                self.api_hash,
                proxy=self.proxy
            )
            
            await self.client.start()
            entity = await self.client.get_entity(self.channel_id)
            
            # 计算起始时间（如果指定了天数）
            start_date = None
            if days is not None:
                start_date = datetime.now(BEIJING_TZ) - timedelta(days=days)
            
            total_count = 0
            token_count = 0
            saved_count = 0
            
            # 每处理100条消息输出一次进度
            progress_interval = 100
            
            async for message in self.client.iter_messages(entity, limit=limit):
                msg_time = message.date.astimezone(BEIJING_TZ)
                
                # 如果指定了天数限制，且消息时间早于起始时间，停止
                if start_date and msg_time < start_date:
                    break
                
                total_count += 1
                
                token_found = await self._process_fourmeme_message(
                    message_id=message.id,
                    message_text=message.message or "",
                    message_date=message.date,
                    source="历史采集"
                )
                
                if token_found is not False:
                    token_count += 1
                    if token_found is True:
                        saved_count += 1
                
                # 每100条输出一次进度
                if total_count % progress_interval == 0:
                    self.log_info(
                        f"📊 进度: 已检查{total_count}条，发现{token_count}个Token，"
                        f"新入库{saved_count}条，重复{token_count - saved_count}条"
                    )
            
            self.log_success(
                f"✅ 历史采集完成: 检查{total_count}条消息，发现{token_count}个Token，"
                f"新入库{saved_count}条，重复{token_count - saved_count}条"
            )
            
        except Exception as e:
            self.log_error("历史采集失败", e)
        finally:
            if self.client:
                await self.client.disconnect()
