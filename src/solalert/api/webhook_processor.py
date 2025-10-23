"""
Alchemy Webhook 数据处理器
将 Alchemy 推送的数据转换为 BSC Monitor 可处理的格式
"""
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class AlchemyWebhookProcessor:
    """Alchemy Webhook 数据处理器"""
    
    # PancakeSwap V2/V3 常量
    TOPIC_V2_SWAP = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'
    TOPIC_V3_SWAP = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
    
    # 代币地址
    USDT_ADDRESS = '0x55d398326f99059ff775485246999027b3197955'.lower()
    WBNB_ADDRESS = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'.lower()
    USDT_WBNB_PAIR = '0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae'.lower()
    
    def __init__(self, bsc_collector):
        """
        初始化处理器
        
        Args:
            bsc_collector: BSC 收集器实例（用于调用链上方法）
        """
        self.bsc_collector = bsc_collector
        
        # 缓存（避免重复调用链上方法）
        self.pair_cache = {}  # pair_address -> (token0, token1, is_target_pair)
        self.wbnb_usdt_price = 600.0  # 默认WBNB价格，会从链上更新
        
        logger.info("✅ Alchemy Webhook 处理器初始化完成")
    
    def update_wbnb_price(self):
        """更新 WBNB/USDT 价格"""
        try:
            price = self.bsc_collector.get_wbnb_usdt_price()
            if price > 0:
                self.wbnb_usdt_price = price
                logger.debug(f"📊 WBNB 价格更新: ${price:.2f}")
        except Exception as e:
            logger.warning(f"⚠️  获取 WBNB 价格失败: {e}")
    
    def is_target_pair(self, token0: str, token1: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        判断是否是目标交易对（USDT/WBNB 对某个代币）
        
        Args:
            token0: token0 地址
            token1: token1 地址
        
        Returns:
            (is_target, base_token, quote_token)
        """
        token0 = token0.lower()
        token1 = token1.lower()
        
        # 情况1: token1 是 USDT/WBNB, token0 是其他代币
        if token1 in (self.USDT_ADDRESS, self.WBNB_ADDRESS) and token0 not in (self.USDT_ADDRESS, self.WBNB_ADDRESS):
            return True, token0, token1
        
        # 情况2: token0 是 USDT/WBNB, token1 是其他代币
        if token0 in (self.USDT_ADDRESS, self.WBNB_ADDRESS) and token1 not in (self.USDT_ADDRESS, self.WBNB_ADDRESS):
            return True, token1, token0
        
        # 排除: USDT-WBNB 参考对
        if {token0, token1} == {self.USDT_ADDRESS, self.WBNB_ADDRESS}:
            return False, None, None
        
        # 其他情况（小币种对小币种）
        return False, None, None
    
    def get_pair_info(self, pair_address: str) -> Optional[Tuple[str, str, bool, Optional[str], Optional[str]]]:
        """
        获取交易对信息（带缓存）
        
        Args:
            pair_address: 交易对地址
        
        Returns:
            (token0, token1, is_target, base_token, quote_token) 或 None
        """
        pair_address = pair_address.lower()
        
        # 检查缓存
        if pair_address in self.pair_cache:
            return self.pair_cache[pair_address]
        
        try:
            # 调用链上方法获取 token0 和 token1
            token0, token1 = self.bsc_collector.get_token0_token1(pair_address)
            token0, token1 = token0.lower(), token1.lower()
            
            # 判断是否是目标交易对
            is_target, base_token, quote_token = self.is_target_pair(token0, token1)
            
            # 缓存结果
            result = (token0, token1, is_target, base_token, quote_token)
            self.pair_cache[pair_address] = result
            
            return result
        
        except Exception as e:
            # 降为 debug 级别，避免刷屏
            logger.debug(f"获取交易对信息失败 {pair_address[:10]}...: {e}")
            return None
    
    def calculate_usdt_value(
        self,
        quote_token: str,
        amount0_in: int,
        amount1_in: int,
        amount0_out: int,
        amount1_out: int,
        quote_is_token1: bool
    ) -> Tuple[float, bool]:
        """
        计算交易的 USDT 价值和方向
        
        Args:
            quote_token: 报价代币地址
            amount0_in, amount1_in, amount0_out, amount1_out: Swap 数量（Wei）
            quote_is_token1: 报价代币是否是 token1
        
        Returns:
            (usdt_value, is_buy)
        """
        quote_token = quote_token.lower()
        
        # 获取报价代币精度
        try:
            decimals = self.bsc_collector.get_decimals(quote_token)
        except:
            decimals = 18
        
        # 判断交易方向和计算金额
        if quote_is_token1:
            # quote = token1 (USDT/WBNB)
            if amount1_in > 0:  # 买入 token0（用USDT/WBNB买）
                quote_amount = amount1_in / (10 ** decimals)
                is_buy = True
            elif amount1_out > 0:  # 卖出 token0（得到USDT/WBNB）
                quote_amount = amount1_out / (10 ** decimals)
                is_buy = False
            else:
                quote_amount = 0
                is_buy = False
        else:
            # quote = token0 (USDT/WBNB)
            if amount0_in > 0:  # 买入 token1
                quote_amount = amount0_in / (10 ** decimals)
                is_buy = True
            elif amount0_out > 0:  # 卖出 token1
                quote_amount = amount0_out / (10 ** decimals)
                is_buy = False
            else:
                quote_amount = 0
                is_buy = False
        
        # 转换为 USDT 价值
        if quote_token == self.USDT_ADDRESS:
            usdt_value = quote_amount
        elif quote_token == self.WBNB_ADDRESS:
            usdt_value = quote_amount * self.wbnb_usdt_price
        else:
            usdt_value = 0
        
        return usdt_value, is_buy
    
    def process_swap_log(self, log: Dict, block_number: int, timestamp: int) -> Optional[Dict]:
        """
        处理单个 Swap log
        
        Args:
            log: Alchemy 推送的 log 数据
            block_number: 区块号
            timestamp: 时间戳
        
        Returns:
            事件字典或 None
        """
        try:
            topics = log.get('topics', [])
            data_hex = log.get('data', '0x')
            
            # Alchemy GraphQL: account.address 而不是直接的 address
            account = log.get('account', {})
            pair_address = account.get('address', '').lower() if account else ''
            
            tx_info = log.get('transaction', {})
            tx_hash = tx_info.get('hash', '')
            
            # 检查基础数据有效性
            if not topics or len(topics) == 0:
                logger.debug(f"跳过: 无 topics")
                return None
            
            if not pair_address or pair_address == '0x' or len(pair_address) < 42:
                logger.debug(f"跳过: 无效的 pair_address = '{pair_address}'")
                return None
            
            topic0 = topics[0].lower()
            
            # 只处理 V2 Swap（V3 暂时跳过）
            if topic0 != self.TOPIC_V2_SWAP:
                return None
            
            # 获取交易对信息
            pair_info = self.get_pair_info(pair_address)
            if not pair_info:
                logger.debug(f"跳过: 获取交易对信息失败 {pair_address[:10]}...")
                return None
            
            token0, token1, is_target, base_token, quote_token = pair_info
            
            # 不是目标交易对，跳过
            if not is_target:
                logger.debug(f"跳过: 非目标对 {pair_address[:10]}... (token0={token0[:10]}..., token1={token1[:10]}...)")
                return None
            
            logger.debug(f"✓ 目标对: {pair_address[:10]}... base={base_token[:10]}... quote={quote_token[:10]}...")
            
            # 解析 Swap data
            data_clean = data_hex[2:] if data_hex.startswith('0x') else data_hex
            if len(data_clean) < 256:
                return None
            
            amount0_in = int(data_clean[0:64], 16)
            amount1_in = int(data_clean[64:128], 16)
            amount0_out = int(data_clean[128:192], 16)
            amount1_out = int(data_clean[192:256], 16)
            
            # 判断 quote 是 token0 还是 token1
            quote_is_token1 = (quote_token == token1)
            
            # 计算 USDT 价值和交易方向
            usdt_value, is_buy = self.calculate_usdt_value(
                quote_token,
                amount0_in,
                amount1_in,
                amount0_out,
                amount1_out,
                quote_is_token1
            )
            
            logger.debug(f"  计算结果: USDT=${usdt_value:.2f}, is_buy={is_buy}")
            
            # 只保留买入交易
            if not is_buy:
                logger.debug(f"跳过: 卖出交易")
                return None
            
            if usdt_value <= 0:
                logger.debug(f"跳过: USDT价值<=0")
                return None
            
            # 返回标准事件格式
            return {
                'block_number': block_number,
                'tx_hash': tx_hash,
                'pair_address': pair_address,
                'base_token': base_token,
                'quote_token': quote_token,
                'usdt_value': usdt_value,
                'timestamp': timestamp,
                'is_buy': True
            }
        
        except Exception as e:
            logger.debug(f"处理 Swap log 失败: {e}")
            return None
    
    def process_webhook_data(self, webhook_data: Dict) -> List[Dict]:
        """
        处理完整的 webhook 数据
        
        Args:
            webhook_data: Alchemy 推送的完整数据
        
        Returns:
            事件列表
        """
        try:
            # 提取区块信息
            event = webhook_data.get('event', {})
            block_data = event.get('data', {}).get('block', {})
            
            block_number = block_data.get('number', 0)
            timestamp = block_data.get('timestamp', 0)
            logs = block_data.get('logs', [])
            
            if not isinstance(block_number, int):
                block_number = int(block_number, 16) if isinstance(block_number, str) and block_number.startswith('0x') else int(block_number)
            if not isinstance(timestamp, int):
                timestamp = int(timestamp, 16) if isinstance(timestamp, str) and timestamp.startswith('0x') else int(timestamp)
            
            # 更新 WBNB 价格
            self.update_wbnb_price()
            
            # 统计信息
            stats = {
                'total_logs': len(logs),
                'v2_swaps': 0,
                'non_target_pairs': 0,
                'sell_trades': 0,
                'buy_trades': 0,
                'low_value': 0
            }
            
            # 处理所有 logs
            events = []
            for log in logs:
                # 统计 V2 Swap
                topics = log.get('topics', [])
                if topics and topics[0].lower() == self.TOPIC_V2_SWAP:
                    stats['v2_swaps'] += 1
                
                event_result = self.process_swap_log(log, block_number, timestamp)
                if event_result:
                    stats['buy_trades'] += 1
                    events.append(event_result)
            
            # 打印详细统计
            logger.info(f"📊 [Processor] 区块 #{block_number} 处理结果:")
            logger.info(f"   └─ 总 Logs: {stats['total_logs']}")
            logger.info(f"   └─ V2 Swap: {stats['v2_swaps']}")
            logger.info(f"   └─ 目标交易对: {stats['buy_trades']} 个（USDT/WBNB + 买入）")
            
            if stats['buy_trades'] > 0:
                # 显示每个目标交易的详情
                for i, evt in enumerate(events, 1):
                    logger.info(f"   └─ [{i}] {evt['base_token'][:10]}... | ${evt['usdt_value']:.2f} USDT")
            
            return events
        
        except Exception as e:
            logger.error(f"❌ 处理 webhook 数据失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

