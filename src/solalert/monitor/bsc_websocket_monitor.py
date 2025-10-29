"""
BSC WebSocket 监控器
使用 WebSocket 实时监听链上事件，替代 Alchemy Webhook
"""
import json
import time
import asyncio
import logging
import signal
import random
import re
import websocket
import requests
import traceback
import urllib3
import threading
import os
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from collections import OrderedDict
from ..api.telegram_api import TelegramAPI
from ..api.dbotx_api import DBotXAPI
from ..core.redis_client import get_redis
from ..core.config import TELEGRAM_CONFIG
from .trigger_logic import TriggerLogic
from ..notifiers.alert_recorder import get_alert_recorder
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 可选依赖：eth_abi（用于 Multicall2）
try:
    from eth_abi import encode as eth_abi_encode, decode as eth_abi_decode
    HAS_ETH_ABI = True
except ImportError:
    HAS_ETH_ABI = False
    eth_abi_encode = None
    eth_abi_decode = None

# 可选依赖：telegram（用于按钮）
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    HAS_TELEGRAM_BUTTONS = True
except ImportError:
    HAS_TELEGRAM_BUTTONS = False
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None

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
        
        # Alert Recorder（用于记录到数据库和推送WebSocket）
        self.alert_recorder = get_alert_recorder()
        
        # 常量
        self.USDT = "0x55d398326f99059ff775485246999027b3197955"
        self.WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        self.USDC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
        self.FOURMEME_PROXY = [
            "0x5c952063c7fc8610ffdb798152d69f0b9550762b".lower(),  # 主Proxy
            "0x8e06ab256ca534ebba05d700f8e40341ec39e0d6".lower()   # Try Buy
        ]
        self.TOPIC_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
        
        # Fourmeme 自定义事件（可捕获内部调用）
        self.FOURMEME_CUSTOM_EVENTS = [
            "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942",  # 事件1
            "0x48063b1239b68b5d50123408787a6df1f644d9160f0e5f702fefddb9a855954d"   # 事件2
        ]
        
        # Multicall2 配置（BSC）
        # 注意：BSC 上有多个 Multicall 实现，优先使用跨链通用的 Multicall3
        self.MULTICALL2_ADDRESS = "0xcA11bde05977b3631167028862bE2A173976CA11"  # Multicall3（跨链通用地址）
        # tryAggregate 函数选择器: tryAggregate(bool requireSuccess, tuple[] calls)
        # Multicall3 也支持此函数，向后兼容 Multicall2
        self.MULTICALL2_TRY_AGGREGATE_SELECTOR = "bce38bd7"  # 不带0x前缀
        
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
        
        # 时间间隔和Top持有者阈值（从 Redis 加载）
        self.time_interval_internal = '1m'  # 内盘默认1分钟
        self.time_interval_external = '5m'  # 外盘默认5分钟
        self.top_holders_threshold_internal = None  # 内盘Top持有者阈值（None表示不检查）
        self.top_holders_threshold_external = None  # 外盘Top持有者阈值（None表示不检查）
        
        # 默认 events_config（后备配置）
        self.internal_events_config = {
            'priceChange': {'enabled': True, 'risePercent': 30},  # 默认：内盘涨幅 >= 30%
            'volume': {'enabled': True, 'threshold': 5000}        # 默认：内盘交易量 >= $5000
        }
        self.external_events_config = {
            'priceChange': {'enabled': True, 'risePercent': 50},  # 默认：外盘涨幅 >= 50%
            'volume': {'enabled': True, 'threshold': 20000}       # 默认：外盘交易量 >= $20000
        }
        
        # 触发逻辑（默认值）
        self.trigger_logic_internal = 'any'  # 内盘触发逻辑
        self.trigger_logic_external = 'any'  # 外盘触发逻辑
        
        # WebSocket
        self.ws = None
        self.should_stop = False
        self.reconnect_count = 0  # 重连计数
        self.last_message_time = time.time()  # 最后一次收到消息的时间
        self.message_count = 0  # 消息计数器
        self.cache_hit_count = 0  # 非fourmeme缓存命中计数
        
        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="BSC-WS-Worker")
        self.thread_local = threading.local()
        
        # 交易去重（使用 tx_hash:logIndex 组合键，支持多日志处理）
        self.seen_txs = OrderedDict()
        self.max_seen_txs = 100000  # 增大容量以适应 (tx_hash, logIndex) 组合键
        
        # WBNB 价格缓存
        self.wbnb_price = 600.0
        self.wbnb_price_timestamp = 0
        self.price_cache_ttl = 300  # 5分钟
        

        
        self.session = requests.Session()
        # 配置连接池和重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(
            pool_connections=10,  # 连接池大小
            pool_maxsize=20,      # 最大连接数
            max_retries=retry_strategy
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # RPC 调用计数
        self.rpc_id = 0
        
        # 断线回补
        self.last_processed_block = 0
        self.reconnect_time = 0
        self.last_backfill_time = 0  # 上次回补时间
        self.backfill_cooldown = 60  # 回补冷却期（60秒）
        self.backfill_count = 0  # 回补次数统计
        
        # 回执缓存（减少 eth_getTransactionReceipt 重复调用）
        self.receipt_cache = {}  # {tx_hash: {"receipt": {}, "tx_info": {}, "cached_at": timestamp}}
        self.receipt_cache_ttl = 60  # 60秒过期
        self.receipt_cache_hits = 0  # 命中计数
        self.receipt_cache_lock = threading.Lock()  # 线程安全锁
        
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

                internal_data = re.sub(r'"@type"\s*:\s*"[^"]*"\s*,?\s*', '', internal_data)
                internal_data = re.sub(r':\s*(\d+)L\b', r':\1', internal_data)
                internal_data = re.sub(r',\s*}', '}', internal_data)
                
                config = json.loads(internal_data)
                self.min_amount_internal = config.get('min_transaction_usd', 200)
                self.cumulative_min_amount_internal = config.get('cumulative_min_amount_usd', 500)
                self.time_interval_internal = config.get('timeInterval', '1m')  # 内盘时间间隔
                self.trigger_logic_internal = config.get('triggerLogic', 'any')  # 内盘触发逻辑
                # topHoldersThreshold：如果配置了就启用检查，否则为None（不检查）
                threshold = config.get('topHoldersThreshold')
                self.top_holders_threshold_internal = float(threshold) if threshold is not None else None
                
                events_config_str = config.get('eventsConfig', '{}')
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
                self.cumulative_min_amount_external = config.get('cumulative_min_amount_usd', 1000)  
                self.time_interval_external = config.get('timeInterval', '5m')  # 外盘时间间隔
                self.trigger_logic_external = config.get('triggerLogic', 'any')  # 外盘触发逻辑
                # topHoldersThreshold：如果配置了就启用检查，否则为None（不检查）
                threshold = config.get('topHoldersThreshold')
                self.top_holders_threshold_external = float(threshold) if threshold is not None else None  
                
                events_config_str = config.get('eventsConfig', '{}')
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
        logger.info(f"📊 内盘配置: 单笔>={self.min_amount_internal}U, 累计>={self.cumulative_min_amount_internal}U, 涨幅>={self.internal_events_config.get('priceChange', {}).get('risePercent')}%, 交易量>=${self.internal_events_config.get('volume', {}).get('threshold')}, 触发逻辑={self.trigger_logic_internal}")
        logger.info(f"📊 外盘配置: 单笔>={self.min_amount_external}U, 累计>={self.cumulative_min_amount_external}U, 涨幅>={self.external_events_config.get('priceChange', {}).get('risePercent')}%, 交易量>=${self.external_events_config.get('volume', {}).get('threshold')}, 触发逻辑={self.trigger_logic_external}")
        
        # 性能优化说明
        logger.info("✨ 性能优化: 已启用三层缓存架构 (L1: 内存LRU / L2: Redis持久化 / L3: Multicall3批量查询)")
        logger.info(f"✨ Multicall3: {self.MULTICALL2_ADDRESS} (跨链通用地址)")
        logger.info(f"✨ eth-abi 状态: {'✅ 已安装' if HAS_ETH_ABI else '❌ 未安装（将使用手动编码）'}")
        logger.info("✨ 支持代币: USDT, USDC, WBNB (可扩展)")
        logger.info("✨ 优化效果: 缓存命中0次RPC / 全miss仅1次Multicall3 (vs 旧版6次eth_call)")
        
        # 预加载 WBNB 价格（在线程池中执行，避免阻塞事件循环）
        self.wbnb_price = await asyncio.to_thread(self.get_wbnb_price)
        logger.info(f"💰 WBNB 价格: ${self.wbnb_price:.2f}")
        
        # 统计非fourmeme缓存大小并确保TTL
        self.NON_FOURMEME_KEY = "bsc:non_fourmeme_tokens"
        self.NON_FOURMEME_TTL = 30 * 24 * 3600  # 30天
        
        if self.redis_client:
            try:
                cache_size = self.redis_client.scard(self.NON_FOURMEME_KEY)
                # 确保缓存有过期时间（防止永久存储）
                ttl = self.redis_client.client.ttl(self.NON_FOURMEME_KEY)
                if ttl == -1:  # -1 表示没有过期时间
                    self.redis_client.client.expire(self.NON_FOURMEME_KEY, self.NON_FOURMEME_TTL)
                    logger.info(f"📊 非fourmeme缓存: {cache_size} 个token (已设置30天过期)")
                else:
                    logger.info(f"📊 非fourmeme缓存: {cache_size} 个token (剩余{ttl // 86400}天)")
            except Exception as e:
                logger.debug(f"获取缓存统计失败: {e}")
    
    def get_thread_dbotx_api(self) -> DBotXAPI:
        """获取当前线程的 DBotX API 实例"""
        if not hasattr(self.thread_local, 'dbotx_api'):
            self.thread_local.dbotx_api = DBotXAPI()
        return self.thread_local.dbotx_api
    
    def get_receipt_cached(self, tx_hash: str) -> tuple:
        """
        获取交易回执（带缓存，60秒TTL，线程安全）
        
        Returns:
            (receipt, tx_info) 或 (None, None) 如果失败
        """
        now = time.time()
        
        # 1. 检查缓存（线程安全）
        with self.receipt_cache_lock:
            if tx_hash in self.receipt_cache:
                cached_data = self.receipt_cache[tx_hash]
                cached_at = cached_data.get("cached_at", 0)
                
                # 未过期
                if now - cached_at < self.receipt_cache_ttl:
                    receipt = cached_data.get("receipt")
                    tx_info = cached_data.get("tx_info")
                    
                    # 验证数据完整性（防止脏数据）
                    if receipt and isinstance(receipt, dict) and receipt.get("logs"):
                        self.receipt_cache_hits += 1
                        logger.debug(f"✅ 回执缓存命中: {tx_hash[:10]}... (#{self.receipt_cache_hits})")
                        return receipt, tx_info
                    else:
                        # 脏数据，删除并重新拉取
                        logger.debug(f"⚠️ 回执缓存数据不完整，重新拉取: {tx_hash[:10]}...")
                        del self.receipt_cache[tx_hash]
        
        # 2. 缓存未命中，调用 RPC（锁外执行，避免阻塞其他线程）
        logger.debug(f"🔍 回执缓存未命中，调用RPC: {tx_hash[:10]}...")
        receipt = self.rpc_call("eth_getTransactionReceipt", [tx_hash])
        tx_info = self.rpc_call("eth_getTransactionByHash", [tx_hash])
        
        # 3. 写入缓存（线程安全）
        with self.receipt_cache_lock:
            # 双重检查：可能其他线程已经写入了
            if tx_hash not in self.receipt_cache or now - self.receipt_cache[tx_hash].get("cached_at", 0) > self.receipt_cache_ttl:
                self.receipt_cache[tx_hash] = {
                    "receipt": receipt,
                    "tx_info": tx_info,
                    "cached_at": now
                }
            
            # 4. 清理过期缓存（防止内存泄漏）
            if len(self.receipt_cache) > 5000:  # 超过 5k 条
                to_delete = [
                    k for k, v in self.receipt_cache.items()
                    if now - v.get("cached_at", 0) > self.receipt_cache_ttl
                ]
                for k in to_delete:
                    del self.receipt_cache[k]
                if to_delete:
                    logger.debug(f"🧹 清理过期回执缓存: {len(to_delete)} 条")
        
        return receipt, tx_info
    
    def rpc_call(self, method: str, params: list):
        """发送 HTTP RPC 请求（使用长连接 + 慢调用监控）"""
        self.rpc_id += 1
        start_time = time.time()
        
        try:
            # 使用长连接（Session）
            resp = self.session.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": self.rpc_id, "method": method, "params": params},
                timeout=10
            )
            result = resp.json().get("result")
            
            # 慢调用监控（超过 1 秒）
            latency = time.time() - start_time
            if latency > 1.0:
                logger.warning("RPC慢调用", extra={
                    "method": method,
                    "latency": f"{latency:.2f}s",
                    "params_count": len(params)
                })
            
            return result
        except Exception as e:
            latency = time.time() - start_time
            logger.debug(f"RPC 错误: {e}", extra={
                "method": method,
                "latency": f"{latency:.2f}s"
            })
            return None
    
    def multicall2_try_aggregate(self, calls: list) -> list:
        """
        使用 Multicall2.tryAggregate 批量查询
        优先使用 eth_abi，无库时使用修正后的手动编码
        
        Args:
            calls: [(target_address, calldata), ...] 调用列表
        
        Returns:
            [result1, result2, ...] 结果列表（失败返回 None）
        """
        if not calls:
            return []
        
        try:
            # 路径1: 使用 eth_abi（推荐，结构准确）
            if HAS_ETH_ABI:
                # tryAggregate(bool requireSuccess, (address,bytes)[] calls)
                call_tuples = []
                for target, calldata in calls:
                    target_bytes = bytes.fromhex(target[2:] if target.startswith('0x') else target)
                    calldata_bytes = bytes.fromhex(calldata[2:] if calldata.startswith('0x') else calldata)
                    call_tuples.append((target_bytes, calldata_bytes))
                
                # 编码参数：requireSuccess=false, calls
                encoded_args = eth_abi_encode(
                    ['bool', '(address,bytes)[]'],
                    [False, call_tuples]
                )
                
                # 构建完整的 calldata
                full_calldata = self.MULTICALL2_TRY_AGGREGATE_SELECTOR + encoded_args.hex()
                
                # 调用 Multicall2
                result = self.rpc_call("eth_call", [{
                    "to": self.MULTICALL2_ADDRESS,
                    "data": "0x" + full_calldata
                }, "latest"])
                
                if not result or result == "0x":
                    logger.warning(f"⚠️ Multicall2 返回空结果 (eth_abi)，回退到逐个调用")
                    logger.debug(f"调用数量: {len(calls)}, Calldata长度: {len(full_calldata)}")
                    return self._fallback_individual_calls(calls)
                
                # 解码结果
                try:
                    result_bytes = bytes.fromhex(result[2:] if result.startswith('0x') else result)
                    decoded = eth_abi_decode(['(bool,bytes)[]'], result_bytes)[0]
                    
                    results = []
                    for success, return_data in decoded:
                        if success and return_data:
                            results.append('0x' + return_data.hex())
                        else:
                            results.append(None)
                    
                    logger.info(f"✅ Multicall2 批量查询成功 (eth_abi): {len(results)} 个调用")
                    return results
                except Exception as decode_error:
                    logger.warning(f"⚠️ eth_abi 解码失败: {decode_error}, 回退到逐个调用")
                    return self._fallback_individual_calls(calls)
            
            # 路径2: 手动编码（修正后，无依赖）
            sig = "bce38bd7"  # tryAggregate selector
            ignore_results = "00" * 32  # bool False (32 bytes)
            
            # Array offset: 0x20 (after bool)
            array_offset = format(0x20, '064x')  # 32 bytes padded
            
            # Array length
            array_len_hex = format(len(calls), '064x')
            
            # Array data: 对于 tuple[] 类型，需要嵌套偏移
            # 每个元素是一个 tuple，包含 address + bytes（动态）
            # 结构：[offset1, offset2, ...] + [tuple1_data, tuple2_data, ...]
            
            tuple_offsets = []
            tuple_contents = []
            
            # 偏移基准：len(calls) * 32（每个偏移占32字节）
            base_offset = len(calls) * 32
            current_offset = base_offset
            
            for target, calldata in calls:
                target_clean = target[2:] if target.startswith('0x') else target
                calldata_clean = calldata[2:] if calldata.startswith('0x') else calldata
                
                # 记录当前 tuple 的偏移
                tuple_offsets.append(format(current_offset, '064x'))
                
                # 构建 tuple 内容：address (32b) + bytes_offset (0x20) + bytes_len + bytes_data
                address_padded = target_clean.zfill(64)  # 32 bytes
                bytes_offset_in_tuple = format(0x20, '064x')  # bytes 在 tuple 内偏移 32 字节（address 后）
                
                calldata_len = len(calldata_clean) // 2
                calldata_len_hex = format(calldata_len, '064x')
                calldata_full = calldata_clean  # 动态数据，不需要 padding
                
                tuple_content = address_padded + bytes_offset_in_tuple + calldata_len_hex + calldata_full
                tuple_contents.append(tuple_content)
                
                # 更新偏移（以字节为单位）
                current_offset += len(tuple_content) // 2
            
            # 组装数组数据
            array_data = "".join(tuple_offsets) + "".join(tuple_contents)
            
            # 完整编码
            encoded_args = ignore_results + array_offset + array_len_hex + array_data
            full_data = sig + encoded_args
            
            # 调用 RPC
            result = self.rpc_call("eth_call", [{
                "to": self.MULTICALL2_ADDRESS,
                "data": "0x" + full_data
            }, "latest"])
            
            if not result or result == "0x":
                logger.warning("⚠️ Multicall2 返回空结果 (manual)，回退到逐个调用")
                logger.debug(f"Full data len: {len(full_data)}, first 100: {full_data[:100]}")
                return self._fallback_individual_calls(calls)
            
            # 手动解析返回值: (bool success, bytes returnData)[] 数组
            result_hex = result[2:] if result.startswith('0x') else result
            
            # 数组偏移（通常是0x20）
            array_start = int(result_hex[0:64], 16) * 2
            # 数组长度
            array_len = int(result_hex[array_start:array_start+64], 16)
            
            results = []
            offset = array_start + 64  # 跳过长度字段
            
            # 读取每个元素的偏移（相对于数组开始位置）
            result_offsets = []
            for i in range(array_len):
                elem_offset = int(result_hex[offset:offset+64], 16) * 2
                result_offsets.append(array_start + elem_offset)
                offset += 64
            
            # 解析每个 (bool, bytes) tuple
            for elem_offset in result_offsets:
                success = int(result_hex[elem_offset:elem_offset+64], 16)
                bytes_offset = int(result_hex[elem_offset+64:elem_offset+128], 16) * 2
                bytes_start = elem_offset + bytes_offset
                bytes_len = int(result_hex[bytes_start:bytes_start+64], 16)
                
                if success == 1 and bytes_len > 0:
                    ret_data = "0x" + result_hex[bytes_start+64:bytes_start+64+bytes_len*2]
                    results.append(ret_data)
                else:
                    results.append(None)
            
            logger.info(f"✅ Multicall2 批量查询成功 (manual): {len(results)} 个调用")
            return results
            
        except Exception as e:
            logger.warning(f"⚠️ Multicall2 调用失败: {e}, 回退到逐个调用")
            logger.debug(f"错误详情: {traceback.format_exc()}")
            return self._fallback_individual_calls(calls)
    
    def _fallback_individual_calls(self, calls: list) -> list:
        """回退方案：逐个调用"""
        results = []
        for target, calldata in calls:
            try:
                result = self.rpc_call("eth_call", [{
                    "to": target,
                    "data": calldata
                }, "latest"])
                results.append(result)
            except Exception as e:
                logger.debug(f"调用失败 {target}: {e}")
                results.append(None)
        return results
    
    def _extract_pair_from_receipt(self, logs: list) -> str:
        """从 receipt logs 中提取 PancakeV2 Pair 地址"""
        try:
            swap_topic = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
            for log in logs:
                topics = log.get("topics", [])
                if topics and topics[0] == swap_topic:
                    # Swap 事件的地址就是 Pair 地址
                    return log.get("address", "").lower()
        except Exception as e:
            logger.debug(f"提取 pair 失败: {e}")
        return None
    
    def get_wbnb_price(self) -> float:
        """动态获取 WBNB 价格（带缓存）"""
        now = time.time()
        if now - self.wbnb_price_timestamp < self.price_cache_ttl:
            return self.wbnb_price
        
        try:
            # 禁用 SSL 警告
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            # 使用长连接（Session）
            resp = self.session.get(
                'https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BNB_USDT',
                timeout=5,
                verify=False  # 禁用 SSL 证书验证
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
        """获取代币精度（L1 内存缓存 + L2 Redis缓存 + L3 链上查询）"""
        # L1: LRU Cache 已通过装饰器处理
        
        try:
            # L2: Redis 缓存
            redis_key = f"token:{token}:decimals"
            cached_value = self.redis_client.client.get(redis_key)
            if cached_value:
                try:
                    value = int(cached_value)
                    return value
                except:
                    pass
            
            # L3: 链上查询
            data = self.rpc_call("eth_call", [{
                "to": token,
                "data": "0x313ce567"  # decimals()
            }, "latest"])
            
            result = int(data, 16) if data else 18
            
            # 写入 Redis (TTL=1天)
            try:
                self.redis_client.client.setex(redis_key, 86400, str(result))
            except:
                pass
            
            return result
        except:
            return 18
    
    def parse_symbol_data(self, data: str) -> str:
        """解析 symbol() 返回的数据"""
        if not data or data == "0x":
            return "???"
        
        try:
            hex_data = data[2:] if data.startswith('0x') else data
            
            # 动态字符串：offset(32) + length(32) + data
            if len(hex_data) >= 128:
                length = int(hex_data[64:128], 16)
                data_hex = hex_data[128:128 + length * 2]
                if data_hex:
                    return bytes.fromhex(data_hex).decode('utf-8', errors='ignore').rstrip('\x00')
            
            # 固定长度字符串（直接编码）
            if len(hex_data) == 64:
                return bytes.fromhex(hex_data).decode('utf-8', errors='ignore').rstrip('\x00')
            
            return "???"
        except Exception as e:
            logger.debug(f"解析 symbol 失败: {e}")
            return "???"
    
    @lru_cache(maxsize=10000)
    def get_token_symbol(self, token: str) -> str:
        """获取代币符号（L1 内存缓存 + L2 Redis缓存 + L3 链上查询）"""
        # L1: LRU Cache 已通过装饰器处理
        
        try:
            # L2: Redis 缓存
            redis_key = f"token:{token}:symbol"
            cached_value = self.redis_client.client.get(redis_key)
            if cached_value:
                if isinstance(cached_value, bytes):
                    cached_value = cached_value.decode('utf-8')
                return cached_value
            
            # L3: 链上查询
            data = self.rpc_call("eth_call", [{
                "to": token,
                "data": "0x95d89b41"  # symbol()
            }, "latest"])
            
            # 使用改进的解析函数
            result = self.parse_symbol_data(data)
            
            # 写入 Redis (TTL=1天)
            try:
                self.redis_client.client.setex(redis_key, 86400, result)
            except:
                pass
            
            return result
        except:
            return "???"
    
    @lru_cache(maxsize=5000)
    def get_pair_tokens(self, pair: str) -> tuple:
        """获取交易对的 token0 和 token1（L1 内存缓存 + L2 Redis缓存 + L3 链上查询）"""
        # L1: LRU Cache 已通过装饰器处理
        
        try:
            # L2: Redis 缓存
            redis_key = f"pair:{pair}:tokens"
            cached_value = self.redis_client.client.get(redis_key)
            if cached_value:
                if isinstance(cached_value, bytes):
                    cached_value = cached_value.decode('utf-8')
                parts = cached_value.split(',')
                if len(parts) == 2:
                    return parts[0], parts[1]
            
            # L3: 链上查询
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
                token0 = token0.lower()
                token1 = token1.lower()
                
                # 写入 Redis (TTL=1天)
                try:
                    self.redis_client.client.setex(redis_key, 86400, f"{token0},{token1}")
                except:
                    pass
                
                return token0, token1
        except:
            pass
        return None, None
    
    def get_pair_full_info(self, pair_address: str) -> Optional[Dict]:
        """
        获取交易对完整信息（优化版：缓存 → Multicall2批量查询）
        
        Returns:
            {
                'token0': '0x...',
                'token1': '0x...',
                'decimals0': 18,
                'symbol0': 'USDT',
                'decimals1': 18,
                'symbol1': 'TOKEN'
            }
        """
        try:
            # 第一步：获取 token0 和 token1（走缓存）
            token0, token1 = self.get_pair_tokens(pair_address)
            
            if not token0 or not token1:
                return None
            
            # 第二步：智能批量查询（先查缓存，收集 miss，批量调用）
            result = {
                'token0': token0,
                'token1': token1,
                'decimals0': None,
                'symbol0': None,
                'decimals1': None,
                'symbol1': None
            }
            
            # 检查 L1 (LRU) 缓存
            # 注意：@lru_cache 的缓存检查需要实际调用，但会走内部的 L2 (Redis) 逻辑
            miss_calls = []  # [(token, calldata, field_name)]
            
            # 检查 token0 decimals 缓存（直接查 Redis，不触发链上查询）
            try:
                cached = self.redis_client.client.get(f"token:{token0}:decimals")
                if cached:
                    result['decimals0'] = int(cached)
                else:
                    miss_calls.append((token0, "0x313ce567", 'decimals0'))
            except:
                miss_calls.append((token0, "0x313ce567", 'decimals0'))
            
            # 检查 token0 symbol 缓存
            try:
                cached = self.redis_client.client.get(f"token:{token0}:symbol")
                if cached:
                    if isinstance(cached, bytes):
                        cached = cached.decode('utf-8')
                    result['symbol0'] = cached
                else:
                    miss_calls.append((token0, "0x95d89b41", 'symbol0'))
            except:
                miss_calls.append((token0, "0x95d89b41", 'symbol0'))
            
            # 检查 token1 decimals 缓存
            try:
                cached = self.redis_client.client.get(f"token:{token1}:decimals")
                if cached:
                    result['decimals1'] = int(cached)
                else:
                    miss_calls.append((token1, "0x313ce567", 'decimals1'))
            except:
                miss_calls.append((token1, "0x313ce567", 'decimals1'))
            
            # 检查 token1 symbol 缓存
            try:
                cached = self.redis_client.client.get(f"token:{token1}:symbol")
                if cached:
                    if isinstance(cached, bytes):
                        cached = cached.decode('utf-8')
                    result['symbol1'] = cached
                else:
                    miss_calls.append((token1, "0x95d89b41", 'symbol1'))
            except:
                miss_calls.append((token1, "0x95d89b41", 'symbol1'))
            
            # 如果有未命中的，使用 Multicall2 批量查询
            if not miss_calls:
                # 全部命中，直接返回
                return result
            
            multicall_params = [(target, calldata) for target, calldata, _ in miss_calls]
            multicall_results = self.multicall2_try_aggregate(multicall_params)
            
            # 解析结果并更新缓存
            for (target, calldata, field_name), call_result in zip(miss_calls, multicall_results):
                if call_result:
                    if 'decimals' in field_name:
                        try:
                            value = int(call_result, 16) if call_result else 18
                            result[field_name] = value
                            # 写入 Redis 缓存
                            try:
                                redis_key = f"token:{target}:decimals"
                                self.redis_client.client.setex(redis_key, 86400, str(value))
                            except:
                                pass
                        except:
                            result[field_name] = 18
                    elif 'symbol' in field_name:
                        try:
                            # 使用 parse_symbol_data 处理动态/固定长度编码
                            value = self.parse_symbol_data(call_result)
                            result[field_name] = value
                            # 写入 Redis 缓存
                            try:
                                redis_key = f"token:{target}:symbol"
                                self.redis_client.client.setex(redis_key, 86400, value)
                            except:
                                pass
                        except:
                            result[field_name] = "???"
                else:
                    # 调用失败，使用默认值
                    if 'decimals' in field_name:
                        result[field_name] = 18
                    else:
                        result[field_name] = "???"
            
            # 确保所有值都有默认值
            for key in ['decimals0', 'decimals1']:
                if result[key] is None:
                    result[key] = 18
            for key in ['symbol0', 'symbol1']:
                if result[key] is None:
                    result[key] = "???"
            
            return result
        
        except Exception as e:
            logger.error(f"❌ 获取交易对信息失败: {e}")
            return None
    
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
    
    async def check_and_set_alert_cooldown(self, token_address: str) -> bool:
        """
        原子化检查冷却期并设置（使用Lua脚本）
        返回 True = 允许推送并已设置冷却期
        返回 False = 在冷却期内，跳过
        """
        redis_key = f"bsc:alert:last:{token_address.lower()}"
        
        try:
            now_timestamp = int(time.time())
            # 使用 uniform 获得更精确的抖动（float → int）
            jitter_seconds = random.uniform(0, self.cooldown_jitter * 60)
            cooldown_seconds = int(self.cooldown_minutes * 60 + jitter_seconds)
            
            # Lua脚本：原子化检查并设置冷却期
            lua_script = """
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local cooldown = tonumber(ARGV[2])
            
            -- 获取上次记录
            local last_data = redis.call('GET', key)
            
            -- 首次或无记录
            if not last_data then
                local new_data = string.format('{"timestamp":%d,"cooldown_seconds":%d,"alert_count":1}', now, cooldown)
                redis.call('SETEX', key, 86400, new_data)
                return 1  -- 允许推送
            end
            
            -- 解析JSON（简化：直接提取timestamp）
            local last_timestamp = tonumber(string.match(last_data, '"timestamp":(%d+)'))
            
            -- 无法解析，视为首次
            if not last_timestamp then
                local new_data = string.format('{"timestamp":%d,"cooldown_seconds":%d,"alert_count":1}', now, cooldown)
                redis.call('SETEX', key, 86400, new_data)
                return 1
            end
            
            -- 检查冷却期
            if now - last_timestamp < cooldown then
                return 0  -- 冷却期内，拒绝
            end
            
            -- 通过冷却期，更新记录
            local alert_count = tonumber(string.match(last_data, '"alert_count":(%d+)')) or 0
            local new_data = string.format('{"timestamp":%d,"cooldown_seconds":%d,"alert_count":%d}', now, cooldown, alert_count + 1)
            redis.call('SETEX', key, 86400, new_data)
            return 1  -- 允许推送
            """
            
            # 执行Lua脚本
            result = await asyncio.to_thread(
                self.redis_client.client.eval,
                lua_script,
                1,  # numkeys
                redis_key,
                now_timestamp,
                cooldown_seconds
            )
            
            if result == 1:
                return True  # 允许推送
            else:
                logger.info(f"⏳ 冷却期内，跳过: {token_address}")
                return False
        
        except Exception as e:
            logger.error(f"检查冷却期失败: {e}")
            # 出错时允许推送（避免误阻止）
            return True
    
    async def check_alert_cooldown_readonly(self, token_address: str) -> bool:
        """
        只读检查代币是否在冷却期内（不设置冷却期）
        用于第一层过滤后，避免浪费API调用
        """
        redis_key = f"bsc:alert:last:{token_address.lower()}"
        
        try:
            last_alert_data = await asyncio.to_thread(self.redis_client.get, redis_key)
            
            if not last_alert_data:
                return True  # 没有记录，允许继续
            
            # 安全解析 JSON
            try:
                if isinstance(last_alert_data, dict):
                    last_alert = last_alert_data
                elif isinstance(last_alert_data, (str, bytes)):
                    if isinstance(last_alert_data, bytes):
                        last_alert_data = last_alert_data.decode('utf-8')
                    if not last_alert_data or last_alert_data == 'null':
                        return True
                    last_alert = json.loads(last_alert_data)
                else:
                    return True
            except:
                return True
            
            last_timestamp = last_alert.get('timestamp', 0)
            cooldown_seconds = last_alert.get('cooldown_seconds', int(self.cooldown_minutes * 60))
            now_timestamp = int(time.time())
            
            if now_timestamp - last_timestamp < cooldown_seconds:
                logger.info(f"⏳ 冷却期内，跳过: {token_address} (剩余 {cooldown_seconds - (now_timestamp - last_timestamp)}秒)")
                return False
            
            return True
        except Exception as e:
            logger.error(f"检查冷却期失败: {e}")
            return True  # 出错时允许继续
    
    async def check_alert_cooldown(self, token_address: str) -> bool:
        """
        检查代币是否在冷却期内（兼容旧接口，只读）
        """
        return await self.check_alert_cooldown_readonly(token_address)
    
    # update_alert_history已废弃，逻辑已合并到check_and_set_alert_cooldown中
    
    def create_token_buttons(self, token_address: str):
        """创建代币的 Telegram 内联按钮"""
        if not HAS_TELEGRAM_BUTTONS:
            return None
        
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
                logger.info(f"✅ Telegram通知已发送 - {token_address[:10]}...")
            else:
                logger.error(f"❌ Telegram发送失败 - {token_address[:10]}...: {result.get('error')}")
        
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
                logger.debug("第二层过滤-无DBotX数据", extra={"token": token_address[:10]})
                return None
            
            # 3. 判断内外盘
            launchpad_status = launchpad_info.get('launchpad_status', 0)
            is_internal = (launchpad_status == 0)
            pool_type = "内盘" if is_internal else "外盘"
            pool_emoji = "🔴" if is_internal else "🟢"
            
            # 4. 根据内外盘选择时间间隔
            time_interval = self.time_interval_internal if is_internal else self.time_interval_external
            
            # 5. 解析数据（使用动态时间间隔）
            token_data = dbotx_api.parse_token_data(raw_data, time_interval)
            if not token_data:
                logger.debug(f"⏭️  [第二层] 解析失败: {token_address}...")
                return None
            
            # 6. Top持有者过滤（内盘和外盘都检查）
            # 先判断 Redis 配置是否有 topHoldersThreshold
            top_holders_threshold = self.top_holders_threshold_internal if is_internal else self.top_holders_threshold_external
            top10_holder_check_passed = None  # 用于日志显示
            if top_holders_threshold is not None:
                # 再判断 API 返回数据是否有 top10_holder_rate
                top10_holder_rate = token_data.get('top10_holder_rate')
                if top10_holder_rate is not None:
                    # API 返回的是小数（0-1），需要转成百分比（0-100）再比较
                    top10_holder_percent = top10_holder_rate * 100
                    top10_holder_check_passed = top10_holder_percent < top_holders_threshold
                    # 两个都有，才进行校验
                    if top10_holder_percent >= top_holders_threshold:
                        symbol = token_data.get('symbol', 'Unknown')
                        logger.info(f"⏭️  [第二层] Top10持有者比例过高: {symbol} ({top10_holder_percent:.1f}% >= {top_holders_threshold:.1f}%)")
                        return None
                else:
                    top10_holder_check_passed = "N/A"  # API没返回数据，跳过检查
            else:
                top10_holder_check_passed = "未配置"  # Redis未配置，跳过检查
            
            # 7. 获取指标数据
            price_change = token_data.get('price_change', 0)
            volume = token_data.get('volume', 0)
            symbol = token_data.get('symbol', 'Unknown')
            
            # 8. 构造 stats 数据（用于 TriggerLogic）
            stats = {
                'priceChange': price_change,
                'volume': volume,
                'holderChange': 0
            }
            
            # 9. 选择对应的 events_config 和 trigger_logic
            events_config = self.internal_events_config if is_internal else self.external_events_config
            trigger_logic = self.trigger_logic_internal if is_internal else self.trigger_logic_external
            
            logger.info(f"🔎 [第二层指标检查] {pool_emoji}{pool_type} {symbol} ({token_address})")
            logger.info(f"   ├─ {time_interval}涨幅: {price_change:+.2f}%")
            logger.info(f"   ├─ {time_interval}交易量: ${volume:,.2f}")
            
            # 显示 Top10 持有者检查状态
            if top10_holder_check_passed == "未配置":
                logger.info(f"   ├─ Top10持有者: 未配置阈值（跳过此项）")
            elif top10_holder_check_passed == "N/A":
                logger.info(f"   ├─ Top10持有者: API未返回（跳过此项）")
            elif top10_holder_check_passed is True:
                top10_holder_rate = token_data.get('top10_holder_rate', 0)
                logger.info(f"   ├─ Top10持有者: {top10_holder_rate * 100:.1f}% (✅ < {top_holders_threshold:.1f}%)")
            elif top10_holder_check_passed is False:
                # 这个分支不会执行，因为如果未通过已经return了
                pass
            
            # 显示触发逻辑
            logic_text = "AND" if trigger_logic == "all" else "OR"
            logger.info(f"   └─ 配置阈值: 涨幅>={events_config.get('priceChange', {}).get('risePercent')}% {logic_text} 交易量>=${events_config.get('volume', {}).get('threshold')}")
            logger.debug(f"   配置详情: {events_config}")
            logger.debug(f"   统计数据: {stats}")
            
            # 10. 使用 TriggerLogic 评估（使用配置的触发逻辑）
            should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
                stats, events_config, trigger_logic
            )
            
            logger.debug(f"   触发结果: should_trigger={should_trigger}, triggered_events={len(triggered_events) if triggered_events else 0}")
            
            if not should_trigger:
                logger.info(f"   ❌ 未达到触发条件")
                return None
            
            # 11. 通过筛选，返回数据
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
        """处理 PancakeSwap Swap 事件（外盘）- 优化版（使用三层缓存 + receipt兜底）"""
        tx_hash = log.get("transactionHash")
        pair_address = log.get("address", "").lower()
        swap_data = self.parse_swap_data(log.get("data"))
        
        if not swap_data:
            # WebSocket数据解析失败，尝试从receipt兜底
            await self._handle_swap_with_receipt_fallback(tx_hash, pair_address)
            return
        
        # 使用优化的批量查询（支持 L1/L2/L3 缓存）
        pair_info = self.get_pair_full_info(pair_address)
        if not pair_info:
            return
        
        token0 = pair_info['token0']
        token1 = pair_info['token1']
        
        # 快速过滤：只处理 USDT/USDC/WBNB 相关的交易对
        if token0 not in (self.USDT, self.USDC, self.WBNB) and token1 not in (self.USDT, self.USDC, self.WBNB):
            return
        
        # 排除稳定币对（如 USDT/WBNB）
        if {token0, token1} & {self.USDT, self.USDC, self.WBNB} == {token0, token1}:
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
        quote_decimals = 18
        base_decimals = 18
        quote_symbol = "???"
        base_symbol = "???"
        
        if amount0_in > 0 and amount1_out > 0:
            if token0 in (self.USDT, self.USDC, self.WBNB):
                quote_token = token0
                base_token = token1
                quote_amount = amount0_in
                base_amount = amount1_out
                quote_decimals = pair_info['decimals0']
                base_decimals = pair_info['decimals1']
                quote_symbol = pair_info['symbol0']
                base_symbol = pair_info['symbol1']
        elif amount1_in > 0 and amount0_out > 0:
            if token1 in (self.USDT, self.USDC, self.WBNB):
                quote_token = token1
                base_token = token0
                quote_amount = amount1_in
                base_amount = amount0_out
                quote_decimals = pair_info['decimals1']
                base_decimals = pair_info['decimals0']
                quote_symbol = pair_info['symbol1']
                base_symbol = pair_info['symbol0']
        
        if not quote_token or not base_token:
            return
        
        quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
        if quote_token == self.WBNB:
            wbnb_price = self.get_wbnb_price()
            usd_value = float(quote_value) * wbnb_price
        else:
            usd_value = float(quote_value)
        
        # 第一层过滤
        if not self.first_layer_filter(usd_value, is_internal=False):
            return
        
        # 东八区时间
        cn_time = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
        logger.info(f"✅ [外盘] 通过第一层: {base_symbol} (${usd_value:.2f}) [{cn_time}] - {base_token[:10]}...")
        
        # 🚀 优化：先检查 Redis 缓存（非fourmeme token黑名单）
        if self.redis_client:
            try:
                is_cached_non_fourmeme = self.redis_client.sismember(self.NON_FOURMEME_KEY, base_token)
                if is_cached_non_fourmeme:
                    self.cache_hit_count += 1
                    logger.debug(f"⏭️  外盘非fourmeme (缓存命中 #{self.cache_hit_count}): {base_symbol} - {base_token[:10]}...")
                    return
            except Exception as e:
                logger.warning(f"⚠️  Redis缓存查询失败: {e}")
        
        # fourmeme 验证（未命中缓存才调用API）
        launchpad_info = await self.check_external_is_fourmeme(base_token)
        if not launchpad_info:
            # 不是fourmeme → 加入Redis缓存（30天过期）
            if self.redis_client:
                try:
                    self.redis_client.client.sadd(self.NON_FOURMEME_KEY, base_token)
                    # 每次添加时重置过期时间，保持30天滚动窗口
                    self.redis_client.client.expire(self.NON_FOURMEME_KEY, self.NON_FOURMEME_TTL)
                except Exception as e:
                    logger.warning(f"⚠️  Redis缓存写入失败: {e}")
            logger.debug(f"⏭️  外盘非fourmeme: {base_symbol} - {base_token[:10]}...")
            return
        
        logger.info(f"✓ 外盘是fourmeme: {base_symbol} - {base_token[:10]}...")
        
        # 第二层过滤
        token_data = await self.second_layer_filter(base_token, pair_address, launchpad_info, is_internal=False)
        if not token_data:
            return
        
        # 🔒 关键：第二层通过后立即设置冷却期（防止并发重复播报）
        # 在播报前设置，避免同步 I/O 阻塞期间其他交易也通过
        already_alerted = not await self.check_and_set_alert_cooldown(base_token)
        if already_alerted:
            logger.info(f"⏳ 已在播报流程中，跳过: {base_token}")
            return
        
        # 构建消息
        quote_formatted = self.format_amount(quote_amount, quote_decimals)
        base_formatted = self.format_amount(base_amount, base_decimals)
        
        pool_emoji = token_data['pool_emoji']
        pool_type = token_data['pool_type']
        is_internal = token_data.get('is_internal', False)
        symbol = token_data.get('symbol', base_symbol)
        price_change = token_data.get('price_change', 0)
        volume = token_data.get('volume', 0)
        market_cap = token_data.get('market_cap', 0)  # parse_token_data 已解析为 market_cap（下划线）
        buy_tax = token_data.get('buy_tax', 0)
        sell_tax = token_data.get('sell_tax', 0)
        price = token_data.get('price', 0)
        
        # 获取时间间隔（用于日志显示）
        time_interval = self.time_interval_internal if is_internal else self.time_interval_external
        
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
                    alert_reasons.append(f"📈 {time_interval}涨幅 {price_change:+.2f}%")
                elif event.get('event') == 'volume':
                    alert_reasons.append(f"💹 {time_interval}交易量 ${volume_str}")
        
        if not alert_reasons:
            alert_reasons.append(f"💰 大额交易 ${usd_value:.2f}")
        
        message = f"""<b>🟢 BSC 信号</b>

💰 代币: {symbol}
📝 名称: {symbol}
🔗 合约: <code>{base_token}</code>
🔗 交易哈希: <code>{tx_hash}</code>

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
        
        # 结构化日志输出（外盘）
        logger.info("外盘交易触发", extra={
            "pool_type": pool_type,
            "symbol": symbol,
            "token": base_token[:10],
            "tx_hash": tx_hash[:10],
            "quote_amount": f"{quote_formatted} {quote_symbol}",
            "usd_value": f"${usd_value:.2f}",
            "base_amount": f"{base_formatted} {symbol}",
            "price_change": f"{price_change:+.2f}%",
            "volume": f"${volume:,.0f}",
            "market_cap": f"${market_cap:,.0f}",
            "buy_tax": f"{buy_tax:.1f}%",
            "sell_tax": f"{sell_tax:.1f}%"
        })
        
        # 发送推送
        await self.send_alert(message, base_token)
        
        # 记录到数据库并推送WebSocket
        await asyncio.to_thread(
            self.alert_recorder.write_bsc_alert,
            ca=base_token,
            token_name=symbol,
            token_symbol=symbol,
            single_max=usd_value,
            total_sum=usd_value,
            alert_reasons=alert_reasons,
            block_number=0,  # WebSocket不关心区块号
            price_usdt=price,
            pair_address=pair_address,
            market_cap=market_cap,
            price_change=price_change,
            volume_24h=volume,
            holders=0,
            logo="",
            notify_error=None
        )
        # 冷却期已在播报前设置，此处无需重复
    
    async def _handle_swap_with_receipt_fallback(self, tx_hash: str, pair_address: str):
        """外盘receipt兜底：从交易回执中提取Swap事件"""
        try:
            # 获取交易回执（使用缓存）
            receipt, _ = self.get_receipt_cached(tx_hash)
            if not receipt:
                logger.debug(f"⚠️ 获取receipt失败: {tx_hash}")
                return
            
            logs = receipt.get("logs", [])
            swap_topic = self.TOPIC_V2_SWAP
            
            # 查找Swap事件
            for log in logs:
                topics = log.get("topics", [])
                log_addr = log.get("address", "").lower()
                
                # 匹配Swap事件
                if topics and topics[0].lower() == swap_topic and log_addr == pair_address:
                    logger.info(f"✅ Receipt兜底成功: {tx_hash} (外盘)")
                    # 递归调用原函数处理
                    await self.handle_swap_event(log)
                    return
            
            logger.debug(f"⚠️ Receipt中未找到Swap事件: {tx_hash}")
        except Exception as e:
            logger.debug(f"❌ Receipt兜底失败: {e}")
    
    async def handle_proxy_event(self, log: Dict):
        """处理 Fourmeme Proxy 事件（内盘）"""
        tx_hash = log.get("transactionHash")
        addr = log.get("address", "").lower()
        
        proxy_type = "主Proxy" if addr == self.FOURMEME_PROXY[0] else "Try Buy"
        
        try:
            dbotx_api = self.get_thread_dbotx_api()
            
            # 获取交易回执（使用缓存）
            receipt, tx_info = self.get_receipt_cached(tx_hash)
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
            
            # 获取交易信息（BNB 买入，已从缓存获取）
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
            
            # 🔍 调试日志：内盘 tx 详情
            logger.info(f"🔍 内盘tx: hash={tx_hash}, proxy={proxy_type}, input_BNB={quote_amount / 10**quote_decimals:.4f}, usd={usd_value:.2f}, token={target_token}")
            
            # 第一层过滤
            if not self.first_layer_filter(usd_value, is_internal=True):
                logger.debug(f"⏭️  内盘金额不足: {target_symbol} (${usd_value:.2f}) - {target_token[:10]}...")
                return
            
            # 东八区时间
            cn_time = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
            logger.info(f"✅ [内盘] 通过第一层: {target_symbol} (${usd_value:.2f}) [{cn_time}]")
            
            # 获取 launchpad 信息
            launchpad_info = await dbotx_api.get_token_launchpad_info('bsc', target_token)
            if not launchpad_info:
                logger.warning(f"⚠️ API miss: hash={tx_hash}, token={target_token} - 使用 fallback")
                # Fallback：构造基础 launchpad_info
                launchpad_info = {
                    'launchpad': 'fourmeme',
                    'pair_address': None  # 稍后尝试从 receipt 提取
                }
            
            pair_address = launchpad_info.get('pair_address')
            if not pair_address:
                # 尝试从 receipt 的 logs 中提取 PancakeV2 Pair 地址
                pair_address = self._extract_pair_from_receipt(logs)
                if pair_address:
                    logger.info(f"✅ 从 receipt 提取到 pair: {pair_address}")
                    launchpad_info['pair_address'] = pair_address
                else:
                    logger.debug("内盘无交易对地址", extra={
                        "token": target_token[:10],
                        "symbol": target_symbol
                    })
                    return
            
            # 第二层过滤
            token_data = await self.second_layer_filter(target_token, pair_address, launchpad_info, is_internal=True)
            if not token_data:
                return
            
            # 更新symbol缓存（如果第一层获取失败，这里用DBotX的正确symbol更新）
            if target_symbol == "???" and token_data.get('symbol'):
                correct_symbol = token_data.get('symbol')
                try:
                    redis_key = f"token:{target_token}:symbol"
                    self.redis_client.client.setex(redis_key, 86400, correct_symbol)
                    logger.debug(f"✅ 更新symbol缓存: {target_token} → {correct_symbol}")
                except:
                    pass
            
            # 🔒 关键：第二层通过后立即设置冷却期（防止并发重复播报）
            # 在播报前设置，避免同步 I/O 阻塞期间其他交易也通过
            already_alerted = not await self.check_and_set_alert_cooldown(target_token)
            if already_alerted:
                logger.info(f"⏳ 已在播报流程中，跳过: {target_token}")
                return
            
            # 构建消息
            quote_formatted = self.format_amount(quote_amount, quote_decimals)
            target_formatted = self.format_amount(target_amount, target_decimals)
            
            pool_emoji = token_data['pool_emoji']
            pool_type = token_data['pool_type']
            is_internal = token_data.get('is_internal', True)  # Proxy事件默认是内盘
            symbol = token_data.get('symbol', target_symbol)
            price_change = token_data.get('price_change', 0)
            volume = token_data.get('volume', 0)
            market_cap = token_data.get('market_cap', 0)  # parse_token_data 已解析为 market_cap（下划线）
            buy_tax = token_data.get('buy_tax', 0)
            sell_tax = token_data.get('sell_tax', 0)
            price = token_data.get('price', 0)
            
            # 获取时间间隔（用于日志显示）
            time_interval = self.time_interval_internal if is_internal else self.time_interval_external
            
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
                        alert_reasons.append(f"📈 {time_interval}涨幅 {price_change:+.2f}%")
                    elif event.get('event') == 'volume':
                        alert_reasons.append(f"💹 {time_interval}交易量 ${volume_str}")
            
            if not alert_reasons:
                alert_reasons.append(f"💰 大额交易 ${usd_value:.2f}")
            
            message = f"""<b>{pool_emoji} BSC 信号</b>

💰 代币: {symbol}
📝 名称: {symbol}
🔗 合约: <code>{target_token}</code>
🔗 交易哈希: <code>{tx_hash}</code>

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
            
            # 结构化日志输出（内盘）
            logger.info("内盘交易触发", extra={
                "pool_type": pool_type,
                "proxy_type": proxy_type,
                "symbol": symbol,
                "token": target_token[:10],
                "tx_hash": tx_hash[:10],
                "quote_amount": f"{quote_formatted} {quote_symbol}",
                "usd_value": f"${usd_value:.2f}",
                "target_amount": f"{target_formatted} {symbol}",
                "price_change": f"{price_change:+.2f}%",
                "volume": f"${volume:,.0f}",
                "market_cap": f"${market_cap:,.0f}",
                "buy_tax": f"{buy_tax:.1f}%",
                "sell_tax": f"{sell_tax:.1f}%"
            })
            
            # 发送推送
            await self.send_alert(message, target_token)
            
            # 记录到数据库并推送WebSocket
            await asyncio.to_thread(
                self.alert_recorder.write_bsc_alert,
                ca=target_token,
                token_name=symbol,
                token_symbol=symbol,
                single_max=usd_value,
                total_sum=usd_value,
                alert_reasons=alert_reasons,
                block_number=0,  # WebSocket不关心区块号
                price_usdt=price,
                pair_address=pair_address,
                market_cap=market_cap,
                price_change=price_change,
                volume_24h=volume,
                holders=0,
                logo="",
                notify_error=None
            )
            # 冷却期已在播报前设置，此处无需重复
        
        except Exception as e:
            logger.error(f"❌ 处理内盘交易出错: {e}")
    
    def health_check_loop(self):
        """健康检查循环（每分钟输出一次状态）"""
        while not self.should_stop:
            try:
                time.sleep(60)  # 每60秒检查一次
                
                if self.should_stop:
                    break
                
                now = time.time()
                idle_seconds = int(now - self.last_message_time)
                
                # 去重缓存定期清理（超过 80% 容量时清理最老的 20%）
                seen_txs_size = len(self.seen_txs)
                if seen_txs_size > self.max_seen_txs * 0.8:
                    cleanup_count = int(self.max_seen_txs * 0.2)
                    for _ in range(cleanup_count):
                        if self.seen_txs:
                            self.seen_txs.popitem(last=False)  # 弹出最老的
                    logger.info(f"🧹 去重缓存清理: 移除 {cleanup_count} 条旧记录 ({seen_txs_size} → {len(self.seen_txs)})")
                
                logger.info("=" * 80)
                logger.info("💓 WebSocket 健康检查")
                logger.info(f"   状态: {'🟢 运行中' if self.ws and not self.should_stop else '🔴 已停止'}")
                logger.info(f"   重连次数: {self.reconnect_count}")
                logger.info(f"   回补次数: {self.backfill_count} (冷却期: {self.backfill_cooldown}s)")
                logger.info(f"   消息总数: {self.message_count}")
                logger.info(f"   去重缓存: {len(self.seen_txs)} / {self.max_seen_txs} ({len(self.seen_txs) / self.max_seen_txs * 100:.1f}%)")
                logger.info(f"   回执缓存: {len(self.receipt_cache)} 条 (命中 {self.receipt_cache_hits} 次, 节省RPC)")
                logger.info(f"   非fourmeme缓存: {self.cache_hit_count} 次（节省API调用）")
                logger.info(f"   上次消息: {idle_seconds}秒前")
                logger.info(f"   空闲警告: {'⚠️ 超过5分钟无消息！' if idle_seconds > 300 else '✅ 正常'}")
                logger.info("=" * 80)
                
                # 如果超过10分钟没有消息，主动重连
                if idle_seconds > 600 and self.ws:
                    logger.warning("⚠️ 检测到10分钟无消息，主动触发重连...")
                    try:
                        self.ws.close()
                    except:
                        pass
                    
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
    
    def on_message(self, ws, message):
        """WebSocket 消息回调"""
        try:
            # 更新最后消息时间和计数
            self.last_message_time = time.time()
            self.message_count += 1
            
            msg = json.loads(message)
            
            # 跳过订阅确认（包含id但不包含method的消息）
            if "id" in msg and "method" not in msg:
                # 这是订阅确认消息，记录subscription ID
                sub_id = msg.get("result")
                if sub_id:
                    logger.debug(f"✓ 订阅成功，subscription ID: {sub_id}")
                return
            
            # 获取实时事件（method=eth_subscription）
            if msg.get("method") != "eth_subscription":
                logger.warning(f"⚠️ 收到未知消息类型: {msg.get('method', 'unknown')}")
                return
            
            params = msg.get("params", {})
            result = params.get("result", {})
            
            if not isinstance(result, dict):
                return
            
            # 去重（使用 tx_hash:logIndex 组合键，支持同一交易的多个日志）
            tx_hash = result.get("transactionHash")
            if not tx_hash:
                # transactionHash 可能为 None（订阅确认、部分节点 bug）
                return
            
            # logIndex 是十六进制字符串，转为整数避免格式差异（0x1 vs 0x01）
            log_index_hex = result.get("logIndex", "0x0")
            try:
                log_index = int(log_index_hex, 16) if isinstance(log_index_hex, str) else int(log_index_hex or 0)
            except (ValueError, TypeError):
                log_index = 0
            
            # 组合键：tx_hash:logIndex
            key = f"{tx_hash}:{log_index}"
            if key in self.seen_txs:
                logger.debug(f"⏭️  去重跳过: {tx_hash[:10]}...#{log_index}")
                return
            
            self.seen_txs[key] = True
            logger.debug(f"✅ 处理日志: {tx_hash[:10]}...#{log_index} (缓存大小: {len(self.seen_txs)})")
            
            # LRU淘汰最老的日志（FIFO）
            if len(self.seen_txs) > self.max_seen_txs:
                self.seen_txs.popitem(last=False)  # 弹出最早的
            
            # 更新最后处理的区块号（用于断线回补）
            block_number = result.get("blockNumber")
            if block_number:
                try:
                    block_num = int(block_number, 16) if isinstance(block_number, str) else block_number
                    if block_num > self.last_processed_block:
                        self.last_processed_block = block_num
                except:
                    pass
            
            # 判断事件类型
            topics = result.get("topics", [])
            addr = result.get("address", "").lower()
            
            # 防御性检查：topics必须存在且不为空
            if not topics or len(topics) == 0:
                return
            
            # 统一小写（BSC节点返回是0x大写）
            topic0 = topics[0].lower() if topics[0] else ""
            if not topic0:
                return
            
            # Fourmeme 自定义事件（内盘，含内部调用）
            if topic0 in self.FOURMEME_CUSTOM_EVENTS:
                self.executor.submit(self._run_async_in_thread, self.handle_proxy_event, result)
            
            # Swap 事件（外盘）
            elif topic0 == self.TOPIC_V2_SWAP:
                self.executor.submit(self._run_async_in_thread, self.handle_swap_event, result)
            
            # Proxy 事件（内盘，直接调用）
            elif addr in self.FOURMEME_PROXY:
                self.executor.submit(self._run_async_in_thread, self.handle_proxy_event, result)
            
            # Transfer 兜底（防止fourmeme升级/换topic）
            elif topic0 == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
                # Transfer事件格式: Transfer(address indexed from, address indexed to, uint256 value)
                # topics[1] = from (padded), topics[2] = to (padded)
                if len(topics) >= 3:
                    # 提取from和to地址（去掉padding的0）
                    from_addr = ("0x" + topics[1][-40:]).lower() if len(topics[1]) >= 40 else ""
                    to_addr = ("0x" + topics[2][-40:]).lower() if len(topics[2]) >= 40 else ""
                    
                    # 检查from或to是否是Proxy地址
                    if from_addr in self.FOURMEME_PROXY or to_addr in self.FOURMEME_PROXY:
                        self.executor.submit(self._run_async_in_thread, self.handle_proxy_event, result)
        
        except Exception as e:
            logger.error(f"❌ 处理消息出错: {e}")
    
    def _run_async_in_thread(self, async_func, *args, **kwargs):
        """在线程池中运行异步函数（使用 asyncio.run 简化事件循环管理）"""
        asyncio.run(async_func(*args, **kwargs))
    
    def on_open(self, ws):
        """WebSocket 连接成功回调"""
        is_reconnect = self.reconnect_count > 0
        
        if not is_reconnect:
            logger.info("✅ WebSocket 连接成功！")
            logger.info(f"节点: {self.ws_url[:50]}")
        else:
            logger.info(f"✅ WebSocket 重连成功！(第{self.reconnect_count}次)")
            # 重连后立即回补遗漏的交易
            self.executor.submit(self._backfill_missed_logs, f"重连#{self.reconnect_count}")
        
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
        
        # 订阅 Fourmeme 自定义事件（捕获内部调用）
        custom_event_id = swap_id + 1
        ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": custom_event_id,
            "method": "eth_subscribe",
            "params": ["logs", {
                "address": self.FOURMEME_PROXY,
                "topics": [self.FOURMEME_CUSTOM_EVENTS]
            }]
        }))
        logger.info(f"✓ 订阅 Fourmeme 自定义事件（含内部调用）")
        
        logger.info("✅ 已订阅事件监听")
        logger.info(f"📱 Telegram 频道: {self.bsc_channel_id}")
        logger.info(f"⏳ 等待链上交易...")
    
    def on_error(self, ws, error):
        """WebSocket 错误回调"""
        logger.error(f"❌ WebSocket 错误: {error}")
        logger.error(f"错误堆栈: {traceback.format_exc()}")
    
    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 关闭回调"""
        if self.should_stop:
            logger.info(f"✅ WebSocket 连接已关闭")
        else:
            logger.warning(f"⚠️  WebSocket 连接断开: {close_status_code} - {close_msg}")
            logger.info("🔄 将在5秒后自动重连...")
    
    def _backfill_missed_logs(self, reason="重连"):
        """
        断线回补：使用eth_getLogs回补遗漏的交易（优化版）
        
        优化：
        - 60秒冷却期，防止频繁触发
        - 离线时间阈值（>30秒才回补）
        - 缩小区块跨度（200块）
        - 记录触发原因和统计
        """
        try:
            now = time.time()
            
            # 1. 冷却期检查（60秒内不重复回补）
            if now - self.last_backfill_time < self.backfill_cooldown:
                elapsed = int(now - self.last_backfill_time)
                logger.info(f"⏭️  回补冷却中 ({elapsed}s/{self.backfill_cooldown}s)，跳过本次回补（原因：{reason}）")
                return
            
            # 记录回补时间和原因
            self.last_backfill_time = now
            self.reconnect_time = now
            
            # 2. 获取当前区块
            latest_block_hex = self.rpc_call("eth_blockNumber", [])
            if not latest_block_hex:
                logger.warning("❌ 获取最新区块失败，跳过回补")
                return
            
            latest_block = int(latest_block_hex, 16)
            
            # 3. 计算回补区块范围
            if self.last_processed_block == 0:
                # 首次连接，只回补最近50个区块（约15秒）
                from_block = max(latest_block - 50, 0)
                offline_seconds = "首次连接"
            else:
                # 计算离线时间（按3秒/块估算）
                missed_blocks = latest_block - self.last_processed_block
                offline_seconds = missed_blocks * 3  # BSC 约3秒/块
                
                # 离线时间阈值：只在离线 > 30秒 才回补
                if offline_seconds < 30:
                    logger.info(f"⏭️  离线时间过短 ({offline_seconds:.0f}s < 30s)，跳过回补（原因：{reason}）")
                    self.last_processed_block = latest_block
                    return
                
                # 限制回补区块跨度（最多200块，约10分钟）
                max_backfill_blocks = 200
                from_block = max(self.last_processed_block, latest_block - max_backfill_blocks)
            
            block_span = latest_block - from_block
            self.backfill_count += 1
            
            logger.info(f"🔄 [回补 #{self.backfill_count}] 开始: #{from_block} → #{latest_block} ({block_span}块, 离线≈{offline_seconds}s, 原因:{reason})")
            
            # 4. 分批查询（缩小batch，降低单次请求压力）
            batch_size = 200  # 从1000改为200
            total_logs = 0
            
            for start in range(from_block, latest_block + 1, batch_size):
                end = min(start + batch_size - 1, latest_block)
                
                # 查询Proxy相关的日志
                logs = self.rpc_call("eth_getLogs", [{
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                    "address": self.FOURMEME_PROXY
                }])
                
                if logs and isinstance(logs, list):
                    total_logs += len(logs)
                    # 处理每条日志
                    for log in logs:
                        try:
                            # 异步处理日志（在线程池中）
                            self.executor.submit(self._run_async_in_thread, self._process_backfill_log, log)
                        except Exception as e:
                            logger.debug(f"处理回补日志失败: {e}")
            
            logger.info(f"✅ [回补 #{self.backfill_count}] 完成: 共处理 {total_logs} 条日志")
            self.last_processed_block = latest_block
            
        except Exception as e:
            logger.error(f"❌ [回补 #{self.backfill_count}] 失败: {e}")
    
    async def _process_backfill_log(self, log):
        """处理回补的日志"""
        try:
            # 判断是内盘还是外盘
            topics = log.get("topics", [])
            if not topics:
                return
            
            topic0 = topics[0].lower() if topics[0] else ""
            addr = log.get("address", "").lower()
            
            # 内盘事件
            if topic0 in self.FOURMEME_CUSTOM_EVENTS or addr in self.FOURMEME_PROXY:
                await self.handle_proxy_event(log)
        except Exception as e:
            logger.debug(f"处理回补日志异常: {e}")
    
    def signal_handler(self, signum, frame):
        """信号处理器（Ctrl+C）"""
        logger.info("\n⚠️  收到停止信号，正在关闭...")
        self.should_stop = True
        
        if self.ws:
            self.ws.close()
        
        # 关闭 HTTP Session
        if hasattr(self, 'session'):
            try:
                self.session.close()
                logger.info("✅ HTTP Session 已关闭")
            except Exception as e:
                logger.debug(f"关闭 Session 异常: {e}")
        
        self.executor.shutdown(wait=False)
        

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
        
        # 在单独线程中运行 WebSocket（添加心跳和自动重连）
        def run_ws_with_retry():
            """带重连机制的 WebSocket 运行循环"""
            retry_count = 0
            while not self.should_stop:
                try:
                    logger.info(f"🔌 WebSocket 连接尝试... (第{retry_count + 1}次)")
                    
                    # 每次重连都创建新的 WebSocket 对象
                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_message=self.on_message,
                        on_open=self.on_open,
                        on_error=self.on_error,
                        on_close=self.on_close
                    )
                    
                    self.ws.run_forever(
                        ping_interval=20,    # 每20秒发送ping
                        ping_timeout=10,     # ping超时10秒
                        skip_utf8_validation=True
                    )
                    
                    # 如果正常退出（用户停止），跳出循环
                    if self.should_stop:
                        break
                    
                    # 异常退出，等待后重连
                    retry_count += 1
                    wait_seconds = min(5 * retry_count, 60)  # 最多等60秒
                    logger.warning(f"⏳ WebSocket 断开，{wait_seconds}秒后重连...")
                    time.sleep(wait_seconds)
                    
                except Exception as e:
                    logger.error(f"❌ WebSocket 运行异常: {e}")
                    logger.error(f"异常堆栈: {traceback.format_exc()}")
                    
                    if not self.should_stop:
                        retry_count += 1
                        wait_seconds = min(5 * retry_count, 60)
                        logger.warning(f"⏳ {wait_seconds}秒后重试...")
                        time.sleep(wait_seconds)

        ws_thread = threading.Thread(target=run_ws_with_retry, daemon=True)
        ws_thread.start()
        
        # 启动健康检查线程
        health_thread = threading.Thread(target=self.health_check_loop, daemon=True)
        health_thread.start()
        logger.info("💓 健康检查已启动（每60秒一次）")
        
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
            
            # 关闭 HTTP Session
            if hasattr(self, 'session'):
                try:
                    self.session.close()
                    logger.info("✅ HTTP Session 已关闭")
                except Exception as e:
                    logger.debug(f"关闭 Session 异常: {e}")
            
            self.executor.shutdown(wait=False)

