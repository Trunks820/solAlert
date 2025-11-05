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
from ..notifiers.telegram import TelegramNotifier
from ..core.redis_client import get_redis
from ..core.config import TELEGRAM_CONFIG
from ..core.formatters import format_number
from .trigger_logic import TriggerLogic
from ..notifiers.alert_recorder import get_alert_recorder
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Prometheus Metrics
try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server, REGISTRY
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    Counter = Gauge = Histogram = None


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

# 使用统一的层级logger命名
logger = logging.getLogger('solalert.monitor.bsc_ws')


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
        
        # 启动时间
        self.start_time = time.time()
        
        # Redis
        self.redis_client = get_redis()
        
        # Alert Recorder（用于记录到数据库和推送WebSocket）
        self.alert_recorder = get_alert_recorder()
        
        # 常量
        self.USDT = "0x55d398326f99059ff775485246999027b3197955"
        self.WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        self.USDC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
        # 只监听主Proxy（Try Buy已废弃，2025年无活动）
        self.FOURMEME_PROXY = [
            "0x5c952063c7fc8610ffdb798152d69f0b9550762b".lower()  # 主Proxy
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
        self.telegram_notifier = TelegramNotifier(enabled=self.enable_telegram)
        
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
        
        # 第一层/第二层统计（内外盘分别计数）
        self.first_layer_pass_internal = 0  # 内盘通过第一层
        self.first_layer_pass_external = 0  # 外盘通过第一层
        self.second_layer_check_internal = 0  # 内盘第二层检查次数
        self.second_layer_check_external = 0  # 外盘第二层检查次数
        self.second_layer_pass_internal = 0  # 内盘通过第二层
        self.second_layer_pass_external = 0  # 外盘通过第二层
        
        # 告警发送统计
        self.alert_success_count = 0  # 告警发送成功次数
        self.alert_fail_count = 0  # 告警发送失败次数
        
        # ========== 直接处理架构（无队列）==========
        # 处理流程：WebSocket → 线程池 → 异步处理（低延迟，高吞吐）
        
        # 线程池（直接处理模式：WebSocket回调 → 线程池 → 异步处理）
        # 8核24G服务器：扩大线程池以支持高并发直接处理
        self.executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="BSC-WS-Direct")
        self.thread_local = threading.local()
        
        # 交易去重（使用 tx_hash:logIndex 组合键，支持多日志处理）
        self.seen_txs = OrderedDict()
        self.max_seen_txs = 100000  # 增大容量以适应 (tx_hash, logIndex) 组合键
        
        # WBNB 价格缓存
        self.wbnb_price = 600.0
        self.wbnb_price_timestamp = 0
        self.price_cache_ttl = 300  # 5分钟
        

        
        self.session = requests.Session()
        # 配置连接池和重试策略（降低并发）
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(
            pool_connections=5,   # 连接池大小（与线程池max_workers一致）
            pool_maxsize=10,      # 最大连接数（降低并发压力）
            max_retries=retry_strategy
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # RPC 调用计数和统计
        self.rpc_id = 0
        self.rpc_stats = {}  # {method: count} 统计各方法调用次数
        self.rpc_stats_start_time = time.time()  # 统计开始时间
        
        # 速率限制（防止429限流）
        self.rate_limit_lock = threading.Lock()
        self.last_rpc_time = 0
        self.min_rpc_interval = 0.001  # 1ms 象征性间隔，Chainstack无限制
        self.rate_limit_429_count = 0  # 429错误计数
        self.rate_limit_backoff_until = 0  # 退避截止时间（秒）
        self.rate_limit_consecutive_429 = 0  # 连续429次数
        
        # 断线回补
        self.last_processed_block = 0
        self.reconnect_time = 0
        self.last_backfill_time = 0  # 上次回补时间
        self.backfill_cooldown = 300  # 回补冷却期（5分钟，大幅减少回补频率）
        self.backfill_count = 0  # 回补次数统计
        
        # 回执缓存（减少 eth_getTransactionReceipt 重复调用，带并发保护）
        self.receipt_cache = {}  # {tx_hash: {"receipt": {}, "tx_info": {}, "cached_at": timestamp, "status": "ready|loading|failed", "event": threading.Event()}}
        # OPTIMIZED: TTL延长到1小时，提高缓存命中率（交易回执永不变）
        self.receipt_cache_ttl = 3600  # 1小时过期（从30分钟提升）
        self.receipt_cache_failed_ttl = 300  # 失败结果缓存5分钟（从2分钟提升，避免NodeReal慢节点重试）
        self.receipt_cache_hits = 0  # 命中计数
        self.receipt_cache_misses = 0  # 未命中计数
        self.receipt_cache_concurrent_waits = 0  # 并发等待计数
        self.receipt_cache_failed_hits = 0  # 失败缓存命中（避免重试）
        self.receipt_cache_wait_time_total = 0.0  # 累计等待耗时（秒）
        self.receipt_cache_wait_timeouts = 0  # 等待超时次数
        self.receipt_cache_lock = threading.Lock()  # 全局锁（仅用于读写缓存字典）
        
        # eth_call 缓存（减少代币信息重复查询）
        self.eth_call_cache = {}  # {(to, data): (result, cached_at)}
        self.eth_call_cache_ttl = 300  # 5分钟过期（decimals/symbol不会变）
        self.eth_call_cache_hits = 0  # 命中计数
        self.eth_call_cache_lock = threading.Lock()  # 线程安全锁
        
        # ========== Prometheus Metrics ==========
        if HAS_PROMETHEUS:
            try:
                # Counter（计数器）- 只增不减
                self.metrics_messages = Counter(
                    'bsc_messages_total', 
                    'WebSocket接收的总消息数'
                )
                self.metrics_first_layer_pass = Counter(
                    'bsc_first_layer_pass_total', 
                    '第一层过滤通过次数',
                    ['type']  # type: internal/external
                )
                self.metrics_second_layer_check = Counter(
                    'bsc_second_layer_check_total', 
                    '第二层检查次数',
                    ['type']  # type: internal/external
                )
                self.metrics_second_layer_pass = Counter(
                    'bsc_second_layer_pass_total', 
                    '第二层检查通过次数',
                    ['type']  # type: internal/external
                )
                self.metrics_alerts = Counter(
                    'bsc_alerts_total', 
                    '告警发送次数',
                    ['status']  # status: success/failure
                )
                self.metrics_cache_hits = Counter(
                    'bsc_cache_hits_total', 
                    '缓存命中次数',
                    ['cache_type']  # cache_type: receipt/eth_call/non_fourmeme
                )
                self.metrics_fallback = Counter(
                    'bsc_time_window_fallback_total',
                    '时间窗口退让次数',
                    ['original', 'fallback']  # 1m->5m, 5m->1h
                )
                
                # Gauge（仪表）- 可增可减
                self.metrics_connections = Gauge(
                    'bsc_websocket_connections', 
                    'WebSocket连接数'
                )
                self.metrics_cache_size = Gauge(
                    'bsc_cache_size', 
                    '缓存大小',
                    ['cache_type']  # cache_type: seen_txs/receipt/eth_call
                )
                
                # Histogram（直方图）- 延迟分布
                self.metrics_processing_time = Histogram(
                    'bsc_processing_seconds',
                    '事件处理耗时（秒）',
                    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
                )
                
                logger.info("✅ Prometheus Metrics 已启用")
            except Exception as e:
                logger.error(f"❌ Prometheus Metrics 初始化失败: {e}")
                HAS_PROMETHEUS = False
        else:
            logger.warning("⚠️ Prometheus Metrics 未安装")
        
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
                
                logger.info(f"📊 内盘配置: 单笔>={self.min_amount_internal}U, 累计>={self.cumulative_min_amount_internal}U, 时间间隔={self.time_interval_internal}")
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
                
                logger.info(f"📊 外盘配置: 单笔>={self.min_amount_external}U, 累计>={self.cumulative_min_amount_external}U, 时间间隔={self.time_interval_external}")
                
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
        
        # 计算全局最小金额阈值（用于提前过滤）
        self.global_min_amount = min(self.min_amount_internal, self.min_amount_external)
        logger.info(f"🔍 全局最小过滤阈值: {self.global_min_amount}U（取内外盘最小值，提前过滤小额交易）")
        
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
        获取交易回执（带缓存，并发保护，失败缓存）
        
        优化：
        1. Loading状态：防止多个线程同时拉取同一交易
        2. Event等待：后续线程等待第一个线程完成
        3. 失败缓存：RPC失败时缓存5秒，避免风暴式重试
        4. 详细统计：hits/misses/waits/failed_hits
        
        Returns:
            (receipt, tx_info) 或 (None, None) 如果失败
        """
        now = time.time()
        event_to_wait = None
        
        # === 阶段1: 检查缓存（快速路径） ===
        with self.receipt_cache_lock:
            if tx_hash in self.receipt_cache:
                cached_data = self.receipt_cache[tx_hash]
                status = cached_data.get("status", "ready")
                cached_at = cached_data.get("cached_at", 0)
                
                # 情况1: 正在加载中 → 其他线程正在拉取，等待它完成
                if status == "loading":
                    event_to_wait = cached_data.get("event")
                    self.receipt_cache_concurrent_waits += 1
                    logger.info(f"⏳ 并发等待（其他线程正在拉取）: {tx_hash[:10]}... (等待#{self.receipt_cache_concurrent_waits})")
                
                # 情况2: 成功缓存，未过期
                elif status == "ready" and now - cached_at < self.receipt_cache_ttl:
                    receipt = cached_data.get("receipt")
                    tx_info = cached_data.get("tx_info")
                    
                    # 验证数据完整性
                    if receipt and isinstance(receipt, dict) and receipt.get("logs"):
                        self.receipt_cache_hits += 1
                        if HAS_PROMETHEUS:
                            self.metrics_cache_hits.labels(cache_type='receipt').inc()
                        logger.debug(f"✅ 回执缓存命中: {tx_hash[:10]}... (命中#{self.receipt_cache_hits})")
                        return receipt, tx_info
                    else:
                        # 脏数据，删除
                        logger.debug(f"⚠️ 脏数据，重新拉取: {tx_hash[:10]}...")
                        del self.receipt_cache[tx_hash]
                
                # 情况3: 失败缓存，未过期 → 避免短期内重试
                elif status == "failed" and now - cached_at < self.receipt_cache_failed_ttl:
                    self.receipt_cache_failed_hits += 1
                    logger.debug(f"🚫 失败缓存命中（跳过重试）: {tx_hash[:10]}... (失败缓存#{self.receipt_cache_failed_hits})")
                    return None, None
                
                # 情况4: 过期，删除
                else:
                    del self.receipt_cache[tx_hash]
        
        # === 阶段2: 如果需要等待其他线程 ===
        if event_to_wait:
            # OPTIMIZED: 等待5秒适配NodeReal高延迟
            wait_start = time.time()
            wait_result = event_to_wait.wait(timeout=5)
            wait_elapsed = time.time() - wait_start
            
            # 统计等待耗时
            self.receipt_cache_wait_time_total += wait_elapsed
            
            # 检查是否超时
            if not wait_result or wait_elapsed >= 5.5:  # 接近6秒视为超时
                self.receipt_cache_wait_timeouts += 1
                logger.warning(
                    f"⚠️ 并发等待超时: {tx_hash[:10]}... (耗时{wait_elapsed:.2f}s, "
                    f"超时#{self.receipt_cache_wait_timeouts}次，将自行拉取)"
                )
            else:
                logger.info(f"✅ 并发等待完成: {tx_hash[:10]}... (耗时{wait_elapsed:.2f}s)")
            
            # 等待完成后，再次尝试读缓存
            with self.receipt_cache_lock:
                if tx_hash in self.receipt_cache:
                    cached_data = self.receipt_cache[tx_hash]
                    if cached_data.get("status") == "ready":
                        receipt = cached_data.get("receipt")
                        tx_info = cached_data.get("tx_info")
                        if receipt:
                            logger.info(f"✅ 等待后获取结果成功: {tx_hash[:10]}...")
                            return receipt, tx_info
            
            # 如果等待后仍未获取到，说明第一个线程失败了，继续后续流程
            logger.warning(f"⚠️ 等待后仍未获取到数据，自行拉取: {tx_hash[:10]}...")
        
        # === 阶段3: 缓存未命中，占位并拉取 ===
        loading_event = threading.Event()
        
        with self.receipt_cache_lock:
            # 双重检查：可能刚才等待时其他线程已写入
            if tx_hash in self.receipt_cache and self.receipt_cache[tx_hash].get("status") == "ready":
                cached_data = self.receipt_cache[tx_hash]
                receipt = cached_data.get("receipt")
                tx_info = cached_data.get("tx_info")
                if receipt:
                    return receipt, tx_info
            
            # 设置 loading 状态（占位）
            self.receipt_cache[tx_hash] = {
                "status": "loading",
                "event": loading_event,
                "cached_at": now
            }
            self.receipt_cache_misses += 1
            logger.debug(f"🔍 回执缓存未命中，调用RPC: {tx_hash[:10]}... (未命中#{self.receipt_cache_misses})")
        
        # === 阶段4: 锁外执行 RPC（避免阻塞） ===
        try:
            receipt = self.rpc_call("eth_getTransactionReceipt", [tx_hash])
            tx_info = self.rpc_call("eth_getTransactionByHash", [tx_hash])
            
            # 判断是否成功
            success = receipt and isinstance(receipt, dict) and receipt.get("logs")
            
            # 写入缓存
            with self.receipt_cache_lock:
                if success:
                    # 成功：缓存 5 分钟
                    self.receipt_cache[tx_hash] = {
                        "status": "ready",
                        "receipt": receipt,
                        "tx_info": tx_info,
                        "cached_at": now,
                        "event": None
                    }
                else:
                    # 失败：缓存 5 秒（防止风暴式重试）
                    self.receipt_cache[tx_hash] = {
                        "status": "failed",
                        "receipt": None,
                        "tx_info": None,
                        "cached_at": now,
                        "event": None
                    }
                    logger.debug(f"❌ RPC失败，缓存失败状态5秒: {tx_hash[:10]}...")
                
                # 清理过期缓存
                if len(self.receipt_cache) > 5000:
                    to_delete = [
                        k for k, v in self.receipt_cache.items()
                        if now - v.get("cached_at", 0) > max(self.receipt_cache_ttl, self.receipt_cache_failed_ttl)
                    ]
                    for k in to_delete:
                        del self.receipt_cache[k]
                    if to_delete:
                        logger.debug(f"🧹 清理过期回执缓存: {len(to_delete)} 条")
            
            # 通知等待的线程
            loading_event.set()
            
            return receipt, tx_info
            
        except Exception as e:
            logger.debug(f"❌ RPC异常: {tx_hash[:10]}... - {e}")
            
            # 异常也缓存为失败状态
            with self.receipt_cache_lock:
                self.receipt_cache[tx_hash] = {
                    "status": "failed",
                    "receipt": None,
                    "tx_info": None,
                    "cached_at": now,
                    "event": None
                }
            
            loading_event.set()
            return None, None
    
    def cached_eth_call(self, to: str, data: str):
        """
        带缓存的 eth_call（用于代币信息查询）
        
        优化：
        - 缓存 decimals/symbol 等不变的数据
        - USDT/WBNB等常见代币100%命中
        - 减少30-50% eth_call
        """
        cache_key = (to.lower(), data.lower())
        now = time.time()
        
        # 检查缓存
        with self.eth_call_cache_lock:
            if cache_key in self.eth_call_cache:
                result, cached_at = self.eth_call_cache[cache_key]
                if now - cached_at < self.eth_call_cache_ttl:
                    self.eth_call_cache_hits += 1
                    return result
        
        # 缓存未命中，调用RPC
        result = self.rpc_call("eth_call", [{"to": to, "data": data}, "latest"])
        
        # 写入缓存
        if result:  # 只缓存成功的结果
            with self.eth_call_cache_lock:
                self.eth_call_cache[cache_key] = (result, now)
                
                # 清理过期缓存（防止内存泄漏）
                if len(self.eth_call_cache) > 5000:
                    to_delete = [
                        k for k, (_, t) in self.eth_call_cache.items()
                        if now - t > self.eth_call_cache_ttl
                    ]
                    for k in to_delete:
                        del self.eth_call_cache[k]
        
        return result
    
    def rpc_call(self, method: str, params: list):
        """
        发送 HTTP RPC 请求（带429处理 + 慢调用监控）
        
        优化：
        1. 最小间隔：象征性1ms间隔（Chainstack无限制）
        2. 429检测：检测限流错误并指数退避
        3. 退避机制：连续429时延长退避时间（最高16s）
        4. 统计监控：记录429次数和慢调用
        """
        self.rpc_id += 1
        
        # === 阶段1: 速率限制（防止429） ===
        with self.rate_limit_lock:
            # 检查是否在退避期内
            now = time.time()
            if now < self.rate_limit_backoff_until:
                backoff_wait = self.rate_limit_backoff_until - now
                logger.warning(f"⏸️  速率限制退避中，等待 {backoff_wait:.2f}s...")
                time.sleep(backoff_wait)
            
            # 限制最小请求间隔
            elapsed_since_last = now - self.last_rpc_time
            if elapsed_since_last < self.min_rpc_interval:
                time.sleep(self.min_rpc_interval - elapsed_since_last)
            
            self.last_rpc_time = time.time()
        
        # === 阶段2: 发送RPC请求 ===
        start_time = time.time()
        self.rpc_stats[method] = self.rpc_stats.get(method, 0) + 1
        
        try:
            resp = self.session.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": self.rpc_id, "method": method, "params": params},
                timeout=10
            )
            
            # === 阶段3: 检查429限流 ===
            if resp.status_code == 429:
                self.rate_limit_429_count += 1
                self.rate_limit_consecutive_429 += 1
                
                # 指数退避：1s, 2s, 4s, 8s, 最高16s
                backoff_time = min(2 ** self.rate_limit_consecutive_429, 16)
                self.rate_limit_backoff_until = time.time() + backoff_time
                
                logger.warning(
                    f"🚫 遇到429限流 (累计#{self.rate_limit_429_count}, 连续#{self.rate_limit_consecutive_429}次), "
                    f"退避 {backoff_time}s, method={method}"
                )
                
                # 返回None，让上层缓存为failed状态
                return None
            
            # === 阶段4: 成功响应，重置连续429计数 ===
            if resp.status_code == 200:
                self.rate_limit_consecutive_429 = 0  # 重置连续429计数
            
            result = resp.json().get("result")
            
            # === 阶段5: 慢调用监控 ===
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
            
            # L3: 链上查询（使用缓存）
            data = self.cached_eth_call(token, "0x313ce567")
            
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
            
            # L3: 链上查询（使用缓存）
            data = self.cached_eth_call(token, "0x95d89b41")
            
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
            
            # L3: 链上查询（使用缓存）
            token0_data = self.cached_eth_call(pair, "0x0dfe1681")
            token1_data = self.cached_eth_call(pair, "0xd21220a7")
            
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
    
    async def send_alert(self, message: str, token_address: str) -> bool:
        """
        发送 Telegram 通知
        
        Returns:
            bool: 是否发送成功
        """
        if not self.enable_telegram:
            logger.debug(f"⏭️  Telegram未启用，跳过发送")
            return False
        
        try:
            reply_markup = self.create_token_buttons(token_address)
            
            result = await self.telegram_notifier.send(
                target=self.bsc_channel_id,
                message=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            
            if result:
                logger.info(f"✅ Telegram通知已发送 - {token_address[:10]}...")
                self.alert_success_count += 1  # 发送成功计数
                return True
            else:
                logger.error(f"❌ Telegram发送失败 - {token_address[:10]}...")
                self.alert_fail_count += 1  # 发送失败计数
                return False
        
        except Exception as e:
            logger.error(f"❌ 发送通知异常: {e}")
            self.alert_fail_count += 1  # 异常也算发送失败
            return False
    
    async def check_external_is_fourmeme(self, token_address: str) -> tuple[bool, bool, Optional[Dict]]:
        """
        检查外盘代币是否来自 fourmeme 平台
        
        Returns:
            (is_fourmeme, is_confirmed, launchpad_info):
            - is_fourmeme: 是否是fourmeme
            - is_confirmed: 是否确认结果（False表示API失败，结果不确定）
            - launchpad_info: 详细信息（仅当is_fourmeme=True时有值）
            
        示例:
            (True, True, {...})   - 确认是fourmeme
            (False, True, None)   - 确认不是fourmeme（可以加黑名单）
            (False, False, None)  - API失败，未知（不应加黑名单）
        """
        dbotx_api = self.get_thread_dbotx_api()
        
        try:
            launchpad_info = await dbotx_api.get_token_launchpad_info('bsc', token_address)
            
            if not launchpad_info:
                # API失败或无数据，结果不确定
                # 这可能是：1) API故障  2) 网络问题  3) token太新还没数据
                # 为安全起见，不确认结果
                return (False, False, None)
            
            launchpad = launchpad_info.get('launchpad', '').lower()
            
            if launchpad == 'fourmeme':
                # 确认是fourmeme
                return (True, True, launchpad_info)
            elif launchpad:
                # 有明确的launchpad信息（如pancake_v2），确认不是fourmeme
                return (False, True, None)
            else:
                # launchpad为空，可能是数据不完整，不确认
                return (False, False, None)
        
        except Exception as e:
            logger.error(f"❌ 检查 Launchpad 失败: {e}")
            # API异常，结果不确定
            return (False, False, None)
    
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
            
            # 7. 获取指标数据 + 时间窗口退让策略
            price_change = token_data.get('price_change', 0)
            volume = token_data.get('volume', 0)
            symbol = token_data.get('symbol', 'Unknown')
            fallback_info = None  # 用于记录退让信息（给TG播报用）
            
            # 时间窗口退让：如果数据为0，自动退让到更长时间窗口
            if price_change == 0 and volume == 0:
                original_interval = time_interval
                fallback_interval = None
                
                # 定义退让链（1m→5m停止，5m→1h停止）
                if time_interval == '1m':
                    fallback_interval = '5m'
                elif time_interval == '5m':
                    fallback_interval = '1h'
                
                # 尝试退让
                if fallback_interval:
                    logger.info(f"   🔄 {original_interval}数据为0，尝试退让至{fallback_interval}")
                    fallback_data = dbotx_api.parse_token_data(raw_data, fallback_interval)
                    if fallback_data:
                        fallback_price_change = fallback_data.get('price_change', 0)
                        fallback_volume = fallback_data.get('volume', 0)
                        
                        if fallback_price_change != 0 or fallback_volume != 0:
                            # 退让成功，使用退让数据
                            price_change = fallback_price_change
                            volume = fallback_volume
                            time_interval = fallback_interval  # 更新时间窗口
                            fallback_info = {
                                'original': original_interval,
                                'fallback': fallback_interval,
                                'reason': f'{original_interval}数据为0'
                            }
                            # Prometheus: 时间窗口退让计数
                            if HAS_PROMETHEUS:
                                self.metrics_fallback.labels(original=original_interval, fallback=fallback_interval).inc()
                            logger.info(f"   ✅ 退让成功: 使用{fallback_interval}数据 (涨幅{price_change:+.2f}%, 交易量${volume:,.2f})")
                        else:
                            logger.info(f"   ❌ {fallback_interval}数据也为0，无法退让")
                    else:
                        logger.warning(f"   ⚠️ 解析{fallback_interval}数据失败")
            
            # 8. 构造 stats 数据（用于 TriggerLogic）
            stats = {
                'priceChange': price_change,
                'volume': volume,
                'holderChange': 0
            }
            
            # 9. 选择对应的 events_config 和 trigger_logic
            events_config = self.internal_events_config if is_internal else self.external_events_config
            trigger_logic = self.trigger_logic_internal if is_internal else self.trigger_logic_external
            
            # 第二层检查计数
            if is_internal:
                self.second_layer_check_internal += 1
                if HAS_PROMETHEUS:
                    self.metrics_second_layer_check.labels(type='internal').inc()
            else:
                self.second_layer_check_external += 1
                if HAS_PROMETHEUS:
                    self.metrics_second_layer_check.labels(type='external').inc()
            
            logger.info(f"🔎 [第二层检查] {pool_emoji}{pool_type} {symbol} ({token_address})")
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
            
            # 第二层通过计数
            if is_internal:
                self.second_layer_pass_internal += 1
                if HAS_PROMETHEUS:
                    self.metrics_second_layer_pass.labels(type='internal').inc()
            else:
                self.second_layer_pass_external += 1
                if HAS_PROMETHEUS:
                    self.metrics_second_layer_pass.labels(type='external').inc()
            
            token_data['pool_type'] = pool_type
            token_data['is_internal'] = is_internal
            token_data['pool_emoji'] = pool_emoji
            token_data['triggered_events'] = triggered_events
            token_data['fallback_info'] = fallback_info  # 时间窗口退让信息
            
            return token_data
        
        except Exception as e:
            logger.error(f"❌ 第二层过滤失败: {e}")
            return None
    
    async def handle_swap_event(self, log: Dict):
        """
        处理 PancakeSwap Swap 事件（外盘）
        
        🚀 优化：使用 DBotX API 替代 RPC 调用（带降级策略）
        - 优先路径：1次API调用获取所有数据
        - 降级路径：API失败 → RPC获取token0/token1 → 继续处理
        """
        tx_hash = log.get("transactionHash")
        pair_address = log.get("address", "").lower()
        swap_data = self.parse_swap_data(log.get("data"))
        
        if not swap_data:
            # WebSocket数据解析失败，尝试从receipt兜底
            await self._handle_swap_with_receipt_fallback(tx_hash, pair_address)
            return
        
        # 🚀 优先路径：尝试使用 DBotX API 获取交易对信息（复用连接池）
        mint = None
        base_mint = None
        base_symbol = None
        token_symbol = None
        use_api_data = False  # 标记是否使用 API 完整数据
        pair_info_raw = None
        
        # 🚀 优化：检查"无数据pair"缓存
        no_data_key = f"no_data_pair:{pair_address}"
        skip_api = False  # 标记是否跳过API调用
        
        if self.redis_client:
            try:
                if self.redis_client.get(no_data_key):
                    skip_api = True
                    logger.debug(f"⏭️  [缓存命中] pair无API数据，跳过API直接走RPC: {pair_address[:10]}...")
            except Exception as e:
                logger.debug(f"Redis查询失败: {e}")
        
        # 如果缓存未命中，尝试API
        if not skip_api:
            dbotx_api = self.get_thread_dbotx_api()
            pair_info_raw = await dbotx_api.get_pair_info('bsc', pair_address)
            
            # 🔍 调试：打印API返回的完整字段（仅打印前3个，避免刷屏）
            if pair_info_raw is not None and hasattr(self, '_api_debug_count'):
                if self._api_debug_count < 3:
                    logger.info(f"🔍 [调试] API返回字段: {list(pair_info_raw.keys())[:20]}")
                    self._api_debug_count += 1
            elif pair_info_raw is not None and not hasattr(self, '_api_debug_count'):
                self._api_debug_count = 1
                logger.info(f"🔍 [调试] API返回的所有字段名: {list(pair_info_raw.keys())}")
        
        # 检查 API 返回（可能是 None、空字典 {}、或有数据的字典）
        if pair_info_raw is not None:  # 排除 None
            mint = pair_info_raw.get('mint', '').lower()
            base_mint = pair_info_raw.get('baseMint', '').lower()
            base_symbol = pair_info_raw.get('baseSymbol', '')
            token_symbol = pair_info_raw.get('symbol', '')
            
            # 检查关键字段是否存在且非空
            # 注意：空字典 {} 会进入这个分支，但 mint/base_mint 会是空字符串
            if mint and base_mint:  # 两者都非空才使用 API 数据
                use_api_data = True
                logger.info(f"⚡ [快速路径] API数据完整: {pair_address[:10]}... (mint={mint[:10]}, base={base_mint[:10]})")
            else:
                logger.info(f"🔄 [降级] API数据不完整（空字典或缺字段）: {pair_address[:10]}... → 使用RPC")
                # 🚀 缓存无数据pair（1小时），避免重复RPC查询
                if self.redis_client:
                    try:
                        self.redis_client.set(no_data_key, "1", ex=3600)
                        logger.debug(f"✅ 已缓存无数据pair: {pair_address[:10]}...")
                    except Exception as e:
                        logger.debug(f"Redis写入失败: {e}")
        else:
            logger.info(f"🔄 [降级] API返回None: {pair_address[:10]}... → 使用RPC")
            # 🚀 缓存无数据pair（1小时）
            if self.redis_client:
                try:
                    self.redis_client.set(no_data_key, "1", ex=3600)
                    logger.debug(f"✅ 已缓存无数据pair: {pair_address[:10]}...")
                except Exception as e:
                    logger.debug(f"Redis写入失败: {e}")
        
        # 🔄 降级路径：API 失败或数据不完整，使用 RPC 获取 token0/token1
        pair_info_rpc = None  # RPC获取的pair信息
        if not use_api_data:
            
            # 使用原来的 RPC 方式
            pair_info_rpc = self.get_pair_full_info(pair_address)
            if not pair_info_rpc:
                logger.debug(f"⏭️  RPC 也失败，跳过: {pair_address[:10]}...")
            return
        
            mint = pair_info_rpc['token0'].lower()  # 根据测试，token0 = mint
            base_mint = pair_info_rpc['token1'].lower()  # token1 = baseMint
            token_symbol = pair_info_rpc.get('symbol0', '???')
            base_symbol = pair_info_rpc.get('symbol1', '???')
            
            # 标记为降级模式（后续需要调用 second_layer_filter）
            # 注意：保持 pair_info_raw = None 用于判断，但后续使用 pair_info_rpc 获取数据
            pair_info_raw = None
        
        # 快速过滤：检查基础货币是否是我们关注的稳定币
        if base_mint not in (self.USDT, self.USDC, self.WBNB):
            return
        
        # 解析交易数据
        amount0_in = swap_data["amount0In"]
        amount1_in = swap_data["amount1In"]
        amount0_out = swap_data["amount0Out"]
        amount1_out = swap_data["amount1Out"]
        
        # 判断是否是买入行为（稳定币输入 → 主代币输出）
        # 根据测试结果：mint=token0, baseMint=token1 (100%匹配)
        quote_token = None
        base_token = None
        quote_amount = 0
        base_amount = 0
        quote_decimals = 18  # 稳定币精度默认18
        # 🔧 修复：RPC路径下从 pair_info_rpc 获取精度，API路径从 pair_info_raw 获取
        if pair_info_raw:
            base_decimals = pair_info_raw.get('decimals', 18)  # API路径
        elif pair_info_rpc:
            base_decimals = pair_info_rpc.get('decimals0', 18)  # RPC路径
        else:
            base_decimals = 18  # 兜底
        quote_symbol = base_symbol
        base_symbol = token_symbol
        
        if amount0_in > 0 and amount1_out > 0:
            # token0输入 → token1输出
            # 这种情况通常不是买入（token0是主代币，token1是稳定币）
            # 但我们仍需检查
            if mint == base_mint:  # 特殊情况：稳定币对
                return
            logger.debug(f"⏭️  可能是卖出：token0输入 → token1输出")
            return
            
        elif amount1_in > 0 and amount0_out > 0:
            # token1输入 → token0输出
            # 根据测试：token1=baseMint（稳定币），token0=mint（主代币）
            # 这是标准的买入行为 ✓
            quote_token = base_mint  # 稳定币
            base_token = mint  # 主代币
            quote_amount = amount1_in
            base_amount = amount0_out
        else:
            # 其他情况：可能是复杂交易
            return
        
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
        self.first_layer_pass_external += 1  # 外盘第一层计数
        
        # Prometheus: 第一层通过计数
        if HAS_PROMETHEUS:
            self.metrics_first_layer_pass.labels(type='external').inc()
        
        # 🚀 优化：先检查 Redis 缓存（非fourmeme token黑名单）
        if self.redis_client:
            try:
                is_cached_non_fourmeme = self.redis_client.sismember(self.NON_FOURMEME_KEY, base_token)
                if is_cached_non_fourmeme:
                    self.cache_hit_count += 1
                    logger.info(f"⏭️  [外盘] 非fourmeme (缓存命中 #{self.cache_hit_count}): {base_symbol} (${usd_value:.2f}) - {base_token[:10]}...")
                    return
            except Exception as e:
                logger.warning(f"⚠️  Redis缓存查询失败: {e}")
        
        # 检查是否是 fourmeme
        is_fourmeme = False
        is_confirmed = False  # 是否能确认（API 有数据）
        
        if use_api_data and pair_info_raw:
            # 快速路径：使用 API 已返回的数据
            pre_dex = pair_info_raw.get('preDex', '').lower()
            pool_type = pair_info_raw.get('poolType', '').lower()
            is_fourmeme = (pre_dex == 'fourmeme' or pool_type == 'fourmeme')
            is_confirmed = True
        else:
            # 降级路径：使用原有的 API 检查
            logger.debug(f"🔄 [降级] 调用 check_external_is_fourmeme: {base_token[:10]}...")
            is_fourmeme, is_confirmed, launchpad_info = await self.check_external_is_fourmeme(base_token)
        
        if not is_fourmeme:
            if is_confirmed:
                # 确认不是fourmeme → 加入Redis黑名单（30天过期）
                if self.redis_client:
                    try:
                        self.redis_client.client.sadd(self.NON_FOURMEME_KEY, base_token)
                        self.redis_client.client.expire(self.NON_FOURMEME_KEY, self.NON_FOURMEME_TTL)
                        logger.debug(f"✅ 已加入黑名单: {base_symbol} - {base_token[:10]}...")
                    except Exception as e:
                        logger.warning(f"⚠️  Redis缓存写入失败: {e}")
                    
                    # 🔍 详细日志：显示判定依据
                    if use_api_data and pair_info_raw:
                        pre_dex = pair_info_raw.get('preDex', 'N/A')
                        pool_type = pair_info_raw.get('poolType', 'N/A')
                        logger.info(f"⏭️  [外盘] 非fourmeme，跳过: {base_symbol} (${usd_value:.2f}) | preDex={pre_dex}, poolType={pool_type} | {base_token[:10]}...")
                    else:
                        logger.info(f"⏭️  [外盘] 非fourmeme，跳过: {base_symbol} (${usd_value:.2f}) | {base_token[:10]}...")
            else:
                # API 失败，不确定 → 不加黑名单
                logger.info(f"⚠️  [外盘] fourmeme检查失败（API故障），跳过但不加黑名单: {base_symbol} - {base_token[:10]}...")
            return
        
        # 🔍 详细日志：显示判定依据
        if use_api_data and pair_info_raw:
            pre_dex = pair_info_raw.get('preDex', 'N/A')
            pool_type = pair_info_raw.get('poolType', 'N/A')
            logger.info(f"✅ [外盘] 是fourmeme: {base_symbol} (${usd_value:.2f}) | preDex={pre_dex}, poolType={pool_type} | {base_token[:10]}...")
        else:
            logger.info(f"✅ [外盘] 是fourmeme: {base_symbol} (${usd_value:.2f}) | {base_token[:10]}...")
        
        # 🚀 第二层过滤：区分快速路径和降级路径
        if use_api_data and pair_info_raw:
            # ============================================
            # 快速路径：直接使用 API 返回的数据进行第二层判断
            # ============================================
            logger.info(f"⚡ [快速路径] 使用API数据进行第二层检查: {base_token[:10]}...")
            
            token_price_usd = pair_info_raw.get('tokenPriceUsd', 0)
            market_cap = pair_info_raw.get('marketCap', 0)
            
            # 获取配置的时间间隔
            time_interval = self.time_interval_external  # 外盘
            
            # 根据时间间隔选择对应的涨跌幅和交易量 + 退让策略
            fallback_info = None  # 退让信息
            
            if time_interval == '1m':
                price_change = pair_info_raw.get('priceChange1m', 0) * 100
                volume = pair_info_raw.get('buyAndSellVolume1m', 0)
            elif time_interval == '5m':
                price_change = pair_info_raw.get('priceChange5m', 0) * 100
                volume = pair_info_raw.get('buyAndSellVolume5m', 0)
            elif time_interval == '1h':
                price_change = pair_info_raw.get('priceChange1h', 0) * 100
                volume = pair_info_raw.get('buyAndSellVolume1h', 0)
            else:
                price_change = pair_info_raw.get('priceChange5m', 0) * 100  # 默认5分钟
                volume = pair_info_raw.get('buyAndSellVolume5m', 0)
            
            # 时间窗口退让：如果数据为0，自动退让到更长时间窗口
            if price_change == 0 and volume == 0:
                original_interval = time_interval
                fallback_interval = None
                
                # 定义退让链（1m→5m停止，5m→1h停止）
                if time_interval == '1m':
                    fallback_interval = '5m'
                elif time_interval == '5m':
                    fallback_interval = '1h'
                
                # 尝试退让
                if fallback_interval:
                    logger.info(f"   🔄 [外盘快速路径] {original_interval}数据为0，尝试退让至{fallback_interval}")
                    
                    if fallback_interval == '5m':
                        fallback_price_change = pair_info_raw.get('priceChange5m', 0) * 100
                        fallback_volume = pair_info_raw.get('buyAndSellVolume5m', 0)
                    elif fallback_interval == '1h':
                        fallback_price_change = pair_info_raw.get('priceChange1h', 0) * 100
                        fallback_volume = pair_info_raw.get('buyAndSellVolume1h', 0)
                    else:
                        fallback_price_change = 0
                        fallback_volume = 0
                    
                    if fallback_price_change != 0 or fallback_volume != 0:
                        # 退让成功
                        price_change = fallback_price_change
                        volume = fallback_volume
                        time_interval = fallback_interval
                        fallback_info = {
                            'original': original_interval,
                            'fallback': fallback_interval,
                            'reason': f'{original_interval}数据为0'
                        }
                        # Prometheus: 时间窗口退让计数
                        if HAS_PROMETHEUS:
                            self.metrics_fallback.labels(original=original_interval, fallback=fallback_interval).inc()
                        logger.info(f"   ✅ 退让成功: 使用{fallback_interval}数据 (涨幅{price_change:+.2f}%, 交易量${volume:,.2f})")
                    else:
                        logger.info(f"   ❌ {fallback_interval}数据也为0，无法退让")
            
            # 获取外盘配置（从 external_events_config 读取）
            external_config = self.external_events_config
            
            # Prometheus: 外盘快速路径第二层检查计数
            if HAS_PROMETHEUS:
                self.metrics_second_layer_check.labels(type='external').inc()
            
            # 第二层判断：涨跌幅和交易量
            min_price_change = external_config.get('priceChange', {}).get('risePercent', 50)  # 默认50%
            min_volume = external_config.get('volume', {}).get('threshold', 20000)  # 默认$20000
            
            # 检查是否满足条件
            triggered_events = []
            
            # 检查涨跌幅
            price_change_enabled = external_config.get('priceChange', {}).get('enabled', True)
            if price_change_enabled:
                if price_change >= min_price_change:
                    triggered_events.append({'event': 'priceChange', 'value': price_change})
                    logger.info(f"   ✅ 涨跌幅达标: {price_change:+.2f}% >= {min_price_change}%")
                else:
                    logger.info(f"   ⏭️  涨跌幅不足: {price_change:.2f}% < {min_price_change}%")
            
            # 检查交易量
            volume_enabled = external_config.get('volume', {}).get('enabled', True)
            if volume_enabled:
                if volume >= min_volume:
                    triggered_events.append({'event': 'volume', 'value': volume})
                    logger.info(f"   ✅ 交易量达标: ${volume:.2f} >= ${min_volume}")
                else:
                    logger.info(f"   ⏭️  交易量不足: ${volume:.2f} < ${min_volume}")
            
            # 根据触发逻辑判断是否通过第二层
            trigger_logic = self.trigger_logic_external  # 'any' 或 'all'
            
            if trigger_logic == 'all':
                # 要求所有启用的指标都达标
                required_events = []
                if price_change_enabled:
                    required_events.append('priceChange')
                if volume_enabled:
                    required_events.append('volume')
                
                triggered_event_names = {e['event'] for e in triggered_events}
                if not all(evt in triggered_event_names for evt in required_events):
                    logger.info(f"   ⏭️  未满足'all'触发逻辑（需要所有指标）")
                    return
            elif trigger_logic == 'any':
                # 只要有一个指标达标即可
                if not triggered_events:
                    logger.info(f"   ⏭️  未满足'any'触发逻辑（至少一个指标）")
                    return
            
            logger.info(f"✅ 通过第二层: 触发事件={[e['event'] for e in triggered_events]}")
            
            # 外盘快速路径通过第二层计数
            self.second_layer_pass_external += 1
            if HAS_PROMETHEUS:
                self.metrics_second_layer_pass.labels(type='external').inc()
            
            # 构建 token_data（兼容原有格式）
            token_data = {
                'symbol': token_symbol,
                'price': token_price_usd,
                'price_change': price_change,
                'volume': volume,
                'market_cap': market_cap,
                'buy_tax': pair_info_raw.get('safetyInfo', {}).get('buyTax', 0) if pair_info_raw.get('safetyInfo') else 0,
                'sell_tax': pair_info_raw.get('safetyInfo', {}).get('sellTax', 0) if pair_info_raw.get('safetyInfo') else 0,
                'pool_type': pool_type or 'pancake_v2',
                'pool_emoji': '🔥',
                'is_internal': False,
                'triggered_events': triggered_events,
                'fallback_info': fallback_info  # 时间窗口退让信息
            }
        else:
            # ============================================
            # 降级路径：如果 no_data_pair 已缓存，避免再次调用 API
            # ============================================
            if skip_api:
                # 缓存命中：pair 无API数据，跳过 second_layer_filter（避免再次调用API）
                # 直接返回，不发送告警（因为无法获取准确指标）
                logger.info(f"⏭️  [彻底跳过] pair已缓存为无数据，RPC也无法提供完整指标: {base_token[:10]}...")
                return
            else:
                # 缓存未命中：正常调用 second_layer_filter（会调用一次API）
                logger.info(f"🔄 [降级路径] 调用second_layer_filter获取指标: {base_token[:10]}...")
                
                # 构造 launchpad_info（兼容 second_layer_filter）
                launchpad_info = {
                    'launchpad': 'fourmeme',
                    'launchpad_status': 1,  # 外盘
                    'pair_address': pair_address
                }
                
                # 调用统一的第二层过滤
        token_data = await self.second_layer_filter(base_token, pair_address, launchpad_info, is_internal=False)
                
        if not token_data:
            logger.info(f"⏭️  [降级路径] 第二层过滤未通过: {base_token[:10]}...")
            return
        
        logger.info(f"✅ [降级路径] 通过第二层: 触发事件={[e['event'] for e in token_data.get('triggered_events', [])]}")
        
        # 🔒 关键：检查冷却期（只读，不设置）
        # 避免为已在冷却期的代币构建消息
        if not await self.check_alert_cooldown_readonly(base_token):
            logger.info(f"⏳ 冷却期内，跳过: {base_token}")
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
        
        volume_str = format_number(volume)
        market_cap_str = format_number(market_cap)
        
        price_str = f"${price:.5f} USDT" if price >= 0.01 else f"${price:.10f} USDT"
        
        triggered_events = token_data.get('triggered_events', [])
        fallback_info = token_data.get('fallback_info')  # 获取退让信息
        
        alert_reasons = []
        for event in triggered_events:
            if hasattr(event, 'description'):
                alert_reasons.append(event.description)
            elif isinstance(event, dict):
                if event.get('event') == 'priceChange':
                    alert_reasons.append(f"📈 {time_interval}涨幅 {price_change:+.2f}%")
                elif event.get('event') == 'volume':
                    alert_reasons.append(f"💹 {time_interval}交易量 ${volume_str}")
        
        # 如果有退让信息，添加到告警原因
        if fallback_info:
            original = fallback_info['original']
            fallback = fallback_info['fallback']
            reason = fallback_info['reason']
            alert_reasons.append(f"⚠️ {reason}，采用{fallback}数据")
        
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
        
        # 🚀 发送推送，成功后才设置冷却期
        send_success = await self.send_alert(message, base_token)
        
        if send_success:
            # ✅ 播报成功，设置冷却期
            self.alert_success_count += 1
            if HAS_PROMETHEUS:
                self.metrics_alerts.labels(status='success').inc()
            await self.check_and_set_alert_cooldown(base_token)
            logger.info(f"✅ 已设置冷却期: {base_token[:10]}... ({self.cooldown_minutes}分钟)")
        else:
            # ❌ 播报失败，不设置冷却期，允许下次重试
            self.alert_fail_count += 1
            if HAS_PROMETHEUS:
                self.metrics_alerts.labels(status='failure').inc()
            logger.warning(f"⚠️  播报失败，未设置冷却期: {base_token[:10]}...")
        
        # 记录到数据库并推送WebSocket（无论通知是否成功）
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
            notify_error=None if send_success else "Telegram发送失败"
        )
    
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
        topics = log.get("topics", [])
        
        proxy_type = "主Proxy" if addr == self.FOURMEME_PROXY[0] else "Try Buy"
        
        try:
            dbotx_api = self.get_thread_dbotx_api()
            
            # ========== 快速路径：Custom Events（TokenPurchase/Sale）==========
            if topics and topics[0] in self.FOURMEME_CUSTOM_EVENTS:
                try:
                    # TokenPurchase/Sale 事件格式：
                    # event TokenPurchase(address indexed token, address indexed buyer, uint256 cost, uint256 amount)
                    # topics[0]: event signature
                    # topics[1]: token address (indexed)
                    # topics[2]: buyer address (indexed)  
                    # data: cost (uint256) + amount (uint256)
                    
                    if len(topics) < 3:
                        logger.debug(f"⚠️ Custom Event topics不足: {len(topics)}")
                        # 继续走兜底逻辑
                    else:
                        target_token = ("0x" + topics[1][-40:]).lower()
                        buyer = ("0x" + topics[2][-40:]).lower()
                        
                        # 解码 data
                        # TokenPurchase事件完整格式：8个非索引参数
                        # (address indexed token, address indexed buyer, 
                        #  address payToken, uint256 payAmount, uint256 getAmount, 
                        #  uint256 curvePrice, uint256 protocolFee, uint256 subjectFee, 
                        #  uint256 referralFee, uint256 supply)
                        data = log.get("data", "0x")
                        if data and len(data) >= 66:
                            try:
                                # 使用eth_abi解码（如果可用）
                                if HAS_ETH_ABI:
                                    try:
                                        decoded = eth_abi_decode(['address', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256'], bytes.fromhex(data[2:]))
                                        pay_token = decoded[0]  # 支付代币地址
                                        cost = decoded[1]  # 支付金额
                                        amount = decoded[2]  # 获得代币数量
                                    except:
                                        # Fallback: 手动解析前2个uint256
                                        cost = int(data[2:66], 16) if len(data) >= 66 else 0
                                        amount = int(data[66:130], 16) if len(data) >= 130 else 0
                                else:
                                    # Fallback: 手动解析
                                    # 跳过第一个address(32字节)，取第2、3个uint256
                                    cost = int(data[66:130], 16) if len(data) >= 130 else 0
                                    amount = int(data[130:194], 16) if len(data) >= 194 else 0
                                
                                if cost > 0:
                                    logger.info(f"⚡ [内盘快速] Custom Event: {tx_hash[:10]}...")
                                    logger.info(f"   Token: {target_token[:10]}... | Buyer: {buyer[:10]}...")
                                    logger.info(f"   Cost: {cost} (raw) | Amount: {amount} (raw)")
                                    
                                    # 直接处理（跳过 receipt！）
                                    # 假设 cost 是 USDT（18 decimals），如果是 WBNB 需要进一步判断
                                    quote_token = self.USDT  # 默认 USDT，可以根据实际情况调整
                                    quote_amount = cost
                                    quote_symbol = "USDT"
                                    target_amount = amount
                                    
                                    # 获取 token symbol 和 decimals
                                    target_symbol = self.get_token_symbol(target_token)
                                    quote_decimals = self.get_decimals(quote_token)
                                    target_decimals = self.get_decimals(target_token)
                                    
                                    # 计算 USD 价值（cost 就是支付的 USDT）
                                    quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
                                    usd_value = float(quote_value)  # USDT ≈ $1
                                    
                                    # 第一层过滤：金额检查
                                    if not self.first_layer_filter(usd_value, is_internal=True):
                                        logger.debug(f"⏭️  [内盘快速] 金额不足: {target_symbol} (${usd_value:.2f})")
                                        return
                                    
                                    logger.info(f"✅ [内盘快速] {target_symbol} 买入 ${usd_value:.2f}")
                                    
                                    # 冷却期检查（只读）
                                    if not await self.check_alert_cooldown_readonly(target_token):
                                        logger.info(f"⏳ [内盘快速] 冷却期内，跳过: {target_token[:10]}...")
                                        return
                                    
                                    # 获取 launchpad 信息（轻量 API 调用）
                                    launchpad_info = await dbotx_api.get_token_launchpad_info('bsc', target_token)
                                    if not launchpad_info:
                                        # Fallback：构造基础信息
                                        launchpad_info = {
                                            'launchpad': 'fourmeme',
                                            'pair_address': None
                                        }
                                    
                                    pair_address = launchpad_info.get('pair_address')
                                    if not pair_address:
                                        logger.debug(f"⚠️ [内盘快速] 无pair地址: {target_token[:10]}...")
                                        return
                                    
                                    # 第二层过滤（获取市值等）
                                    token_data = await self.second_layer_filter(target_token, pair_address, launchpad_info, is_internal=True)
                                    if not token_data:
                                        logger.debug(f"⏭️  [内盘快速] 未通过第二层过滤: {target_token[:10]}...")
                                        return
                                    
                                    # 设置冷却期（原子操作）
                                    if not await self.check_and_set_alert_cooldown(target_token):
                                        logger.info(f"⏳ [内盘快速] 冷却期内（竞态），跳过: {target_token[:10]}...")
                                        return
                                    
                                    # 构建并发送告警
                                    await self._send_internal_alert(
                                        tx_hash=tx_hash,
                                        target_token=target_token,
                                        target_symbol=target_symbol,
                                        target_amount=target_amount,
                                        target_decimals=target_decimals,
                                        quote_symbol=quote_symbol,
                                        quote_amount=quote_amount,
                                        quote_decimals=quote_decimals,
                                        usd_value=usd_value,
                                        token_data=token_data,
                                        proxy_type=proxy_type
                                    )
                                    
                                    logger.info(f"📤 [内盘快速] 告警已发送: {target_symbol} ${usd_value:.2f}")
                                    return  # ⚡ 快速返回，不走 receipt 逻辑
                            except Exception as e:
                                logger.debug(f"Custom Event 解码失败: {e}")
                                # 继续走兜底逻辑
                except Exception as e:
                    logger.debug(f"Custom Event 快速路径失败: {e}")
                    # 继续走兜底逻辑
            
            # ========== 兜底路径：从 Receipt 解析 Transfer ==========
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
            self.first_layer_pass_internal += 1  # 内盘第一层计数
            
            # Prometheus: 第一层通过计数
            if HAS_PROMETHEUS:
                self.metrics_first_layer_pass.labels(type='internal').inc()
            
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
            
            # 🔒 关键：检查冷却期（只读，不设置）
            # 避免为已在冷却期的代币构建消息
            if not await self.check_alert_cooldown_readonly(target_token):
                logger.info(f"⏳ 冷却期内，跳过: {target_token}")
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
            
            volume_str = format_number(volume)
            market_cap_str = format_number(market_cap)
            
            price_str = f"${price:.5f} USDT" if price >= 0.01 else f"${price:.10f} USDT"
            
            triggered_events = token_data.get('triggered_events', [])
            fallback_info = token_data.get('fallback_info')  # 获取退让信息
            
            alert_reasons = []
            for event in triggered_events:
                if hasattr(event, 'description'):
                    alert_reasons.append(event.description)
                elif isinstance(event, dict):
                    if event.get('event') == 'priceChange':
                        alert_reasons.append(f"📈 {time_interval}涨幅 {price_change:+.2f}%")
                    elif event.get('event') == 'volume':
                        alert_reasons.append(f"💹 {time_interval}交易量 ${volume_str}")
            
            # 如果有退让信息，添加到告警原因
            if fallback_info:
                original = fallback_info['original']
                fallback = fallback_info['fallback']
                reason = fallback_info['reason']
                alert_reasons.append(f"⚠️ {reason}，采用{fallback}数据")
            
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
            
            # 🚀 发送推送，成功后才设置冷却期
            send_success = await self.send_alert(message, target_token)
            
            if send_success:
                # ✅ 播报成功，设置冷却期
                self.alert_success_count += 1
                if HAS_PROMETHEUS:
                    self.metrics_alerts.labels(status='success').inc()
                await self.check_and_set_alert_cooldown(target_token)
                logger.info(f"✅ 已设置冷却期: {target_token[:10]}... ({self.cooldown_minutes}分钟)")
            else:
                # ❌ 播报失败，不设置冷却期，允许下次重试
                self.alert_fail_count += 1
                if HAS_PROMETHEUS:
                    self.metrics_alerts.labels(status='failure').inc()
                logger.warning(f"⚠️  播报失败，未设置冷却期: {target_token[:10]}...")
            
            # 记录到数据库并推送WebSocket（无论通知是否成功）
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
                notify_error=None if send_success else "Telegram发送失败"
            )
        
        except Exception as e:
            logger.error(f"❌ 处理内盘交易出错: {e}")
    
    async def _send_internal_alert(
        self,
        tx_hash: str,
        target_token: str,
        target_symbol: str,
        target_amount: int,
        target_decimals: int,
        quote_symbol: str,
        quote_amount: int,
        quote_decimals: int,
        usd_value: float,
        token_data: dict,
        proxy_type: str
    ):
        """发送内盘告警（供快速路径和兜底路径共用）"""
        try:
            # 格式化金额
            quote_formatted = self.format_amount(quote_amount, quote_decimals)
            target_formatted = self.format_amount(target_amount, target_decimals)
            
            # 提取 token_data
            pool_emoji = token_data['pool_emoji']
            pool_type = token_data['pool_type']
            is_internal = token_data.get('is_internal', True)
            symbol = token_data.get('symbol', target_symbol)
            price_change = token_data.get('price_change', 0)
            volume = token_data.get('volume', 0)
            market_cap = token_data.get('market_cap', 0)
            price = token_data.get('price', 0)
            
            # 格式化数字（使用已导入的format_number）
            volume_str = format_number(volume)
            market_cap_str = format_number(market_cap)
            price_str = f"${price:.5f} USDT" if price >= 0.01 else f"${price:.10f} USDT"
            
            # 获取时间间隔
            time_interval = self.time_interval_internal if is_internal else self.time_interval_external
            
            # 构建告警原因
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
            
            # 构建消息
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
📊 {time_interval}交易量: ${volume_str}
📈 {time_interval}涨跌幅: {price_change:+.2f}%

🔔 <b>触发原因</b>
{chr(10).join(alert_reasons)}
"""
            
            # 使用现有方法发送（会自动创建GMGN+Axiom按钮）
            send_success = await self.send_alert(message, target_token)
            
            # 记录到数据库（使用现有recorder）
            if hasattr(self, 'alert_recorder') and self.alert_recorder:
                try:
                    await self.alert_recorder.write_bsc_alert(
                        token=target_token,
                        symbol=symbol,
                        tx_hash=tx_hash,
                        pool_type=pool_type,
                        price_change=price_change,
                        volume=volume,
                        market_cap=market_cap,
                        amount=usd_value,
                        alert_reason=", ".join(alert_reasons),
                        notify_error=None if send_success else "Telegram发送失败"
                    )
                except Exception as e:
                    logger.debug(f"记录告警到数据库失败: {e}")
            
            logger.info(f"✅ [内盘] 告警已发送: {symbol} ${usd_value:.2f}")
            
        except Exception as e:
            logger.error(f"❌ 发送内盘告警失败: {e}")
    
    
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
                
                # 计算运行时长
                running_seconds = int(time.time() - self.start_time)
                running_hours = running_seconds // 3600
                running_minutes = (running_seconds % 3600) // 60
                running_secs = running_seconds % 60
                uptime_str = f"{running_hours}时{running_minutes}分{running_secs}秒" if running_hours > 0 else f"{running_minutes}分{running_secs}秒"
                
                logger.info("=" * 80)
                logger.info("💓 WebSocket 健康检查")
                logger.info(f"   状态: {'🟢 运行中' if self.ws and not self.should_stop else '🔴 已停止'}")
                logger.info(f"   运行时长: {uptime_str}")
                logger.info(f"   重连次数: {self.reconnect_count}")
                logger.info(f"   回补次数: {self.backfill_count} (冷却期: {self.backfill_cooldown}s)")
                logger.info(f"   消息总数: {self.message_count}")
                logger.info(f"   去重缓存: {len(self.seen_txs)} / {self.max_seen_txs} ({len(self.seen_txs) / self.max_seen_txs * 100:.1f}%)")
                
                # 回执缓存详细统计
                total_cache_requests = self.receipt_cache_hits + self.receipt_cache_misses
                hit_rate = (self.receipt_cache_hits / total_cache_requests * 100) if total_cache_requests > 0 else 0
                avg_wait_time = (self.receipt_cache_wait_time_total / self.receipt_cache_concurrent_waits) if self.receipt_cache_concurrent_waits > 0 else 0
                
                logger.info(f"   回执缓存: {len(self.receipt_cache)} 条")
                logger.info(f"      ├─ 命中: {self.receipt_cache_hits} 次 ({hit_rate:.1f}% 命中率)")
                logger.info(f"      ├─ 未命中: {self.receipt_cache_misses} 次")
                logger.info(f"      ├─ 并发等待: {self.receipt_cache_concurrent_waits} 次（节省RPC）")
                
                # 等待耗时统计
                if self.receipt_cache_concurrent_waits > 0:
                    logger.info(f"      │  ├─ 平均耗时: {avg_wait_time:.2f}s/次")
                    logger.info(f"      │  ├─ 累计耗时: {self.receipt_cache_wait_time_total:.1f}s")
                    if self.receipt_cache_wait_timeouts > 0:
                        timeout_rate = (self.receipt_cache_wait_timeouts / self.receipt_cache_concurrent_waits * 100)
                        logger.info(f"      │  └─ ⚠️ 超时: {self.receipt_cache_wait_timeouts} 次 ({timeout_rate:.1f}%)")
                    else:
                        logger.info(f"      │  └─ ✅ 无超时")
                
                logger.info(f"      └─ 失败缓存命中: {self.receipt_cache_failed_hits} 次（避免重试）")
                
                logger.info(f"   eth_call缓存: {len(self.eth_call_cache)} 条 (命中 {self.eth_call_cache_hits} 次, 节省RPC)")
                logger.info(f"   非fourmeme缓存: {self.cache_hit_count} 次（节省API调用）")
                
                # 第一层/第二层统计
                total_first_layer = self.first_layer_pass_internal + self.first_layer_pass_external
                total_second_check = self.second_layer_check_internal + self.second_layer_check_external
                total_second_pass = self.second_layer_pass_internal + self.second_layer_pass_external
                
                logger.info(f"   第一层过滤: 通过 {total_first_layer} 个")
                if total_first_layer > 0:
                    internal_pct = (self.first_layer_pass_internal / total_first_layer * 100)
                    external_pct = (self.first_layer_pass_external / total_first_layer * 100)
                    logger.info(f"      ├─ 🔴 内盘: {self.first_layer_pass_internal} ({internal_pct:.1f}%)")
                    logger.info(f"      └─ 🟢 外盘: {self.first_layer_pass_external} ({external_pct:.1f}%)")
                
                logger.info(f"   第二层检查: {total_second_check} 个")
                if total_second_check > 0:
                    internal_check_pct = (self.second_layer_check_internal / total_second_check * 100) if total_second_check > 0 else 0
                    external_check_pct = (self.second_layer_check_external / total_second_check * 100) if total_second_check > 0 else 0
                    logger.info(f"      ├─ 🔴 内盘: {self.second_layer_check_internal} ({internal_check_pct:.1f}%)")
                    logger.info(f"      └─ 🟢 外盘: {self.second_layer_check_external} ({external_check_pct:.1f}%)")
                    
                    pass_rate = (total_second_pass / total_second_check * 100)
                    fail_count = total_second_check - total_second_pass
                    fail_rate = 100 - pass_rate
                    logger.info(f"      ├─ ✅ 通过: {total_second_pass} ({pass_rate:.1f}%)")
                    logger.info(f"      │  ├─ 🔴 内盘: {self.second_layer_pass_internal}")
                    logger.info(f"      │  └─ 🟢 外盘: {self.second_layer_pass_external}")
                    logger.info(f"      └─ ❌ 未通过: {fail_count} ({fail_rate:.1f}%)")
                
                # 告警发送统计
                total_alerts = self.alert_success_count + self.alert_fail_count
                if total_alerts > 0:
                    success_rate = (self.alert_success_count / total_alerts * 100)
                    logger.info(f"   告警发送: {total_alerts} 次")
                    logger.info(f"      ├─ ✅ 成功: {self.alert_success_count} ({success_rate:.1f}%)")
                    logger.info(f"      └─ ❌ 失败: {self.alert_fail_count} ({100-success_rate:.1f}%)")
                
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
            
            # Prometheus: 消息计数
            if HAS_PROMETHEUS:
                self.metrics_messages.inc()
            
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
            
            # ========== 直接处理模式（禁用队列，线程池直接处理）==========
            
            # 1️⃣ Fourmeme Proxy 的所有事件（内盘交易）
            if addr == self.FOURMEME_PROXY[0].lower():
                # 直接用线程池处理（无缓冲，低延迟）
                self.executor.submit(self._run_async_in_thread, self.handle_proxy_event, result)
                return
            
            # 2️⃣ Swap 事件（外盘：PancakeSwap V2）
            elif topic0 == self.TOPIC_V2_SWAP:
                # 直接用线程池处理（无缓冲，低延迟）
                self.executor.submit(self._run_async_in_thread, self.handle_swap_event, result)
                return
            
            # 其他事件：忽略
            else:
                return
        
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
        
        # ========== 优化后的订阅策略 ==========
        
        # 1️⃣ 订阅 Fourmeme Proxy 的所有事件（捕获内盘交易）
        # 注意：Transfer事件是Token合约发出的，不是Proxy发出的
        # 所以需要订阅Proxy的所有事件，然后在handle_proxy_event中过滤
        ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": ["logs", {
                "address": [self.FOURMEME_PROXY[0]]  # 只订阅主Proxy（TryBuy已废弃）
                # 不限制topics - 捕获所有事件（TokenPurchase/TokenSale等）
            }]
            }))
        logger.info(f"✓ 订阅 Fourmeme Proxy 所有事件（内盘）")
        logger.info(f"  监听地址: {self.FOURMEME_PROXY[0][:10]}...")
        logger.info(f"  捕获: TokenPurchase/TokenSale/Custom Events")
        
        # 2️⃣ 订阅 PancakeSwap V2 Swap 事件（外盘交易）
        ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "eth_subscribe",
            "params": ["logs", {"topics": [self.TOPIC_V2_SWAP]}]
        }))
        logger.info(f"✓ 订阅 PancakeV2 Swap 事件（外盘）")
        
        logger.info("✅ 订阅完成")
        logger.info(f"   内盘: Proxy所有事件 (TokenPurchase/Sale等) → {self.FOURMEME_PROXY[0][:10]}...")
        logger.info(f"   外盘: 全链Swap事件 → PancakeSwap V2")
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
        
        # ========== 直接处理模式 ==========
        logger.info("🚀 使用直接处理模式（无队列缓冲，线程池直接处理）")
        logger.info(f"✅ 线程池: {self.executor._max_workers} 个工作线程")
        logger.info(f"   架构: WebSocket → 线程池({self.executor._max_workers}线程) → 异步处理")
        logger.info(f"   特点: 低延迟、高并发、无缓冲积压")
        
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
                    
                    # OPTIMIZED: 减少心跳间隔，提高WS稳定性（NodeReal延迟高）
                    self.ws.run_forever(
                        ping_interval=10,    # 每10秒发送ping（降低重连风险）
                        ping_timeout=5,      # ping超时5秒（快速检测断线）
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
            logger.info("🛑 正在关闭监控...")
            self.should_stop = True
            
            # 关闭WebSocket
            if self.ws:
                try:
                    self.ws.close()
                    logger.info("✅ WebSocket 已关闭")
                except Exception as e:
                    logger.debug(f"关闭 WebSocket 异常: {e}")
            
            # 关闭 HTTP Session
            if hasattr(self, 'session'):
                try:
                    self.session.close()
                    logger.info("✅ HTTP Session 已关闭")
                except Exception as e:
                    logger.debug(f"关闭 Session 异常: {e}")
            
            # 关闭线程池（等待所有任务完成，最多30秒）
            if hasattr(self, 'executor'):
                logger.info("🛑 等待线程池任务完成（最多30秒）...")
                self.executor.shutdown(wait=True)
                logger.info("✅ 线程池已关闭")
            
            logger.info("✅ 监控已完全关闭")

