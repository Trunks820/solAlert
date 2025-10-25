"""
BSC 区块监控收集器
监控 PancakeSwap V2 的 Swap 事件，实时捕获链上交易
"""
import time
import random
import logging
from typing import Dict, List, Tuple, Optional, Deque
from collections import deque, defaultdict
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import requests

logger = logging.getLogger(__name__)


class BSCBlockCollector:
    """BSC 区块链交易监控收集器"""
    
    def __init__(self, config: Dict):
        """
        初始化 BSC 监控器
        
        Args:
            config: 配置字典，包含 rpc_endpoints, confirmations, poll_interval 等
        """
        
        # RPC 配置
        self.rpc_endpoints = config.get('rpc_endpoints', [
            "https://bsc-dataseed.binance.org",
            "https://bsc-dataseed1.binance.org",
            "https://bsc-dataseed2.binance.org",
            "https://bsc-dataseed3.binance.org",
            "https://bsc-dataseed4.binance.org",
            "https://bsc-dataseed1.defibit.io",
            "https://bsc-dataseed1.ninicoin.io",
            "https://bsc.publicnode.com",
        ])
        self.confirmations = config.get('confirmations', 12)
        self.poll_interval = config.get('poll_interval', 0.8)
        
        # PancakeSwap V2 常量
        self.pancake_v2_factory = config.get('pancake_v2_factory', '0xca143ce32fe78f1f7019d7d551a6402fc5350c73').lower()
        self.topic_pair_created = config.get('topic_pair_created', '0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9')
        self.topic_swap = config.get('topic_swap', '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822')
        
        # 代币地址
        self.usdt_address = config.get('usdt_address', '0x55d398326f99059ff775485246999027b3197955').lower()
        self.wbnb_address = config.get('wbnb_address', '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c').lower()
        self.usdt_wbnb_pair = config.get('usdt_wbnb_pair', '0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae').lower()
        
        # 链上方法选择器
        self.SEL_TOKEN0 = "0x0dfe1681"
        self.SEL_TOKEN1 = "0xd21220a7"
        self.SEL_DECIMALS = "0x313ce567"
        self.SEL_GETRESERVES = "0x0902f1ac"
        self.SEL_SYMBOL = "0x95d89b41"
        self.SEL_NAME = "0x06fdde03"
        
        # 事件循环引用（用于线程间通信）
        self.event_loop = None
        
        # 停止标记（用于优雅退出）
        self._stop_flag = False
        
        # 缓存
        self.tokens01_cache: Dict[str, Tuple[str, str]] = {}  # pair -> (token0, token1)
        self.decimals_cache: Dict[str, int] = {}  # token -> decimals
        self.symbol_cache: Dict[str, Tuple[str, str]] = {}  # token -> (symbol, name)
        self.reserve_snapshot: Dict[str, Tuple[int, int, int]] = {}  # pair -> (ts, r0, r1)
        
        # 🔥 WBNB/USDT 价格缓存（避免频繁 RPC 调用）
        self.wbnb_usdt_price_cache: Optional[Tuple[int, float]] = None  # (timestamp, price)
        self.wbnb_usdt_price_ttl = 30  # 缓存 30 秒
        
        # 时间窗口数据
        self.reserve_fresh_seconds = config.get('reserve_fresh_seconds', 15)
        
        # HTTP 会话
        self.session = self._make_session()
        
        # 最后处理的区块
        self.last_block: Optional[int] = None
        
        logger.info(f"✅ BSC 区块监控器初始化完成，确认数: {self.confirmations}")
    
    def _make_session(self) -> requests.Session:
        """创建 HTTP 会话，带重试机制（完全按照 block_watcher_full.py）"""
        s = requests.Session()
        
        # 禁用 SSL 证书验证（解决本地环境证书问题）
        s.verify = False
        
        # 禁用 SSL 警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # 兼容新旧版本 urllib3
        try:
            retry = Retry(
                total=4, connect=3, read=3, backoff_factor=0.3,
                status_forcelist=[408, 429, 500, 502, 503, 504],
                allowed_methods=["POST", "GET"]  # 新版本 (urllib3 >= 1.26)
            )
        except TypeError:
            # 旧版本使用 method_whitelist
            retry = Retry(
                total=4, connect=3, read=3, backoff_factor=0.3,
                status_forcelist=[408, 429, 500, 502, 503, 504],
                method_whitelist=["POST", "GET"]  # 旧版本
            )
        
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=64))
        s.headers.update({"Content-Type": "application/json"})
        return s
    
    def rpc_call(self, method: str, params: List = None, timeout: int = 8) -> any:
        """
        执行 JSON-RPC 调用（完全按照用户提供的 block_watcher_full.py 逻辑）
        """
        body = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
        last_err = None
        
        # 对同一方法重试更"狠"一点
        max_attempts = len(self.rpc_endpoints) * 2
        for attempt in range(max_attempts):
            url = random.choice(self.rpc_endpoints)
            try:
                r = self.session.post(url, json=body, timeout=timeout)
                r.raise_for_status()
                out = r.json()
                if "error" in out:
                    last_err = out["error"]
                    time.sleep(0.02)  # 减少等待时间
                    continue
                return out["result"]
            except Exception as e:
                last_err = e
                time.sleep(0.05)  # 减少等待时间
        
        raise RuntimeError(f"RPC 调用失败: {method}, 错误: {last_err}")
    
    def hex_to_int(self, hex_str: str) -> int:
        """十六进制转整数"""
        return int(hex_str, 16)
    
    def int_to_hex(self, num: int) -> str:
        """整数转十六进制"""
        return hex(num)
    
    def get_latest_safe_block(self) -> int:
        """获取最新安全区块号（扣除确认数）"""
        try:
            latest_hex = self.rpc_call("eth_blockNumber")
            latest = self.hex_to_int(latest_hex)
            safe = latest - self.confirmations
            return safe
        except Exception as e:
            logger.error(f"❌ 获取安全区块失败: {e}")
            raise
    
    def get_block(self, block_number: int) -> Dict:
        """获取区块数据"""
        return self.rpc_call("eth_getBlockByNumber", [self.int_to_hex(block_number), False])
    
    def get_transaction_receipt(self, tx_hash: str) -> Dict:
        """获取交易回执"""
        return self.rpc_call("eth_getTransactionReceipt", [tx_hash])
    
    def eth_call(self, to: str, data: str) -> str:
        """执行 eth_call"""
        return self.rpc_call("eth_call", [{"to": to, "data": data}, "latest"])
    
    def parse_address_from_topic(self, topic_hex: str) -> str:
        """从 topic 中解析地址"""
        return "0x" + topic_hex[-40:]
    
    def get_token0_token1(self, pair_address: str) -> Tuple[str, str]:
        """
        获取 Pair 的 token0 和 token1
        
        Args:
            pair_address: Pair 合约地址
        
        Returns:
            (token0, token1) 元组
        """
        pair = pair_address.lower()
        if pair in self.tokens01_cache:
            return self.tokens01_cache[pair]
        
        token0 = "0x" + self.eth_call(pair, self.SEL_TOKEN0)[-40:]
        token1 = "0x" + self.eth_call(pair, self.SEL_TOKEN1)[-40:]
        token0, token1 = token0.lower(), token1.lower()
        
        self.tokens01_cache[pair] = (token0, token1)
        return token0, token1
    
    def get_decimals(self, token_address: str) -> int:
        """
        获取代币精度
        
        Args:
            token_address: 代币地址
        
        Returns:
            decimals
        """
        token = token_address.lower()
        if token in self.decimals_cache:
            return self.decimals_cache[token]
        
        try:
            decimals = int(self.eth_call(token, self.SEL_DECIMALS), 16)
        except Exception:
            decimals = 18  # 默认精度
        
        self.decimals_cache[token] = decimals
        return decimals
    
    def get_reserves(self, pair_address: str) -> Tuple[int, int]:
        """
        获取 Pair 储备量
        
        Args:
            pair_address: Pair 地址
        
        Returns:
            (reserve0, reserve1) 元组
        """
        pair = pair_address.lower()
        now_ts = int(time.time())
        
        # 检查缓存
        if pair in self.reserve_snapshot:
            ts, r0, r1 = self.reserve_snapshot[pair]
            if now_ts - ts < self.reserve_fresh_seconds:
                return r0, r1
        
        # 从链上获取
        result = self.eth_call(pair, self.SEL_GETRESERVES)[2:]
        reserve0 = int(result[0:64], 16)
        reserve1 = int(result[64:128], 16)
        
        self.reserve_snapshot[pair] = (now_ts, reserve0, reserve1)
        return reserve0, reserve1
    
    def decode_abi_string(self, hex_data: str) -> str:
        """解码 ABI 字符串"""
        if not hex_data or hex_data == "0x":
            return "UNK"
        
        raw = bytes.fromhex(hex_data[2:])
        
        # 尝试标准 ABI 解码
        if len(raw) >= 64:
            try:
                offset = int.from_bytes(raw[0:32], "big")
                if offset == 32 and len(raw) >= 64:
                    str_len = int.from_bytes(raw[32:64], "big")
                    str_bytes = raw[64:64 + str_len]
                    s = str_bytes.decode("utf-8", errors="ignore").strip("\x00")
                    if s:
                        return s
            except Exception:
                pass
        
        # 尝试 bytes32 解码
        try:
            s = raw[:32].rstrip(b"\x00").decode("utf-8", errors="ignore")
            return s if s else "UNK"
        except Exception:
            return "UNK"
    
    def get_token_symbol(self, token_address: str) -> str:
        """
        获取代币符号
        
        Args:
            token_address: 代币地址
        
        Returns:
            symbol 字符串
        """
        token = token_address.lower()
        if token in self.symbol_cache:
            return self.symbol_cache[token][0]
        
        # 特殊代币
        if token == self.usdt_address:
            self.symbol_cache[token] = ("USDT", "Tether USD")
            return "USDT"
        if token == self.wbnb_address:
            self.symbol_cache[token] = ("WBNB", "Wrapped BNB")
            return "WBNB"
        
        symbol = "UNK"
        # 尝试多次获取
        for _ in range(3):
            try:
                result = self.eth_call(token, self.SEL_SYMBOL)
                if result and result not in ("0x", "0x0"):
                    s = self.decode_abi_string(result)
                    if s and s.strip() and s.upper() != "UNK":
                        symbol = s.strip()
                        break
            except Exception:
                time.sleep(0.05)
        
        # 获取 name
        name = "Unknown"
        for _ in range(3):
            try:
                result = self.eth_call(token, self.SEL_NAME)
                if result and result not in ("0x", "0x0"):
                    n = self.decode_abi_string(result)
                    if n and n.strip():
                        name = n.strip()
                        break
            except Exception:
                time.sleep(0.05)
        
        self.symbol_cache[token] = (symbol, name)
        return symbol
    
    def get_token_name(self, token_address: str) -> str:
        """获取代币名称"""
        token = token_address.lower()
        if token in self.symbol_cache:
            return self.symbol_cache[token][1]
        
        # 先调用 get_token_symbol 填充缓存
        self.get_token_symbol(token)
        return self.symbol_cache.get(token, ("UNK", "Unknown"))[1]
    
    def get_wbnb_usdt_price(self) -> float:
        """
        获取 WBNB/USDT 价格（从参考池，带缓存避免频繁 RPC 调用）
        
        Returns:
            1 WBNB = ??? USDT
        """
        now = int(time.time())
        
        # 🔥 检查缓存是否有效
        if self.wbnb_usdt_price_cache:
            cache_ts, cached_price = self.wbnb_usdt_price_cache
            if now - cache_ts < self.wbnb_usdt_price_ttl:
                return cached_price
        
        # 缓存失效或不存在，从链上获取
        try:
            token0, token1 = self.get_token0_token1(self.usdt_wbnb_pair)
            reserve0, reserve1 = self.get_reserves(self.usdt_wbnb_pair)
            
            if reserve0 == 0 or reserve1 == 0:
                return 0.0
            
            decimals0 = self.get_decimals(token0)
            decimals1 = self.get_decimals(token1)
            
            qty0 = reserve0 / (10 ** decimals0)
            qty1 = reserve1 / (10 ** decimals1)
            
            # 判断哪个是 USDT，哪个是 WBNB
            price = 0.0
            if token0 == self.usdt_address and token1 == self.wbnb_address:
                price = qty0 / qty1 if qty1 > 0 else 0.0
            elif token0 == self.wbnb_address and token1 == self.usdt_address:
                price = qty1 / qty0 if qty0 > 0 else 0.0
            
            # 🔥 更新缓存
            if price > 0:
                self.wbnb_usdt_price_cache = (now, price)
                logger.debug(f"💰 WBNB 价格缓存已更新: ${price:.2f}")
            
            return price
        except Exception as e:
            logger.error(f"获取 WBNB/USDT 价格失败: {e}")
            # 如果有旧缓存，即使过期也返回（降级策略）
            if self.wbnb_usdt_price_cache:
                _, old_price = self.wbnb_usdt_price_cache
                logger.warning(f"⚠️ 使用过期缓存价格: ${old_price:.2f}")
                return old_price
            return 0.0
    
    def quote_to_usdt(self, quote_token: str, amount: float) -> float:
        """
        将报价币金额转换为 USDT
        
        Args:
            quote_token: 报价币地址
            amount: 金额
        
        Returns:
            USDT 价值
        """
        qt = quote_token.lower()
        
        if qt == self.usdt_address:
            return amount
        
        if qt == self.wbnb_address:
            rate = self.get_wbnb_usdt_price()
            return amount * rate if rate > 0 else 0.0
        
        return 0.0
    
    def choose_quote_side(self, token0: str, token1: str) -> Tuple[Optional[bool], Optional[str]]:
        """
        选择报价币（USDT 或 WBNB）
        
        Args:
            token0: token0 地址
            token1: token1 地址
        
        Returns:
            (quote_is_token1, quote_token) 元组
            如果都不是 USDT/WBNB，返回 (None, None)
        """
        if token1 in (self.usdt_address, self.wbnb_address):
            return True, token1
        if token0 in (self.usdt_address, self.wbnb_address):
            return False, token0
        return None, None
    
    def decode_swap_event(self, log: Dict) -> Tuple[int, int, int, int]:
        """
        解码 Swap 事件
        
        Args:
            log: 事件日志
        
        Returns:
            (amount0In, amount1In, amount0Out, amount1Out) 元组
        """
        data = log["data"][2:]
        amount0_in = int(data[0:64], 16)
        amount1_in = int(data[64:128], 16)
        amount0_out = int(data[128:192], 16)
        amount1_out = int(data[192:256], 16)
        return amount0_in, amount1_in, amount0_out, amount1_out
    
    def calculate_swap_usdt_value(
        self,
        pair_address: str,
        amount0_in: int,
        amount1_in: int,
        amount0_out: int,
        amount1_out: int
    ) -> Dict:
        """
        计算 Swap 交易的 USDT 价值
        
        Args:
            pair_address: Pair 地址
            amount0_in, amount1_in, amount0_out, amount1_out: Swap 数量
        
        Returns:
            {
                'usdt_value': float,  # USDT 价值
                'base_token': str,    # 基础代币地址
                'quote_token': str,   # 报价代币地址
                'is_valid': bool      # 是否是有效的 USDT/WBNB 交易对
            }
        """
        token0, token1 = self.get_token0_token1(pair_address)
        quote_is_token1, quote_token = self.choose_quote_side(token0, token1)
        
        # 不是 USDT/WBNB 对
        if quote_is_token1 is None:
            return {
                'usdt_value': 0.0,
                'base_token': token0,
                'quote_token': token1,
                'is_valid': False,
                'quote_is_token1': None
            }
        
        decimals0 = self.get_decimals(token0)
        decimals1 = self.get_decimals(token1)
        
        qty0_in = amount0_in / (10 ** decimals0)
        qty1_in = amount1_in / (10 ** decimals1)
        qty0_out = amount0_out / (10 ** decimals0)
        qty1_out = amount1_out / (10 ** decimals1)
        
        base_token = token0 if quote_is_token1 else token1
        
        # 计算 USDT 价值
        if quote_is_token1:
            # quote = token1
            if amount1_in > 0:  # 买入 token0
                usdt_value = self.quote_to_usdt(quote_token, qty1_in)
            elif amount1_out > 0:  # 卖出 token0
                usdt_value = self.quote_to_usdt(quote_token, qty1_out)
            else:
                usdt_value = self.quote_to_usdt(quote_token, qty1_in + qty1_out)
        else:
            # quote = token0
            if amount0_in > 0:  # 买入 token1
                usdt_value = self.quote_to_usdt(quote_token, qty0_in)
            elif amount0_out > 0:  # 卖出 token1
                usdt_value = self.quote_to_usdt(quote_token, qty0_out)
            else:
                usdt_value = self.quote_to_usdt(quote_token, qty0_in + qty0_out)
        
        return {
            'usdt_value': usdt_value,
            'base_token': base_token,
            'quote_token': quote_token,
            'is_valid': True,
            'quote_is_token1': quote_is_token1  # 添加这个标记，用于判断交易方向
        }
    
    def process_block(self, block_number: int) -> List[Dict]:
        """
        处理单个区块，提取 Swap 事件
        
        Args:
            block_number: 区块号
        
        Returns:
            交易事件列表，每个元素包含:
            {
                'block_number': int,
                'tx_hash': str,
                'pair_address': str,
                'base_token': str,
                'quote_token': str,
                'usdt_value': float,
                'timestamp': int
            }
        """
        try:
            block = self.get_block(block_number)
            transactions = block.get("transactions", [])
            total_txs = len(transactions)
            
            logger.info(f"📦 处理区块 {block_number}, 交易数: {total_txs}")
            
            if not transactions:
                return []
            
            events = []
            block_timestamp = self.hex_to_int(block.get("timestamp", "0x0"))
            
            # 进度统计
            swap_count = 0
            buy_count = 0
            sell_count = 0
            
            logger.info(f"   🔄 开始处理 {total_txs} 笔交易...")
            
            for idx, tx_hash in enumerate(transactions, 1):
                try:
                    # 每处理 50 笔交易打印一次进度
                    if idx % 50 == 0:
                        logger.info(f"   ⏳ 进度: {idx}/{total_txs} | Swap={swap_count}, 买入={buy_count}, 卖出={sell_count}")
                    
                    receipt = self.get_transaction_receipt(tx_hash)
                    logs = receipt.get("logs") if receipt else None
                    if not logs:
                        continue
                    
                    for log in logs:
                        topics = log.get("topics") or []
                        if not topics:
                            continue
                        
                        topic0 = topics[0].lower()
                        
                        # 处理 Swap 事件
                        if topic0 == self.topic_swap:
                            swap_count += 1
                            pair_address = log["address"].lower()
                            amount0_in, amount1_in, amount0_out, amount1_out = self.decode_swap_event(log)
                            
                            swap_data = self.calculate_swap_usdt_value(
                                pair_address,
                                amount0_in,
                                amount1_in,
                                amount0_out,
                                amount1_out
                            )
                            
                            # 判断交易方向（只要买入，不要卖出）
                            is_buy = False
                            if swap_data['is_valid'] and swap_data['usdt_value'] > 0:
                                # 判断是买入还是卖出
                                # 买入：用 WBNB/USDT 买入基础代币
                                quote_is_token1 = swap_data.get('quote_is_token1')
                                if quote_is_token1:
                                    # quote = token1 (WBNB/USDT)
                                    # amount1In > 0 表示买入 token0
                                    is_buy = amount1_in > 0
                                else:
                                    # quote = token0 (WBNB/USDT)
                                    # amount0In > 0 表示买入 token1
                                    is_buy = amount0_in > 0
                                
                                # 只记录买入交易
                                if is_buy:
                                    buy_count += 1
                                    events.append({
                                        'block_number': block_number,
                                        'tx_hash': tx_hash,
                                        'pair_address': pair_address,
                                        'base_token': swap_data['base_token'],
                                        'quote_token': swap_data['quote_token'],
                                        'usdt_value': swap_data['usdt_value'],
                                        'timestamp': block_timestamp,
                                        'is_buy': True
                                    })
                                else:
                                    sell_count += 1
                
                except Exception as e:
                    logger.debug(f"处理交易 {tx_hash[:10]}... 失败: {e}")
                    continue
            
            # 最终统计
            if events:
                logger.info(f"   ✅ 区块 {block_number} 统计: Swap={swap_count}, 买入={buy_count}, 卖出={sell_count}")
            else:
                logger.info(f"   ⚪ 区块 {block_number} 统计: Swap={swap_count}, 买入={buy_count}, 卖出={sell_count} (无买入)")
            
            return events
        
        except Exception as e:
            logger.error(f"处理区块 {block_number} 失败: {e}")
            return []
    
    def collect(self):
        """
        主监控循环（实现 BaseCollector 抽象方法）
        注意：此方法是同步的，应该在独立线程中运行（通过 asyncio.to_thread）
        """
        logger.info("🚀 BSC 区块监控已启动...")
        
        try:
            # 初始化最后处理的区块
            if self.last_block is None:
                logger.info("📊 正在获取最新安全区块...")
                self.last_block = self.get_latest_safe_block()
                logger.info(f"✅ 从区块 {self.last_block} 开始监控")
            
            while not self._stop_flag:
                try:
                    safe_block = self.get_latest_safe_block()
                    
                    # 处理新区块
                    while self.last_block <= safe_block:
                        events = self.process_block(self.last_block)
                        
                        # 如果有交易事件，调用回调处理
                        if events:
                            import inspect
                            import asyncio
                            
                            if inspect.iscoroutinefunction(self.on_data_received):
                                # 协程回调：需要提交到主事件循环
                                if self.event_loop:
                                    try:
                                        # 提交协程到事件循环，保存 future 以便捕获回调异常
                                        future = asyncio.run_coroutine_threadsafe(
                                            self.on_data_received(events), self.event_loop
                                        )
                                        # 添加异常回调，记录回调执行中的错误
                                        def log_callback_exception(fut):
                                            try:
                                                fut.result()  # 触发异常（如果有）
                                            except Exception as e:
                                                logger.error(f"❌ 回调执行异常: {e}")
                                                import traceback
                                                logger.error(traceback.format_exc())
                                        future.add_done_callback(log_callback_exception)
                                    except Exception as e:
                                        logger.error(f"❌ 提交回调任务失败: {e}")
                                        import traceback
                                        logger.error(traceback.format_exc())
                                else:
                                    logger.error("❌ 事件循环未设置，无法调用协程回调")
                            else:
                                # 同步回调，直接调用
                                self.on_data_received(events)
                        
                        self.last_block += 1
                    
                    # 显示进度（每处理完一批后）
                    if self.last_block > safe_block:
                        logger.info(f"✅ 已同步到区块 {safe_block}, 等待新区块...")
                    
                    # 等待下一次轮询
                    time.sleep(self.poll_interval)
                
                except Exception as e:
                    logger.error(f"监控循环错误: {e}")
                    time.sleep(5)
        
        except KeyboardInterrupt:
            logger.info("⏹️  BSC 区块监控已停止")
        finally:
            logger.info("🛑 BSC 区块监控线程退出")
    
    def stop(self):
        """停止监控（设置停止标记）"""
        logger.info("🛑 收到停止信号，设置停止标记...")
        self._stop_flag = True
    
    def on_data_received(self, events: List[Dict]):
        """
        数据接收回调（子类可重写）
        
        Args:
            events: 交易事件列表
        """
        # 默认实现：打印日志
        for event in events:
            logger.info(
                f"[区块 {event['block_number']}] "
                f"{event['base_token'][:10]}... "
                f"交易 {event['usdt_value']:.2f} USDT"
            )

