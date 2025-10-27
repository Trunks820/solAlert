"""
BSC WebSocket 监控器
使用 WebSocket 实时监听链上事件，替代 Alchemy Webhook
"""
import json
import time
import asyncio
import logging
import signal
import threading
from decimal import Decimal
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import websocket
import requests

from ..api.telegram_api import TelegramAPI
from ..api.dbotx_api import DBotXAPI
from ..core.redis_client import get_redis
from ..core.config import TELEGRAM_CONFIG
from .trigger_logic import TriggerLogic

logger = logging.getLogger(__name__)


class BSCWebSocketMonitor:
    """BSC WebSocket 监控器"""
    
    def __init__(
        self,
        ws_url: str,
        rpc_url: str,
        enable_telegram: bool = True
    ):
        """
        初始化 WebSocket 监控器
        
        Args:
            ws_url: WebSocket RPC URL
            rpc_url: HTTP RPC URL
            enable_telegram: 是否启用 Telegram 推送
        """
        self.ws_url = ws_url
        self.rpc_url = rpc_url
        self.enable_telegram = enable_telegram
        
        # Redis
        self.redis_client = get_redis()
        
        # 常量
        self.USDT = "0x55d398326f99059ff775485246999027b3197955"
        self.WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        self.FOURMEME_PROXY = [
            "0x5c952063c7fc8610ffdb798152d69f0b9550762b",  # 主Proxy
            "0x8e06ab256ca534ebba05d700f8e40341ec39e0d6"   # Try Buy
        ]
        self.TOPIC_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
        
        # Telegram 配置
        self.bsc_channel_id = str(TELEGRAM_CONFIG.get('bsc_channel_id'))
        
        # 冷却期配置
        self.cooldown_minutes = 3.0
        self.cooldown_jitter = 0.5
        
        # 过滤配置（从 Redis 加载）
        self.min_amount_internal = 200  # 默认值
        self.min_amount_external = 400  # 外盘默认400
        self.cumulative_min_amount_internal = 500
        self.cumulative_min_amount_external = 1000  # 外盘累计1000
        
        # 默认 events_config（后备配置）
        self.internal_events_config = {
            'priceChange': {'enabled': True, 'risePercent': 30},  # 默认：内盘涨幅 >= 30%
            'volume': {'enabled': True, 'threshold': 5000}        # 默认：内盘交易量 >= $5000
        }
        self.external_events_config = {
            'priceChange': {'enabled': True, 'risePercent': 50},  # 默认：外盘涨幅 >= 50%
            'volume': {'enabled': True, 'threshold': 20000}       # 默认：外盘交易量 >= $20000
        }
        
        # WebSocket
        self.ws = None
        self.should_stop = False
        self.reconnect_count = 0  # 重连计数
        
        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="BSC-WS-Worker")
        self.thread_local = threading.local()
        
        # 交易去重
        self.seen_txs = set()
        self.max_seen_txs = 10000
        
        # WBNB 价格缓存
        self.wbnb_price = 600.0
        self.wbnb_price_timestamp = 0
        self.price_cache_ttl = 300  # 5分钟
        
        # RPC 调用计数
        self.rpc_id = 0
        
    async def load_config_from_redis(self):
        """从 Redis 加载配置"""
        try:
            # 加载内盘配置
            internal_data = await asyncio.to_thread(
                self.redis_client.client.get, 'global_monitor:config:bsc:internal'
            )
            if internal_data:
                if isinstance(internal_data, bytes):
                    internal_data = internal_data.decode('utf-8')
                
                # 清理 Java 类型标记
                import re
                internal_data = re.sub(r'"@type"\s*:\s*"[^"]*"\s*,?\s*', '', internal_data)
                internal_data = re.sub(r':\s*(\d+)L\b', r':\1', internal_data)
                internal_data = re.sub(r',\s*}', '}', internal_data)
                
                config = json.loads(internal_data)
                self.min_amount_internal = config.get('min_transaction_usd', 200)
                self.cumulative_min_amount_internal = config.get('cumulative_min_amount_usd', 500)
                
                events_config_str = config.get('events_config', '{}')
                if events_config_str:
                    try:
                        if isinstance(events_config_str, str):
                            loaded_config = json.loads(events_config_str)
                        else:
                            loaded_config = events_config_str
                        
                        # 只在有有效配置时才覆盖默认值
                        if loaded_config and isinstance(loaded_config, dict):
                            self.internal_events_config = loaded_config
                            
                            # 确保 enabled 字段
                            if 'priceChange' in self.internal_events_config:
                                self.internal_events_config['priceChange']['enabled'] = True
                            if 'volume' in self.internal_events_config:
                                self.internal_events_config['volume']['enabled'] = True
                    except:
                        pass  # 保留默认值
            
            # 加载外盘配置
            external_data = await asyncio.to_thread(
                self.redis_client.client.get, 'global_monitor:config:bsc:external'
            )
            if external_data:
                if isinstance(external_data, bytes):
                    external_data = external_data.decode('utf-8')
                
                # 清理 Java 类型标记
                external_data = re.sub(r'"@type"\s*:\s*"[^"]*"\s*,?\s*', '', external_data)
                external_data = re.sub(r':\s*(\d+)L\b', r':\1', external_data)
                external_data = re.sub(r',\s*}', '}', external_data)
                
                config = json.loads(external_data)
                self.min_amount_external = config.get('min_transaction_usd', 400)
                
                events_config_str = config.get('events_config', '{}')
                if events_config_str:
                    try:
                        if isinstance(events_config_str, str):
                            loaded_config = json.loads(events_config_str)
                        else:
                            loaded_config = events_config_str
                        
                        # 只在有有效配置时才覆盖默认值
                        if loaded_config and isinstance(loaded_config, dict):
                            self.external_events_config = loaded_config
                            
                            # 确保 enabled 字段
                            if 'priceChange' in self.external_events_config:
                                self.external_events_config['priceChange']['enabled'] = True
                            if 'volume' in self.external_events_config:
                                self.external_events_config['volume']['enabled'] = True
                    except:
                        pass  # 保留默认值

        except Exception as e:
            logger.error(f"❌ 加载 Redis 配置失败: {e}")
        
        # 打印最终配置信息
        logger.info(f"📊 内盘配置: 单笔>={self.min_amount_internal}U, 涨幅>={self.internal_events_config.get('priceChange', {}).get('risePercent')}%, 交易量>=${self.internal_events_config.get('volume', {}).get('threshold')}")
        logger.info(f"📊 外盘配置: 单笔>={self.min_amount_external}U, 涨幅>={self.external_events_config.get('priceChange', {}).get('risePercent')}%, 交易量>=${self.external_events_config.get('volume', {}).get('threshold')}")
    
    def get_thread_dbotx_api(self) -> DBotXAPI:
        """获取当前线程的 DBotX API 实例"""
        if not hasattr(self.thread_local, 'dbotx_api'):
            self.thread_local.dbotx_api = DBotXAPI()
        return self.thread_local.dbotx_api
    
    def rpc_call(self, method: str, params: list):
        """发送 HTTP RPC 请求"""
        self.rpc_id += 1
        try:
            resp = requests.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": self.rpc_id, "method": method, "params": params},
                timeout=10
            )
            return resp.json().get("result")
        except Exception as e:
            logger.debug(f"RPC 错误: {e}")
            return None
    
    def get_wbnb_price(self) -> float:
        """动态获取 WBNB 价格（带缓存）"""
        now = time.time()
        if now - self.wbnb_price_timestamp < self.price_cache_ttl:
            return self.wbnb_price
        
        try:
            resp = requests.get(
                'https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BNB_USDT',
                timeout=5
            )
            data = resp.json()
            
            if data and isinstance(data, list) and len(data) > 0:
                price = float(data[0].get('last', self.wbnb_price))
                self.wbnb_price = price
                self.wbnb_price_timestamp = now
                logger.info(f"✅ 更新 WBNB 价格: ${price}")
                return price
        except Exception as e:
            logger.warning(f"⚠️ 获取 WBNB 价格失败: {e}")
        
        return self.wbnb_price
    
    @lru_cache(maxsize=10000)
    def get_decimals(self, token: str) -> int:
        """获取代币精度（带缓存）"""
        try:
            data = self.rpc_call("eth_call", [{
                "to": token,
                "data": "0x313ce567"  # decimals()
            }, "latest"])
            return int(data, 16) if data else 18
        except:
            return 18
    
    @lru_cache(maxsize=10000)
    def get_token_symbol(self, token: str) -> str:
        """获取代币符号（带缓存）"""
        try:
            data = self.rpc_call("eth_call", [{
                "to": token,
                "data": "0x95d89b41"  # symbol()
            }, "latest"])
            if data and data != "0x":
                return bytes.fromhex(data[2:]).decode('utf-8', errors='ignore').strip('\x00')
        except:
            pass
        return "???"
    
    @lru_cache(maxsize=5000)
    def get_pair_tokens(self, pair: str) -> tuple:
        """获取交易对的 token0 和 token1"""
        try:
            token0_data = self.rpc_call("eth_call", [{
                "to": pair,
                "data": "0x0dfe1681"  # token0()
            }, "latest"])
            token1_data = self.rpc_call("eth_call", [{
                "to": pair,
                "data": "0xd21220a7"  # token1()
            }, "latest"])
            
            if token0_data and token1_data:
                token0 = "0x" + token0_data[-40:]
                token1 = "0x" + token1_data[-40:]
                return token0.lower(), token1.lower()
        except:
            pass
        return None, None
    
    def parse_swap_data(self, data: str) -> Optional[Dict]:
        """解析 Swap 事件数据"""
        try:
            if not data or data == "0x":
                return None
            
            hex_data = data[2:] if data.startswith("0x") else data
            
            if len(hex_data) < 256:
                return None
            
            return {
                "amount0In": int(hex_data[0:64], 16),
                "amount1In": int(hex_data[64:128], 16),
                "amount0Out": int(hex_data[128:192], 16),
                "amount1Out": int(hex_data[192:256], 16)
            }
        except:
            return None
    
    def format_amount(self, amount: int, decimals: int) -> str:
        """格式化数量"""
        value = Decimal(amount) / (Decimal(10) ** Decimal(decimals))
        if value >= 1000:
            return f"{value:,.2f}"
        elif value >= 1:
            return f"{value:.4f}"
        else:
            return f"{value:.8f}"
    
    def format_number(self, value: float) -> str:
        """格式化数字（K/M/B）"""
        if value >= 1_000_000_000:
            return f"{value/1_000_000_000:.2f}B"
        elif value >= 1_000_000:
            return f"{value/1_000_000:.2f}M"
        elif value >= 1_000:
            return f"{value/1_000:.2f}K"
        else:
            return f"{value:.2f}"
    
    def first_layer_filter(self, usd_value: float, is_internal: bool) -> bool:
        """第一层过滤：金额"""
        threshold = self.min_amount_internal if is_internal else self.min_amount_external
        return usd_value >= threshold
    
    async def check_alert_cooldown(self, token_address: str) -> bool:
        """检查代币是否在冷却期内（原子化检查并设置临时锁）"""
        redis_key = f"bsc:alert:last:{token_address.lower()}"
        lock_key = f"bsc:alert:lock:{token_address.lower()}"
        
        try:
            # 先尝试设置临时锁（5秒），防止并发
            lock_set = await asyncio.to_thread(
                self.redis_client.client.set,
                lock_key,
                "1",
                nx=True,  # 只在key不存在时设置
                ex=5      # 5秒过期
            )
            
            if not lock_set:
                # 无法获取锁，说明其他线程正在处理
                logger.debug(f"⏳ 获取锁失败，跳过: {token_address[:10]}...")
                return False
            
            # 获取锁成功，检查冷却期
            last_alert_data = await asyncio.to_thread(self.redis_client.get, redis_key)
            
            if not last_alert_data:
                return True
            
            if isinstance(last_alert_data, dict):
                last_alert = last_alert_data
            else:
                last_alert = json.loads(last_alert_data)
            
            last_timestamp = last_alert.get('timestamp', 0)
            cooldown_seconds = last_alert.get('cooldown_seconds', int(self.cooldown_minutes * 60))
            now_timestamp = int(time.time())
            
            if now_timestamp - last_timestamp < cooldown_seconds:
                # 在冷却期内，释放锁
                await asyncio.to_thread(self.redis_client.client.delete, lock_key)
                logger.info(f"⏳ 冷却期内，跳过: {token_address}(剩余 {cooldown_seconds - (now_timestamp - last_timestamp)}秒)")
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"检查冷却期失败: {e}")
            # 出错时释放锁
            try:
                await asyncio.to_thread(self.redis_client.client.delete, lock_key)
            except:
                pass
            return True
    
    async def update_alert_history(self, token_address: str):
        """更新代币推送历史（设置冷却期并释放锁）"""
        redis_key = f"bsc:alert:last:{token_address.lower()}"
        lock_key = f"bsc:alert:lock:{token_address.lower()}"
        
        try:
            last_alert_data = await asyncio.to_thread(self.redis_client.get, redis_key)
            alert_count = 1
            
            if last_alert_data:
                if isinstance(last_alert_data, dict):
                    last_alert = last_alert_data
                else:
                    last_alert = json.loads(last_alert_data)
                alert_count = last_alert.get('alert_count', 0) + 1
            
            import random
            cooldown_seconds = int(self.cooldown_minutes * 60 + random.randint(0, int(self.cooldown_jitter * 60)))
            
            alert_data = {
                'timestamp': int(time.time()),
                'alert_count': alert_count,
                'cooldown_seconds': cooldown_seconds
            }
            
            await asyncio.to_thread(
                self.redis_client.set,
                redis_key,
                json.dumps(alert_data),
                ex=600
            )
            
            # 释放锁
            await asyncio.to_thread(self.redis_client.client.delete, lock_key)
            
            logger.info(f"🔒 [冷却期] 已设置 {cooldown_seconds}秒 (约{cooldown_seconds/60:.1f}分钟) | {token_address} (第{alert_count}次)")
        
        except Exception as e:
            logger.error(f"更新推送历史失败: {e}")
            # 出错时也要释放锁
            try:
                await asyncio.to_thread(self.redis_client.client.delete, lock_key)
            except:
                pass
    
    def create_token_buttons(self, token_address: str):
        """创建代币的 Telegram 内联按钮"""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        buttons = [
            [
                InlineKeyboardButton("📊 GMGN", url=f"https://gmgn.ai/bsc/token/{token_address}"),
                InlineKeyboardButton("🔍 OKX", url=f"https://www.okx.com/web3/dex-swap#inputChain=56&inputCurrency={token_address}&outputChain=56&outputCurrency=0x55d398326f99059fF775485246999027B3197955")
            ]
        ]
        return InlineKeyboardMarkup(buttons)
    
    async def send_alert(self, message: str, token_address: str):
        """发送 Telegram 通知"""
        if not self.enable_telegram:
            return
        
        try:
            reply_markup = self.create_token_buttons(token_address)
            
            result = await TelegramAPI.send_message(
                chat_id=self.bsc_channel_id,
                message=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            
            if result.get('success'):
                print(f"✅ 已发送 Telegram 通知")
            else:
                print(f"❌ Telegram 发送失败: {result.get('error')}")
        
        except Exception as e:
            logger.error(f"❌ 发送通知异常: {e}")
    
    async def check_external_is_fourmeme(self, token_address: str) -> Optional[Dict]:
        """检查外盘代币是否来自 fourmeme 平台"""
        dbotx_api = self.get_thread_dbotx_api()
        
        try:
            launchpad_info = await dbotx_api.get_token_launchpad_info('bsc', token_address)
            
            if not launchpad_info:
                return None
            
            launchpad = launchpad_info.get('launchpad', '').lower()
            
            if launchpad != 'fourmeme':
                return None
            
            return launchpad_info
        
        except Exception as e:
            logger.error(f"❌ 检查 Launchpad 失败: {e}")
            return None
    
    async def second_layer_filter(
        self,
        token_address: str,
        pair_address: str,
        launchpad_info: Dict,
        is_internal: bool
    ) -> Optional[Dict]:
        """第二层过滤：指标检查"""
        dbotx_api = self.get_thread_dbotx_api()
        
        try:
            # 1. 使用 launchpad_info 中的 pair_address（如果有）
            api_pair_address = launchpad_info.get('pair_address')
            if api_pair_address:
                pair_address = api_pair_address
            
            # 2. 调用 DBotX API 获取代币指标
            raw_data = await dbotx_api.get_pair_info('bsc', pair_address)
            
            if not raw_data:
                print(f"⏭️  [第二层] 无 DBotX 数据: {token_address}...")
                return None
            
            # 3. 解析数据
            token_data = dbotx_api.parse_token_data(raw_data)
            if not token_data:
                logger.debug(f"⏭️  [第二层] 解析失败: {token_address}...")
                return None
            
            # 4. 判断内外盘
            launchpad_status = launchpad_info.get('launchpad_status', 0)
            is_internal = (launchpad_status == 0)
            pool_type = "内盘" if is_internal else "外盘"
            pool_emoji = "🔴" if is_internal else "🟢"
            
            # 5. 获取指标数据
            price_change_1m = token_data.get('price_change', 0)
            volume_1m = token_data.get('volume', 0)
            symbol = token_data.get('symbol', 'Unknown')
            
            # 6. 构造 stats 数据（用于 TriggerLogic）
            stats = {
                'priceChange': price_change_1m,
                'volume': volume_1m,
                'volume_1m': volume_1m,
                'holderChange': 0
            }
            
            # 7. 选择对应的 events_config
            events_config = self.internal_events_config if is_internal else self.external_events_config
            
            logger.info(f"🔎 [第二层指标检查] {pool_emoji}{pool_type} {symbol} ({token_address})")
            logger.info(f"   ├─ 1分钟涨幅: {price_change_1m:+.2f}%")
            logger.info(f"   └─ 1分钟交易量: ${volume_1m:,.2f}")
            logger.debug(f"   配置: {events_config}")
            logger.debug(f"   统计: {stats}")
            
            # 8. 使用 TriggerLogic 评估
            should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
                stats, events_config, 'any'
            )
            
            logger.debug(f"   触发结果: should_trigger={should_trigger}, triggered_events={len(triggered_events) if triggered_events else 0}")
            
            if not should_trigger:
                logger.info(f"   ❌ 未达到触发条件")
                return None
            
            # 9. 通过筛选，返回数据
            logger.info(f"   ✅ 满足条件！触发 {len(triggered_events)} 个事件")
            
            token_data['pool_type'] = pool_type
            token_data['is_internal'] = is_internal
            token_data['pool_emoji'] = pool_emoji
            token_data['triggered_events'] = triggered_events
            
            return token_data
        
        except Exception as e:
            logger.error(f"❌ 第二层过滤失败: {e}")
            return None
    
    async def handle_swap_event(self, log: Dict):
        """处理 PancakeSwap Swap 事件（外盘）"""
        tx_hash = log.get("transactionHash")
        pair_address = log.get("address", "").lower()
        swap_data = self.parse_swap_data(log.get("data"))
        
        if not swap_data:
            return
        
        token0, token1 = self.get_pair_tokens(pair_address)
        if not token0 or not token1:
            return
        
        if token0 not in (self.USDT, self.WBNB) and token1 not in (self.USDT, self.WBNB):
            return
        
        if {token0, token1} == {self.USDT, self.WBNB}:
            return
        
        # 解析交易
        amount0_in = swap_data["amount0In"]
        amount1_in = swap_data["amount1In"]
        amount0_out = swap_data["amount0Out"]
        amount1_out = swap_data["amount1Out"]
        
        quote_token = None
        base_token = None
        quote_amount = 0
        base_amount = 0
        
        if amount0_in > 0 and amount1_out > 0:
            if token0 in (self.USDT, self.WBNB):
                quote_token = token0
                base_token = token1
                quote_amount = amount0_in
                base_amount = amount1_out
        elif amount1_in > 0 and amount0_out > 0:
            if token1 in (self.USDT, self.WBNB):
                quote_token = token1
                base_token = token0
                quote_amount = amount1_in
                base_amount = amount0_out
        
        if not quote_token or not base_token:
            return
        
        # 计算 USD 价值
        quote_decimals = self.get_decimals(quote_token)
        base_decimals = self.get_decimals(base_token)
        quote_symbol = self.get_token_symbol(quote_token)
        base_symbol = self.get_token_symbol(base_token)
        
        quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
        if quote_token == self.WBNB:
            wbnb_price = self.get_wbnb_price()
            usd_value = float(quote_value) * wbnb_price
        else:
            usd_value = float(quote_value)
        
        # 第一层过滤
        if not self.first_layer_filter(usd_value, is_internal=False):
            return
        
        print(f"✅ [外盘] 通过第一层过滤: {base_symbol} (${usd_value:.2f})")
        
        # 检查冷却期
        can_alert = await self.check_alert_cooldown(base_token)
        if not can_alert:
            return
        
        # fourmeme 验证
        launchpad_info = await self.check_external_is_fourmeme(base_token)
        if not launchpad_info:
            print(f"⏭️  外盘非 fourmeme: {base_symbol}：{base_token}")
            return
        
        print(f"✓ 外盘是 fourmeme: {base_symbol}：{base_token}")
        
        # 第二层过滤
        token_data = await self.second_layer_filter(base_token, pair_address, launchpad_info, is_internal=False)
        if not token_data:
            return
        
        # 构建消息
        quote_formatted = self.format_amount(quote_amount, quote_decimals)
        base_formatted = self.format_amount(base_amount, base_decimals)
        
        pool_emoji = token_data['pool_emoji']
        pool_type = token_data['pool_type']
        symbol = token_data.get('symbol', base_symbol)
        price_change = token_data.get('price_change', 0)
        volume = token_data.get('volume', 0)
        market_cap = token_data.get('market_cap', 0)
        buy_tax = token_data.get('buy_tax', 0)
        sell_tax = token_data.get('sell_tax', 0)
        price = token_data.get('price', 0)
        
        volume_str = self.format_number(volume)
        market_cap_str = self.format_number(market_cap)
        
        price_str = f"${price:.5f} USDT" if price >= 0.01 else f"${price:.10f} USDT"
        
        triggered_events = token_data.get('triggered_events', [])
        alert_reasons = []
        for event in triggered_events:
            if hasattr(event, 'description'):
                alert_reasons.append(event.description)
            elif isinstance(event, dict):
                if event.get('event') == 'priceChange':
                    alert_reasons.append(f"📈 1分钟涨幅 {price_change:+.2f}%")
                elif event.get('event') == 'volume':
                    alert_reasons.append(f"💹 1分钟交易量 ${volume_str}")
        
        if not alert_reasons:
            alert_reasons.append(f"💰 大额交易 ${usd_value:.2f}")
        
        message = f"""<b>🟢 BSC 信号</b>

💰 代币: {symbol}
📝 名称: {symbol}
🔗 合约: <code>{base_token}</code>

📊 <b>实时数据</b>
💵 当前价格: {price_str}
💎 市值: ${market_cap_str}
🏊 状态: {pool_emoji} {pool_type}

📉 <b>交易数据</b>
💰 本次买入: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})
🎁 获得代币: {base_formatted} {symbol}

✨ <b>触发原因</b>
{chr(10).join('• ' + reason for reason in alert_reasons)}

⏰ 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        print(f"\n{'='*80}")
        print(f"{pool_emoji} 【{pool_type}】{quote_symbol} 买入 {symbol}")
        print(f"付出: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})")
        print(f"得到: {base_formatted} {symbol}")
        print(f"涨幅: {price_change:+.2f}% | 交易量: ${volume:,.0f} | 市值: ${market_cap:,.0f}")
        print(f"税率: 买{buy_tax:.1f}% / 卖{sell_tax:.1f}%")
        print(f"地址: {base_token}")
        print(f"{'='*80}\n")
        
        # 发送推送
        await self.send_alert(message, base_token)
        print(f"✅ 已发送 Telegram 通知")
        
        # 设置冷却期
        await self.update_alert_history(base_token)
    
    async def handle_proxy_event(self, log: Dict):
        """处理 Fourmeme Proxy 事件（内盘）"""
        tx_hash = log.get("transactionHash")
        addr = log.get("address", "").lower()
        
        proxy_type = "主Proxy" if addr == self.FOURMEME_PROXY[0] else "Try Buy"
        
        try:
            dbotx_api = self.get_thread_dbotx_api()
            
            receipt = self.rpc_call("eth_getTransactionReceipt", [tx_hash])
            if not receipt:
                return
            
            logs = receipt.get("logs", [])
            
            # 解析 Transfer 事件
            transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
            transfers = []
            
            for log in logs:
                topics = log.get("topics", [])
                if not topics or topics[0] != transfer_topic:
                    continue
                
                token_addr = log.get("address", "").lower()
                data = log.get("data", "0x")
                
                if len(topics) >= 3:
                    from_addr = "0x" + topics[1][-40:]
                    to_addr = "0x" + topics[2][-40:]
                    
                    try:
                        value = int(data, 16) if data and data != "0x" else 0
                    except:
                        value = 0
                    
                    transfers.append({
                        "token": token_addr,
                        "from": from_addr.lower(),
                        "to": to_addr.lower(),
                        "value": value
                    })
            
            if not transfers:
                return
            
            # 找出买入的 USDT/WBNB
            usdt_in = sum(t["value"] for t in transfers 
                         if t["token"] == self.USDT and t["to"] in self.FOURMEME_PROXY)
            wbnb_in = sum(t["value"] for t in transfers 
                         if t["token"] == self.WBNB and t["to"] in self.FOURMEME_PROXY)
            
            # 获取交易信息（BNB 买入）
            tx_info = self.rpc_call("eth_getTransactionByHash", [tx_hash])
            tx_value = 0
            if tx_info and tx_info.get("value"):
                try:
                    tx_value = int(tx_info["value"], 16)
                except:
                    pass
            
            # 确定付出的基准币
            quote_token = None
            quote_amount = 0
            quote_symbol = ""
            
            if usdt_in > 0:
                quote_token = self.USDT
                quote_amount = usdt_in
                quote_symbol = "USDT"
            elif wbnb_in > 0:
                quote_token = self.WBNB
                quote_amount = wbnb_in
                quote_symbol = "WBNB"
            elif tx_value > 0:
                quote_token = self.WBNB
                quote_amount = tx_value
                quote_symbol = "BNB"
            else:
                return
            
            # 找出目标代币
            target_tokens = {}
            for t in transfers:
                if (t["from"] in self.FOURMEME_PROXY and 
                    t["token"] not in (self.USDT, self.WBNB)):
                    target_tokens[t["token"]] = target_tokens.get(t["token"], 0) + t["value"]
            
            if not target_tokens:
                return
            
            target_token = max(target_tokens.items(), key=lambda x: x[1])[0]
            target_amount = target_tokens[target_token]
            
            target_symbol = self.get_token_symbol(target_token)
            quote_decimals = self.get_decimals(quote_token)
            target_decimals = self.get_decimals(target_token)
            
            # 计算 USD 价值
            quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
            if quote_token == self.WBNB:
                wbnb_price = self.get_wbnb_price()
                usd_value = float(quote_value) * wbnb_price
            else:
                usd_value = float(quote_value)
            
            # 第一层过滤
            if not self.first_layer_filter(usd_value, is_internal=True):
                print(f"⏭️  内盘金额不足: {target_symbol} (${usd_value:.2f})")
                return
            
            print(f"✅ [内盘] 通过第一层过滤: {target_symbol} (${usd_value:.2f})")
            
            # 检查冷却期
            can_alert = await self.check_alert_cooldown(target_token)
            if not can_alert:
                return
            
            # 获取 launchpad 信息
            launchpad_info = await dbotx_api.get_token_launchpad_info('bsc', target_token)
            if not launchpad_info:
                print(f"⏭️  内盘无 Launchpad 信息: {target_symbol}")
                return
            
            pair_address = launchpad_info.get('pair_address')
            if not pair_address:
                print(f"⏭️  内盘无交易对地址: {target_symbol}")
                return
            
            # 第二层过滤
            token_data = await self.second_layer_filter(target_token, pair_address, launchpad_info, is_internal=True)
            if not token_data:
                return
            
            # 构建消息
            quote_formatted = self.format_amount(quote_amount, quote_decimals)
            target_formatted = self.format_amount(target_amount, target_decimals)
            
            pool_emoji = token_data['pool_emoji']
            pool_type = token_data['pool_type']
            symbol = token_data.get('symbol', target_symbol)
            price_change = token_data.get('price_change', 0)
            volume = token_data.get('volume', 0)
            market_cap = token_data.get('market_cap', 0)
            buy_tax = token_data.get('buy_tax', 0)
            sell_tax = token_data.get('sell_tax', 0)
            price = token_data.get('price', 0)
            
            volume_str = self.format_number(volume)
            market_cap_str = self.format_number(market_cap)
            
            price_str = f"${price:.5f} USDT" if price >= 0.01 else f"${price:.10f} USDT"
            
            triggered_events = token_data.get('triggered_events', [])
            alert_reasons = []
            for event in triggered_events:
                if hasattr(event, 'description'):
                    alert_reasons.append(event.description)
                elif isinstance(event, dict):
                    if event.get('event') == 'priceChange':
                        alert_reasons.append(f"📈 1分钟涨幅 {price_change:+.2f}%")
                    elif event.get('event') == 'volume':
                        alert_reasons.append(f"💹 1分钟交易量 ${volume_str}")
            
            if not alert_reasons:
                alert_reasons.append(f"💰 大额交易 ${usd_value:.2f}")
            
            message = f"""<b>{pool_emoji} BSC 信号</b>

💰 代币: {symbol}
📝 名称: {symbol}
🔗 合约: <code>{target_token}</code>

📊 <b>实时数据</b>
💵 当前价格: {price_str}
💎 市值: ${market_cap_str}
🏊 状态: {pool_emoji} {pool_type}

📉 <b>交易数据</b>
💰 本次买入: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})
🎁 获得代币: {target_formatted} {symbol}

✨ <b>触发原因</b>
{chr(10).join('• ' + reason for reason in alert_reasons)}

⏰ 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""
            
            print(f"\n{'='*80}")
            print(f"{pool_emoji} 【{pool_type} - {proxy_type}】{quote_symbol} 买入 {symbol}")
            print(f"付出: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})")
            print(f"得到: {target_formatted} {symbol}")
            print(f"涨幅: {price_change:+.2f}% | 交易量: ${volume:,.0f} | 市值: ${market_cap:,.0f}")
            print(f"税率: 买{buy_tax:.1f}% / 卖{sell_tax:.1f}%")
            print(f"地址: {target_token}")
            print(f"{'='*80}\n")
            
            # 发送推送
            await self.send_alert(message, target_token)
            print(f"✅ 已发送 Telegram 通知")
            
            # 设置冷却期
            await self.update_alert_history(target_token)
        
        except Exception as e:
            logger.error(f"❌ 处理内盘交易出错: {e}")
    
    def on_message(self, ws, message):
        """WebSocket 消息回调"""
        try:
            msg = json.loads(message)
            
            # 跳过订阅确认
            if "id" in msg:
                return
            
            # 获取日志
            params = msg.get("params", {})
            result = params.get("result", {})
            
            if not isinstance(result, dict):
                return
            
            # 去重
            tx_hash = result.get("transactionHash")
            if tx_hash:
                if tx_hash in self.seen_txs:
                    return
                
                self.seen_txs.add(tx_hash)
                
                if len(self.seen_txs) > self.max_seen_txs:
                    self.seen_txs.clear()
            
            # 判断事件类型
            topics = result.get("topics", [])
            addr = result.get("address", "").lower()
            
            if not topics:
                return
            
            # Swap 事件（外盘）
            if topics[0] == self.TOPIC_V2_SWAP:
                self.executor.submit(self._run_async_handler, self.handle_swap_event(result))
            
            # Proxy 事件（内盘）
            elif addr in self.FOURMEME_PROXY:
                self.executor.submit(self._run_async_handler, self.handle_proxy_event(result))
        
        except Exception as e:
            logger.error(f"❌ 处理消息出错: {e}")
    
    def _run_async_handler(self, coro):
        """在新事件循环中运行异步处理器"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    
    def on_open(self, ws):
        """WebSocket 连接成功回调"""
        if self.reconnect_count == 0:
            logger.info("✅ WebSocket 连接成功！")
            logger.info(f"节点: {self.ws_url[:50]}")
        else:
            logger.info(f"✅ WebSocket 重连成功！(第{self.reconnect_count}次)")
        
        self.reconnect_count += 1
        
        # 订阅 Proxy 事件
        for idx, proxy_addr in enumerate(self.FOURMEME_PROXY, start=1):
            ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": idx,
                "method": "eth_subscribe",
                "params": ["logs", {"address": proxy_addr}]
            }))
            logger.info(f"✓ 订阅 Proxy #{idx}: {proxy_addr}")
        
        # 订阅 Swap 事件
        swap_id = len(self.FOURMEME_PROXY) + 1
        ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": swap_id,
            "method": "eth_subscribe",
            "params": ["logs", {"topics": [self.TOPIC_V2_SWAP]}]
        }))
        logger.info(f"✓ 订阅 PancakeV2 Swap")
        
        logger.info("✅ 已订阅事件监听")
        logger.info(f"📱 Telegram 频道: {self.bsc_channel_id}")
        logger.info(f"⏳ 等待链上交易...")
    
    def on_error(self, ws, error):
        """WebSocket 错误回调"""
        logger.error(f"❌ WebSocket 错误: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 关闭回调"""
        if self.should_stop:
            logger.info(f"✅ WebSocket 连接已关闭")
        else:
            logger.warning(f"⚠️  WebSocket 连接断开: {close_status_code} - {close_msg}")
            logger.info("🔄 将在5秒后自动重连...")
    
    def signal_handler(self, signum, frame):
        """信号处理器（Ctrl+C）"""
        logger.info("\n⚠️  收到停止信号，正在关闭...")
        self.should_stop = True
        
        if self.ws:
            self.ws.close()
        
        self.executor.shutdown(wait=False)
        
        import os
        os._exit(0)
    
    async def start(self):
        """启动监控"""
        # 加载配置
        await self.load_config_from_redis()
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # 创建 WebSocket（添加 ping/pong 心跳保活）
        websocket.enableTrace(False)
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # 在单独线程中运行 WebSocket（添加心跳参数）
        def run_ws():
            self.ws.run_forever(
                ping_interval=30,  # 每30秒发送一次ping
                ping_timeout=10,   # ping超时时间10秒
                reconnect=5        # 断线后5秒重连
            )
        
        import threading
        ws_thread = threading.Thread(target=run_ws, daemon=True)
        ws_thread.start()
        
        # 保持主线程运行
        try:
            while not self.should_stop:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("⚠️  收到中断信号")
        finally:
            self.should_stop = True
            if self.ws:
                self.ws.close()
            self.executor.shutdown(wait=False)

