"""
GMGN Pump 监听器
监听 Telegram 频道的 Pump 消息并解析入库
"""
import asyncio
import sys
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


class GMGNPumpParser:
    """GMGN Pump消息解析器"""
    
    @staticmethod
    def parse_pump_message(message_text: str) -> Optional[Dict]:
        """
        解析GMGN pump消息
        
        Args:
            message_text: Telegram消息文本
            
        Returns:
            解析后的数据字典或None
        """
        try:
            lines = message_text.strip().split('\n')
            
            # 检查是否是pump消息
            if not any('PUMP已满' in line or 'PUMP⚡️' in line or 
                      ('PUMP' in line and ('已满' in line or '秒满' in line)) 
                      for line in lines):
                return None
            
            token_name = None
            token_symbol = None
            ca = None
            
            # 查找PUMP行索引
            pump_line_idx = -1
            for i, line in enumerate(lines):
                if 'PUMP已满' in line or 'PUMP⚡️' in line or \
                   ('PUMP' in line and ('已满' in line or '秒满' in line)):
                    pump_line_idx = i
                    break
            
            # 提取Token信息
            if pump_line_idx >= 0 and pump_line_idx + 2 < len(lines):
                token_line = lines[pump_line_idx + 2].strip()
                
                if token_line and not token_line.startswith(
                    ('🎲', '👥', '👑', '🚀', '👨🏻‍💻', '💊', '🔥', 'Check')):
                    # 匹配格式: "XAU (Gold)" 或 "$ARBX (ARBITRIX)"
                    match = re.match(r'^(\$?[A-Za-z0-9_]+)\s*\(([^)]+)\)$', token_line)
                    if match:
                        symbol_raw = match.group(1).strip()
                        token_symbol = symbol_raw.lstrip('$')
                        token_name = match.group(2).strip()
                    else:
                        token_name = token_line.lstrip('$')
                        token_symbol = token_line.lstrip('$').split()[0] if token_line.split() else token_line.lstrip('$')
            
            # 提取CA地址
            for i, line in enumerate(lines):
                line = line.strip()
                
                if '🎲 CA:' in line or 'CA:' in line:
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        ca_match = re.search(r'[A-Za-z0-9]{40,50}', next_line)
                        if ca_match:
                            ca = ca_match.group(0)
                            break
                
                ca_match = re.search(r'[A-Za-z0-9]{40,50}', line)
                if ca_match and 'gmgn.ai' not in line:
                    ca = ca_match.group(0)
                    break
            
            # 提取Twitter链接
            twitter_url = None
            for line in lines:
                # 格式1: 🐦  Twitter (https://twitter.com/xxx)
                twitter_match = re.search(r'🐦\s*Twitter\s*\(([^)]+)\)', line)
                if twitter_match:
                    twitter_url = twitter_match.group(1).strip()
                    break
                
                # 格式2: 包含Twitter的行中的链接
                if 'Twitter' in line:
                    twitter_direct = re.search(r'https?://(?:www\.)?twitter\.com/[^\s\|\)]+', line)
                    if twitter_direct:
                        twitter_url = twitter_direct.group(0).strip()
                        break
                
                # 格式3: 直接查找twitter.com链接
                twitter_direct = re.search(r'https?://(?:www\.)?twitter\.com/[^\s\|\)]+', line)
                if twitter_direct:
                    twitter_url = twitter_direct.group(0).strip()
                    break
                
                # 格式4: x.com链接
                x_direct = re.search(r'https?://(?:www\.)?x\.com/[^\s\|\)]+', line)
                if x_direct:
                    twitter_url = x_direct.group(0).strip()
                    break
            
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
            logger.error(f"解析消息失败: {e}")
            return None


class PumpListener(BaseCollector):
    """Pump监听器"""
    
    def __init__(self):
        super().__init__("PumpListener")
        
        # Telegram配置
        self.api_id = TELEGRAM_CONFIG['api_id']
        self.api_hash = TELEGRAM_CONFIG['api_hash']
        self.gmgn_channel_id = TELEGRAM_CONFIG['gmgn_channel_id']  # 从配置读取频道ID
        
        # 代理配置
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
        self.parser = GMGNPumpParser()
        self.token_repo = TokenRepository()
    
    async def start(self):
        """启动实时监听"""
        self.is_running = True
        self.log_info("🚀 启动 GMGN Pump 实时监听器")
        
        try:
            # 初始化Telegram客户端
            self.client = TelegramClient(
                'gmgn_listener_session',
                self.api_id,
                self.api_hash,
                proxy=self.proxy
            )
            
            await self.client.start()
            self.log_success("已连接到 Telegram")
            
            # 获取频道信息
            entity = await self.client.get_entity(self.gmgn_channel_id)
            chat_title = getattr(entity, 'title', 'Unknown')
            self.log_info(f"开始监听频道: {chat_title} (ID: {self.gmgn_channel_id})")
            
            # 注册新消息处理器
            @self.client.on(events.NewMessage(chats=entity))
            async def handle_new_message(event):
                await self._handle_message(event)
            
            self.log_success("实时监听已启动，等待新的 Pump 消息...")
            
            # 保持运行
            await self.client.run_until_disconnected()
            
        except KeyboardInterrupt:
            self.log_info("用户停止监听")
        except Exception as e:
            self.log_error("实时监听失败", e)
        finally:
            await self.stop()
    
    async def _handle_message(self, event):
        """处理新消息"""
        try:
            if not event.message.message:
                return
            
            # 解析pump消息
            pump_data = self.parser.parse_pump_message(event.message.message)
            
            if pump_data:
                self.log_info(f"🎯 发现新 Pump 消息:")
                self.log_info(f"   Token: {pump_data['token_name']} ({pump_data['token_symbol']})")
                self.log_info(f"   CA: {pump_data['ca']}")
                
                # 转换时间为北京时间
                push_time_beijing = event.message.date.astimezone(BEIJING_TZ)
                time_str = push_time_beijing.strftime('%Y-%m-%d %H:%M:%S')
                self.log_info(f"   时间: {time_str} (北京时间)")
                
                # 入库
                success = self.token_repo.insert_pump_token(
                    ca=pump_data['ca'],
                    token_name=pump_data['token_name'],
                    token_symbol=pump_data['token_symbol'],
                    twitter_url=pump_data.get('twitter_url'),
                    launch_time=push_time_beijing,
                    tg_msg_id=str(event.message.id)
                )
                
                if success:
                    self.log_success("已保存到数据库")
                else:
                    self.log_warning("数据库保存失败或重复")
                    
        except Exception as e:
            self.log_error("处理新消息失败", e)
    
    async def collect_history(self):
        """采集历史消息"""
        self.log_info("🚀 开始采集 GMGN 历史消息")
        
        try:
            self.client = TelegramClient(
                'gmgn_history_session',
                self.api_id,
                self.api_hash,
                proxy=self.proxy
            )
            
            await self.client.start()
            self.log_success("已连接到 Telegram")
            
            # 获取频道信息
            entity = await self.client.get_entity(self.gmgn_channel_id)
            chat_title = getattr(entity, 'title', 'Unknown')
            self.log_info(f"目标频道: {chat_title} (ID: {self.gmgn_channel_id})")
            
            total_count = 0
            pump_count = 0
            saved_count = 0
            
            # 遍历历史消息
            async for message in self.client.iter_messages(entity):
                total_count += 1
                
                if message.message:
                    # 检查消息实体中的链接
                    twitter_links = []
                    if hasattr(message, 'entities') and message.entities:
                        for ent in message.entities:
                            if hasattr(ent, 'url') and ent.url:
                                if 'twitter.com' in ent.url or 'x.com' in ent.url:
                                    twitter_links.append(ent.url)
                    
                    # 构建完整消息
                    full_message = message.message
                    if twitter_links:
                        full_message += f"\n\n🐦  Twitter ({twitter_links[0]})"
                    
                    # 解析pump消息
                    pump_data = self.parser.parse_pump_message(full_message)
                    
                    if pump_data:
                        pump_count += 1
                        
                        # 转换时间
                        push_time_beijing = message.date.astimezone(BEIJING_TZ)
                        
                        # 入库
                        if self.token_repo.insert_pump_token(
                            ca=pump_data['ca'],
                            token_name=pump_data['token_name'],
                            token_symbol=pump_data['token_symbol'],
                            twitter_url=pump_data.get('twitter_url'),
                            launch_time=push_time_beijing,
                            tg_msg_id=str(message.id)
                        ):
                            saved_count += 1
                
                # 每处理100条显示进度
                if total_count % 100 == 0:
                    self.log_info(f"📊 进度: 总消息 {total_count}, 发现pump {pump_count}, 已保存 {saved_count}")
            
            self.log_success("历史消息采集完成!")
            self.log_info(f"📊 最终统计: 总消息 {total_count}, 发现pump {pump_count}, 成功保存 {saved_count}")
            
        except Exception as e:
            self.log_error("历史消息采集失败", e)
        finally:
            await self.client.disconnect()
    
    async def stop(self):
        """停止监听"""
        self.is_running = False
        if self.client:
            await self.client.disconnect()
        self.log_info("监听已停止")

