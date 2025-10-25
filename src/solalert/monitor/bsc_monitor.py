"""
BSC 监控主逻辑
实现三层过滤机制 + DBotX API 集成：
1. 第一层：交易金额过滤（单笔 >= 400 USDT OR 区块累计 >= 1000 USDT）
2. 第二层：fourmeme 平台验证 + 指标过滤（使用 DBotX API，1分钟实时数据）
3. 第三层：推送频率控制（同一代币 30 秒冷却期）

优势：
- 使用 DBotX API（API Key 认证，比 GMGN Cookie 更稳定）
- 1分钟实时数据（比 GMGN 5分钟数据快5倍）
- 单次 API 调用获取所有信息（launchpad + 价格 + 交易量）
"""
import asyncio
import logging
import time
import json
import random
from typing import Dict, List, Optional
from collections import defaultdict
from datetime import datetime

from ..collectors.bsc_collector import BSCBlockCollector
from ..core.redis_client import get_redis
from ..core.database import get_db
from ..notifiers.alert_recorder import get_alert_recorder
from ..notifiers.manager import get_notification_manager
from ..api.dbotx_api import DBotXAPI
from .trigger_logic import TriggerLogic

logger = logging.getLogger(__name__)


class BSCMonitor:
    """BSC 链上交易监控器"""
    
    @staticmethod
    def format_number(value: float) -> str:
        """
        格式化数字，自动添加 K/M 后缀
        
        Args:
            value: 数值
            
        Returns:
            格式化后的字符串
        """
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f}M"
        elif value >= 1_000:
            return f"{value / 1_000:.2f}K"
        else:
            return f"{value:.0f}"
    
    def __init__(self, config: Dict):
        """
        初始化 BSC 监控器
        
        Args:
            config: 配置字典
        """
        self.config = config
        
        # 全局监控配置（从数据库或 Redis 读取，缓存起来）
        self.global_config = self.load_global_config()
        
        # 加载并缓存内外盘配置
        self.internal_config = self.load_global_config(market_type='internal')
        self.external_config = self.load_global_config(market_type='external')
        
        # 解析内外盘的 events_config
        self.internal_events_config = self.parse_events_config(
            self.internal_config.get('events_config') if self.internal_config else None
        )
        self.external_events_config = self.parse_events_config(
            self.external_config.get('events_config') if self.external_config else None
        )
        
        # 记录配置信息
        if self.internal_config:
            logger.info(f"📊 内盘配置: 单笔>={self.internal_config.get('min_transaction_usd')}U, 累计>={self.internal_config.get('cumulative_min_amount_usd')}U, 涨幅>={self.internal_events_config.get('priceChange', {}).get('risePercent')}%, 交易量>=${self.internal_events_config.get('volume', {}).get('threshold')}")
        if self.external_config:
            logger.info(f"📊 外盘配置: 单笔>={self.external_config.get('min_transaction_usd')}U, 累计>={self.external_config.get('cumulative_min_amount_usd')}U, 涨幅>={self.external_events_config.get('priceChange', {}).get('risePercent')}%, 交易量>=${self.external_events_config.get('volume', {}).get('threshold')}")
        
        # 用于第一层快速过滤的宽松阈值（取最小值）
        internal_min = self.internal_config.get('min_transaction_usd', 200) if self.internal_config else 200
        internal_cumulative = self.internal_config.get('cumulative_min_amount_usd', 500) if self.internal_config else 500
        external_min = self.external_config.get('min_transaction_usd', 400) if self.external_config else 400
        external_cumulative = self.external_config.get('cumulative_min_amount_usd', 1000) if self.external_config else 1000
        
        self.single_max_usdt = min(internal_min, external_min)
        self.block_accumulate_usdt = min(internal_cumulative, external_cumulative)
        
        # 第二层过滤：events_config（从配置解析）
        self.events_config = self.parse_events_config(
            self.global_config.get('events_config') if self.global_config else None
        )
        
        # 第三层控制：推送频率
        self.min_interval_seconds = 180  # 冷却期180秒（避免重复推送）
        self.cooldown_jitter = 30  # 冷却期抖动：30秒内随机
        
        # 通知配置
        self.enable_telegram = config.get('notification', {}).get('enable_telegram', True)
        self.enable_wechat = config.get('notification', {}).get('enable_wechat', True)
        
        # 打印通知配置状态
        logger.info(f"📢 通知配置: Telegram={self.enable_telegram}, WeChat={self.enable_wechat}")
        
        # Redis 客户端（用于冷却期控制）
        self.redis_client = get_redis()
        
        # 数据库
        self.db = get_db()
        
        # 预警记录器
        self.alert_recorder = get_alert_recorder()
        
        # 通知管理器（用于 TG 推送）
        self.notification_manager = get_notification_manager()
        
        # DBotX API（用于获取 BSC 代币数据，比 GMGN 更稳定）
        self.dbotx_api = DBotXAPI()
        
        # 区块收集器
        self.collector = BSCBlockCollector(config)
        self.collector.on_data_received = self.handle_block_events
        
        # 设置事件循环引用（用于线程间通信）
        try:
            self.collector.event_loop = asyncio.get_running_loop()
        except RuntimeError:
            # 如果还没有运行循环，在 start() 时再设置
            pass
        
        # 初始化完成（详细配置已在 start.py 显示）
        logger.debug(f"BSC Monitor 已就绪")
    
    def load_global_config(self, market_type: str = None) -> Optional[Dict]:
        """
        加载全局监控配置（优先从 Redis 读取）
        
        Args:
            market_type: 市场类型 ('internal' 内盘 或 'external' 外盘)
        
        Returns:
            配置字典或 None
        """
        chain_type = 'bsc'
        # 如果指定了 market_type，则使用分类配置
        if market_type:
            redis_key = f"global_monitor:config:{chain_type}:{market_type}"
        else:
            # 兼容旧配置（没有 market_type）
            redis_key = f"global_monitor:config:{chain_type}"
        
        try:
            # 1. 优先从 Redis 读取
            redis_client = get_redis()
            # 直接获取原始字符串（不使用自动解析）
            cached_data = redis_client.client.get(redis_key)
            
            # Redis 返回的可能是字符串，需要解析
            if cached_data:
                import re
                import json
                
                # 如果是字符串，尝试 JSON 解析
                if isinstance(cached_data, str):
                    json_str = cached_data
                else:
                    # bytes 转 str
                    json_str = cached_data.decode('utf-8') if isinstance(cached_data, bytes) else str(cached_data)
                
                # 清理 Java 特有的 JSON 语法
                # 1. 移除 @type 字段
                json_str = re.sub(r'"@type"\s*:\s*"[^"]*"\s*,?\s*', '', json_str)
                # 2. 移除数字后的 L 后缀 (Java Long)
                json_str = re.sub(r':\s*(\d+)L\b', r':\1', json_str)
                # 3. 清理可能的多余逗号
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*]', ']', json_str)
                
                try:
                    cached_config = json.loads(json_str)
                    logger.info(f"✅ 从 Redis 加载全局配置: {cached_config.get('configName')}")
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Redis 配置 JSON 解析失败: {e}")
                    logger.debug(f"清理后的 JSON: {json_str[:500]}")
                    cached_config = None
                
                if not cached_config:
                    logger.warning("⚠️  Redis 配置解析失败，尝试从数据库加载")
                    cached_data = None
                
                # 转换字段名（Redis 用驼峰命名，需要转换为下划线）
                config = {
                    'id': cached_config.get('id'),
                    'config_name': cached_config.get('configName'),
                    'chain_type': cached_config.get('chainType'),
                    'market_type': cached_config.get('marketType'),
                    'source': cached_config.get('source'),
                    'min_transaction_usd': cached_config.get('minTransactionUsd'),
                    'cumulative_min_amount_usd': cached_config.get('cumulativeMinAmountUsd'),
                    'events_config': cached_config.get('eventsConfig'),
                    'trigger_logic': cached_config.get('triggerLogic'),
                    'notify_methods': cached_config.get('notifyMethods'),
                    'status': cached_config.get('status'),
                }
                return config
            
            # 2. Redis 没有，从数据库读取
            logger.debug("Redis 中未找到配置，从数据库加载...")
            db = get_db()
            
            # 构建查询条件
            if market_type:
                sql = """
                SELECT id, config_name, chain_type, market_type, source, min_transaction_usd,
                       cumulative_min_amount_usd, events_config, trigger_logic, notify_methods, status
                FROM global_monitor_config
                WHERE chain_type = %s AND market_type = %s AND status = '1'
                LIMIT 1
                """
                result = db.execute_query(sql, (chain_type, market_type), fetch_one=True)
            else:
                sql = """
                SELECT id, config_name, chain_type, market_type, source, min_transaction_usd,
                       cumulative_min_amount_usd, events_config, trigger_logic, notify_methods, status
                FROM global_monitor_config
                WHERE chain_type = %s AND status = '1'
                LIMIT 1
                """
                result = db.execute_query(sql, (chain_type,), fetch_one=True)
            
            if result:
                logger.info(f"✅ 从数据库加载全局配置: {result.get('config_name')}")
                
                # 缓存到 Redis（5分钟过期）
                try:
                    cache_data = {
                        'id': result.get('id'),
                        'configName': result.get('config_name'),
                        'chainType': result.get('chain_type'),
                        'marketType': result.get('market_type'),
                        'source': result.get('source'),
                        'minTransactionUsd': float(result.get('min_transaction_usd', 0)),
                        'cumulativeMinAmountUsd': float(result.get('cumulative_min_amount_usd', 0)),
                        'eventsConfig': result.get('events_config'),
                        'triggerLogic': result.get('trigger_logic'),
                        'notifyMethods': result.get('notify_methods'),
                        'status': result.get('status'),
                    }
                    redis_client.set(redis_key, cache_data, ex=300)  # 5分钟过期
                    logger.debug(f"✅ 配置已缓存到 Redis (key: {redis_key})")
                except Exception as e:
                    logger.warning(f"⚠️  缓存配置到 Redis 失败: {e}")
                
                return result
            else:
                logger.warning("⚠️  未找到 BSC 全局配置，使用默认值")
                return None
                
        except Exception as e:
            logger.error(f"❌ 加载全局配置失败: {e}")
            return None
    
    def parse_events_config(self, events_config_str: Optional[str]) -> Optional[Dict]:
        """
        解析 events_config JSON 字符串
        
        Args:
            events_config_str: JSON 字符串
        
        Returns:
            解析后的字典
        """
        if not events_config_str:
            return None
        
        try:
            config = json.loads(events_config_str)
            logger.debug(f"events_config 解析成功: {config}")
            return config
        except Exception as e:
            logger.error(f"❌ 解析 events_config 失败: {e}")
            return None
    
    async def handle_block_events(self, events: List[Dict]):
        """
        处理区块事件（第一层过滤：按区块聚合）
        
        Args:
            events: 交易事件列表
        """
        if not events:
            return
        
        import time
        start_time = time.time()
        
        # 按区块号聚合并处理
        blocks = defaultdict(list)
        for event in events:
            blocks[event['block_number']].append(event)
        
        for block_number, block_events in blocks.items():
            await self.process_block_trades(block_number, block_events)
    
    async def process_block_trades(self, block_number: int, events: List[Dict]):
        """
        处理单个区块的交易（第一层过滤 + Redis冷却期检查）
        
        Args:
            block_number: 区块号
            events: 该区块的交易事件
        """
        import time
        block_start_time = time.time()
        
        # 按代币地址聚合
        token_trades = defaultdict(list)
        for event in events:
            token_trades[event['base_token']].append(event)
        
        logger.info(f"🔍 区块 #{block_number} | 交易: {len(events)} | 代币: {len(token_trades)}")
        
        # 统计
        filter_stats = {
            'total_tokens': len(token_trades),
            'passed_amount': 0,
            'non_launchpad': 0,
            'other_platform': 0,
            'fourmeme_found': 0,
            'in_cooldown': 0,
            'triggered': 0
        }
        
        # 对每个代币进行过滤（根据内外盘使用不同配置）
        for token_address, trades in token_trades.items():
            # 计算单笔最大和累计
            usdt_amounts = [t['usdt_value'] for t in trades]
            single_max = max(usdt_amounts)
            total_sum = sum(usdt_amounts)
            
            # 判断内外盘（从 webhook_processor 的标记）
            is_internal = trades[0].get('is_fourmeme_internal', False)
            market_type = 'internal' if is_internal else 'external'
            pool_name = "内盘" if is_internal else "外盘"
            
            # 使用缓存的配置
            market_config = self.internal_config if is_internal else self.external_config
            if not market_config:
                logger.warning(f"⚠️  未找到 {market_type} 配置，跳过")
                continue
            
            # 获取该配置的金额阈值
            market_min_transaction = market_config.get('min_transaction_usd', 400)
            market_cumulative_min = market_config.get('cumulative_min_amount_usd', 1000)
            
            # 第一层：使用该配置的阈值判断金额是否达标
            if single_max >= market_min_transaction or total_sum >= market_cumulative_min:
                filter_stats['passed_amount'] += 1
                
                # 获取 launchpad_info（用于第二层过滤）
                # 内盘：webhook_processor 已判断，直接构造 launchpad_info
                # 外盘：需要调用 API 验证平台
                if is_internal:
                    # 内盘：直接构造 launchpad_info
                    launchpad_info = {
                        'launchpad': 'fourmeme',
                        'launchpad_status': 0,  # 0 = 内盘
                        'pair_address': trades[0]['pair_address']
                    }
                    filter_stats['fourmeme_found'] += 1
                else:
                    # 外盘：调用 API 验证平台（放到线程池避免阻塞）
                    launchpad_info = await asyncio.to_thread(
                        self.dbotx_api.get_token_launchpad_info, 'bsc', token_address
                    )
                    
                    if launchpad_info is None:
                        filter_stats['non_launchpad'] += 1
                        logger.debug(f"⏭️  {token_address[:10]}... 非Launchpad")
                        continue
                    
                    launchpad_platform = launchpad_info.get('launchpad')
                    if launchpad_platform != 'fourmeme':
                        filter_stats['other_platform'] += 1
                        logger.debug(f"⏭️  {token_address[:10]}... 平台:{launchpad_platform}")
                        continue
                    
                    filter_stats['fourmeme_found'] += 1
                
                # 通过 fourmeme 验证，记录详细信息
                logger.info(f"   🎯 [{pool_name}] {token_address[:10]}... | 单笔{single_max:.0f}U 累计{total_sum:.0f}U")
                
                # 检查 Redis 冷却期（但不跳过，继续处理）
                cooldown_minutes = self.min_interval_seconds / 60
                in_cooldown = not self.check_alert_cooldown(token_address, cooldown_minutes)
                if in_cooldown:
                    filter_stats['in_cooldown'] += 1
                    logger.info(f"⏰ [冷却期] {token_address[:10]}... 在冷却中 (会保存到DB+WS，但不推送TG)")
                
                # 进入第二层过滤（调用 API），传递冷静期状态
                await self.apply_second_layer_filter(
                    token_address,
                    trades[0]['pair_address'],
                    single_max,
                    total_sum,
                    block_number,
                    launchpad_info,
                    in_cooldown=in_cooldown
                )
        
        # 单行显示过滤统计
        logger.info(
            f"   → 金额✓:{filter_stats['passed_amount']} "
            f"Fourmeme:{filter_stats['fourmeme_found']} "
            f"冷却:{filter_stats['in_cooldown']} "
            f"🎯推送:{filter_stats['triggered']}"
        )
    
    async def apply_second_layer_filter(
        self,
        token_address: str,
        pair_address: str,
        single_max: float,
        total_sum: float,
        block_number: int,
        launchpad_info: Dict,
        in_cooldown: bool = False
    ):
        """
        第二层过滤：调用 GMGN API + events_config 判断
        
        Args:
            token_address: 代币地址
            pair_address: 交易对地址
            single_max: 单笔最大金额
            total_sum: 累计金额
            block_number: 区块号
            launchpad_info: Launchpad 信息
            in_cooldown: 是否在冷静期内
        """
        try:
            # 0. 判断内外盘，使用缓存的配置
            launchpad_status = launchpad_info.get('launchpad_status', 0)
            is_internal = (launchpad_status == 0)
            market_type = 'internal' if is_internal else 'external'
            pool_name = "内盘" if is_internal else "外盘"
            
            # 使用缓存的配置和 events_config
            market_config = self.internal_config if is_internal else self.external_config
            market_events_config = self.internal_events_config if is_internal else self.external_events_config
            
            if not market_config or not market_events_config:
                logger.warning(f"⚠️  未找到 {market_type} 配置，跳过")
                return
            
            # 1. 使用 launchpad_info 中返回的交易对地址获取详细数据
            api_pair_address = launchpad_info.get('pair_address')
            if not api_pair_address:
                logger.debug(f"⏭️  跳过 {token_address[:10]}... (无交易对地址)")
                return
            
            # 2. 调用 DBotX API 获取代币数据（放到线程池避免阻塞）
            raw_data = await asyncio.to_thread(
                self.dbotx_api.get_pair_info, 'bsc', api_pair_address
            )
            
            if not raw_data:
                logger.debug(f"⏭️  跳过 {token_address[:10]}... (无DBotX数据)")
                return
            
            # 3. 解析代币数据（使用1分钟实时数据）
            token_data = self.dbotx_api.parse_token_data(raw_data)
            if not token_data:
                logger.debug(f"⏭️  跳过 {token_address[:10]}... (解析失败)")
                return
            
            # 4. 获取1分钟实时数据（比GMGN的5分钟数据更及时）
            price_change_1m = token_data.get('price_change', 0)  # 1分钟涨幅（已转换为%）
            volume_1m = token_data.get('volume', 0)  # 1分钟交易量
            
            # 构造 stats 数据（用于 TriggerLogic 评估）
            stats = {
                'priceChange': price_change_1m,
                'volume': volume_1m,  # 兼容旧版
                'volume_1m': volume_1m,  # 🔥 DBotX API 提供的1分钟交易量
                'holderChange': 0  # 暂不使用持有者变化
            }
            
            # 4. 判断是否满足 events_config
            if not market_events_config:
                logger.debug("⏭️  跳过 (无events_config)")
                return
            
            symbol = token_data.get('symbol', 'Unknown')
            pool_emoji = "🔴" if market_type == 'internal' else "🟢"
            logger.info(f"")
            logger.info(f"🔎 [DBotX 指标检查] {pool_emoji}{pool_name} {symbol} ({token_address[:10]}...)")
            logger.info(f"   ├─ 1分钟涨幅: {price_change_1m:+.2f}%")
            logger.info(f"   ├─ 1分钟交易量: ${volume_1m:,.2f}")
            logger.info(f"   └─ 5分钟涨幅: {token_data.get('price_5m', 0):+.2f}% (参考)")
            
            # 使用 TriggerLogic 评估触发条件（使用该市场类型的配置）
            trigger_logic = market_config.get('trigger_logic', 'any') if market_config else 'any'
            should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
                stats, market_events_config, trigger_logic
            )
            
            if not should_trigger:
                logger.info(f"   ❌ 未达到触发条件")
                logger.info("")
                return
            
            # 5. 满足条件，准备推送
            logger.info(f"   ✅ 满足条件！触发 {len(triggered_events)} 个事件")
            logger.info(f"")
            
            if in_cooldown:
                logger.info(f"⏰ [冷静期内] {symbol} | 单笔${single_max:.0f} | 累计${total_sum:.0f} （保存记录但不推送）")
            else:
                logger.info(f"🚨 [准备推送] {symbol} | 单笔${single_max:.0f} | 累计${total_sum:.0f}")
            
            # 发送推送（包含数据库、WebSocket、TG），传递冷静期状态
            await self.send_bsc_alert(
                token_address=token_address,
                token_data=token_data,
                triggered_events=triggered_events,
                single_max=single_max,
                total_sum=total_sum,
                block_number=block_number,
                pair_address=pair_address,
                launchpad_info=launchpad_info,
                in_cooldown=in_cooldown
            )
            
        except Exception as e:
            logger.error(f"❌ 第二层过滤异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def apply_third_layer_control(
        self,
        token_address: str,
        pair_address: str,
        single_max: float,
        total_sum: float,
        alert_reasons: List[str],
        block_number: int,
        cooldown_minutes: float
    ):
        """
        第三层控制：推送频率限制
        
        Args:
            token_address: 代币地址
            pair_address: 交易对地址
            single_max: 单笔最大金额
            total_sum: 累计金额
            alert_reasons: 触发原因列表
            block_number: 区块号
            cooldown_minutes: 冷却时间（分钟）
        """
        if not self.alert_enable:
            # 推送控制未启用，直接推送
            self.send_alert(
                token_address,
                pair_address,
                single_max,
                total_sum,
                alert_reasons,
                block_number
            )
            return
        
        # 检查冷却期
        if self.check_alert_cooldown(token_address, cooldown_minutes):
            logger.info(
                f"✅ [第三层通过] 代币 {token_address[:10]}... "
                f"不在冷却期内，准备推送"
            )
            
            # 发送推送
            self.send_alert(
                token_address,
                pair_address,
                single_max,
                total_sum,
                alert_reasons,
                block_number
            )
            
            # 更新推送历史
            self.update_alert_history(token_address)
        else:
            logger.debug(
                f"⏭️  [第三层拦截] 代币 {token_address[:10]}... "
                f"在冷却期内，跳过推送"
            )
    
    def get_token_monitor_config(self, token_address: str) -> Optional[Dict]:
        """
        从数据库获取代币监控配置
        
        Args:
            token_address: 代币地址
        
        Returns:
            配置字典或 None
        """
        try:
            sql = """
            SELECT price_change_5m, cooldown_minutes, is_active
            FROM token_monitor_config
            WHERE ca = %s AND is_active = 1
            LIMIT 1
            """
            result = await asyncio.to_thread(
                self.db.execute_query, sql, (token_address,), fetch_one=True
            )
            return result
        except Exception as e:
            logger.warning(f"查询代币监控配置失败: {e}")
            return None
    
    def check_alert_cooldown(self, token_address: str, cooldown_minutes: float) -> bool:
        """
        检查代币是否在冷却期内
        
        Args:
            token_address: 代币地址
            cooldown_minutes: 冷却时间（分钟）
        
        Returns:
            True: 不在冷却期，可以推送
            False: 在冷却期内，不能推送
        """
        redis_key = f"bsc:alert:last:{token_address.lower()}"
        
        try:
            last_alert_data = await asyncio.to_thread(
                self.redis_client.get, redis_key
            )
            
            if not last_alert_data:
                return True  # 首次推送
            
            # 如果已经是字典，直接使用；否则解析 JSON
            if isinstance(last_alert_data, dict):
                last_alert = last_alert_data
            else:
                last_alert = json.loads(last_alert_data)
            
            last_timestamp = last_alert.get('timestamp', 0)
            now_timestamp = int(time.time())
            
            cooldown_seconds = cooldown_minutes * 60
            
            if now_timestamp - last_timestamp < cooldown_seconds:
                return False  # 在冷却期内
            
            return True  # 已过冷却期
        
        except Exception as e:
            logger.error(f"检查冷却期失败: {e}")
            return True  # 出错时允许推送
    
    def update_alert_history(self, token_address: str):
        """
        更新代币推送历史
        
        Args:
            token_address: 代币地址
        """
        redis_key = f"bsc:alert:last:{token_address.lower()}"
        
        try:
            # 读取历史记录
            last_alert_data = await asyncio.to_thread(
                self.redis_client.get, redis_key
            )
            alert_count = 1
            
            if last_alert_data:
                # 如果已经是字典，直接使用；否则解析 JSON
                if isinstance(last_alert_data, dict):
                    last_alert = last_alert_data
                else:
                    last_alert = json.loads(last_alert_data)
                alert_count = last_alert.get('alert_count', 0) + 1
            
            # 更新记录
            alert_data = {
                'timestamp': int(time.time()),
                'alert_count': alert_count
            }
            
            # 保存到 Redis，TTL 10 分钟
            await asyncio.to_thread(
                self.redis_client.set,
                redis_key,
                json.dumps(alert_data),
                ex=600  # 10 分钟
            )
        
        except Exception as e:
            logger.error(f"更新推送历史失败: {e}")
    
    async def send_bsc_alert(
        self,
        token_address: str,
        token_data: Dict,
        triggered_events: List,
        single_max: float,
        total_sum: float,
        block_number: int,
        pair_address: str,
        launchpad_info: Dict,
        in_cooldown: bool = False
    ):
        """
        发送 BSC 监控推送通知
        
        Args:
            token_address: 代币地址
            token_data: GMGN API 返回的代币数据
            triggered_events: 触发事件列表（TriggerEvent 对象）
            single_max: 单笔最大金额
            total_sum: 累计金额
            block_number: 区块号
            pair_address: 交易对地址
            launchpad_info: Launchpad 信息
            in_cooldown: 是否在冷静期内（冷静期内只保存不推送）
        """
        try:
            # 获取代币信息
            symbol = token_data.get('symbol', 'Unknown')
            name = token_data.get('name', 'Unknown')
            stats = token_data.get('stats5m', {})
            
            # 获取当前价格（从储备量计算，放到线程池避免阻塞）
            try:
                token0, token1 = await asyncio.to_thread(
                    self.collector.get_token0_token1, pair_address
                )
                reserve0, reserve1 = await asyncio.to_thread(
                    self.collector.get_reserves, pair_address
                )
                
                # 判断哪个是基础代币
                if token0.lower() == token_address.lower():
                    # token0 是基础代币（放到线程池避免阻塞）
                    decimals0 = await asyncio.to_thread(self.collector.get_decimals, token0)
                    decimals1 = await asyncio.to_thread(self.collector.get_decimals, token1)
                    qty0 = reserve0 / (10 ** decimals0)
                    qty1 = reserve1 / (10 ** decimals1)
                    price_in_quote = qty1 / qty0 if qty0 > 0 else 0
                    
                    # 转换为 USDT
                    price_usdt = await asyncio.to_thread(
                        self.collector.quote_to_usdt, token1, price_in_quote
                    )
                else:
                    # token1 是基础代币（放到线程池避免阻塞）
                    decimals0 = await asyncio.to_thread(self.collector.get_decimals, token0)
                    decimals1 = await asyncio.to_thread(self.collector.get_decimals, token1)
                    qty0 = reserve0 / (10 ** decimals0)
                    qty1 = reserve1 / (10 ** decimals1)
                    price_in_quote = qty0 / qty1 if qty1 > 0 else 0
                    
                    price_usdt = await asyncio.to_thread(
                        self.collector.quote_to_usdt, token0, price_in_quote
                    )
            
            except Exception as e:
                logger.warning(f"计算价格失败: {e}")
                price_usdt = 0.0
            
            # 构建推送原因（仅第二层触发原因）
            alert_reasons = [e.description for e in triggered_events]
            
            # 获取额外的 Token 数据（DBotX API）
            price_change = token_data.get('price_1m', 0)  # 1分钟涨幅
            volume_24h = token_data.get('volume_24h', 0)  # 24小时交易量
            holders = token_data.get('holder_count', 0)
            market_cap = token_data.get('market_cap', 0)
            logo = token_data.get('logo', '')
            
            # 1. 数据库写入 + WebSocket 推送（放到线程池避免阻塞事件循环）
            success = await asyncio.to_thread(
                self.alert_recorder.write_bsc_alert,
                ca=token_address,
                token_name=name,
                token_symbol=symbol,
                single_max=single_max,
                total_sum=total_sum,
                alert_reasons=alert_reasons,
                block_number=block_number,
                price_usdt=price_usdt,
                pair_address=pair_address,
                market_cap=market_cap,
                price_change=price_change,
                volume_24h=volume_24h,
                holders=holders,
                logo=logo,
                notify_error="冷静期内不播报" if in_cooldown else None
            )
            
            if not success:
                logger.error(f"❌ 数据库写入失败: {symbol}")
                return
            
            if in_cooldown:
                logger.info(f"⏰ [数据库] 写入成功（冷静期，WebSocket 跳过）")
            else:
                logger.info(f"✅ [数据库] 写入成功 | WebSocket 已推送")
            
            # 设置 Redis 冷却期（添加随机抖动）
            if not in_cooldown:  # 只在第一次推送时设置冷却期
                self.update_alert_history(token_address)
                # 基础冷却时间 + 随机抖动
                jitter = random.randint(0, self.cooldown_jitter)
                total_cooldown = self.min_interval_seconds + jitter
                cooldown_minutes = total_cooldown / 60
                logger.info(f"🔒 [冷却期] 已设置 {cooldown_minutes:.1f}分钟冷却期 (基础{self.min_interval_seconds//60}分 + 抖动{jitter}秒)")
            
            # 2. Telegram 推送（仅在非冷静期时推送）
            logger.info(f"🔍 [TG推送检查] enable_telegram={self.enable_telegram}, in_cooldown={in_cooldown}")
            if self.enable_telegram and not in_cooldown:
                message = self.format_bsc_tg_message(
                    token_address=token_address,
                    symbol=symbol,
                    name=name,
                    price_usdt=price_usdt,
                    single_max=single_max,
                    total_sum=total_sum,
                    market_cap=market_cap,
                    alert_reasons=alert_reasons,
                    block_number=block_number,
                    pair_address=pair_address,
                    launchpad_info=launchpad_info
                )
                
                # 创建按钮
                buttons = self.create_bsc_buttons(token_address)
                
                # 异步发送 TG 消息到 BSC 专用频道
                try:
                    from ..core.config import TELEGRAM_CONFIG
                    target_channel = str(TELEGRAM_CONFIG.get('bsc_channel_id'))
                    
                    # 推送前日志
                    logger.info(
                        f"📤 [BSCMonitor] 准备推送至 Telegram -> {target_channel} | "
                        f"token={symbol} ({token_address[:10]}...) | "
                        f"单笔${single_max:.0f} | 累计${total_sum:.0f}"
                    )
                
                    tg_success = await self.notification_manager.send_telegram(
                        target=target_channel,
                        message=message,
                        reply_markup=buttons
                    )
                    
                    # 推送后日志
                    if tg_success:
                        logger.info(f"✅ [BSCMonitor] Telegram 推送完成 -> {symbol} | success=True（含GMGN+OKX按钮）")
                    else:
                        logger.warning(f"⚠️ [BSCMonitor] Telegram 推送完成 -> {symbol} | success=False")
                except Exception as e:
                    logger.error(f"❌ [BSCMonitor] Telegram 推送异常 -> {symbol}: {type(e).__name__} - {e}")
                    import traceback
                    logger.error(f"   堆栈跟踪:\n{traceback.format_exc()}")
        
        except Exception as e:
            logger.error(f"发送推送通知失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    
    def create_bsc_buttons(self, token_address: str):
        """
        创建 BSC 代币的 Telegram 内联按钮
        
        Args:
            token_address: 代币合约地址
            
        Returns:
            InlineKeyboardMarkup 对象
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        buttons = [
            [
                InlineKeyboardButton("📊 GMGN", url=f"https://gmgn.ai/bsc/token/{token_address}"),
                InlineKeyboardButton("🔍 OKX", url=f"https://www.okx.com/web3/dex-swap#inputChain=56&inputCurrency={token_address}&outputChain=56&outputCurrency=0x55d398326f99059fF775485246999027B3197955")
            ]
        ]
        return InlineKeyboardMarkup(buttons)
    
    def format_bsc_tg_message(
        self,
        token_address: str,
        symbol: str,
        name: str,
        price_usdt: float,
        single_max: float,
        total_sum: float,
        market_cap: float,
        alert_reasons: List[str],
        block_number: int,
        pair_address: str,
        launchpad_info: Dict
    ) -> str:
        """
        格式化 BSC 监控的 Telegram 消息
        
        Returns:
            HTML 格式的消息
        """
        # 解析 launchpad 信息
        launchpad_status = launchpad_info.get('launchpad_status', 0)
        
        # 判断内外盘
        if launchpad_status == 0:
            pool_status = "🔴 Fourmeme内盘"
        else:
            pool_status = "🟢 已迁移外盘"
        
        # 格式化数字（使用 K/M 后缀）
        single_max_str = self.format_number(single_max)
        total_sum_str = self.format_number(total_sum)
        market_cap_str = self.format_number(market_cap)
        
        message = f"""<b>🟢 BSC 链上信号 (Fourmeme)</b>

💰 代币: {symbol}
📝 名称: {name}
🔗 合约: <code>{token_address}</code>

📊 <b>实时数据</b>
💵 当前价格: ${price_usdt:.5f} USDT
💎 市值: ${market_cap_str}
🏊 状态: {pool_status}
🏦 交易对: {pair_address[:10]}...

📉 <b>交易数据</b>
💰 单笔最大: ${single_max_str}
📊 区块累计: ${total_sum_str}
🔢 区块号: #{block_number}

✨ <b>触发原因</b>
{chr(10).join('• ' + reason for reason in alert_reasons)}

⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        return message
    
    async def start(self):
        """启动监控（BSC采集器在独立线程运行，避免阻塞事件循环）"""
        logger.info("🚀 BSC 监控器启动中...")
        logger.info("⚙️  BSC区块采集器将在独立线程运行（避免阻塞主事件循环）")
        
        # 确保设置事件循环引用（用于线程间通信）
        if not self.collector.event_loop:
            self.collector.event_loop = asyncio.get_running_loop()
            logger.info("✅ 事件循环引用已设置")
        
        # 在独立线程中运行同步的 collect() 方法
        await asyncio.to_thread(self.collector.collect)
    
    async def stop(self):
        """停止监控"""
        logger.info("⏹️  BSC 监控器停止中...")
        # collector.stop() 现在是同步方法，直接调用
        self.collector.stop()
        logger.info("✅ BSC 监控器已停止")

