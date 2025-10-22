"""
BSC 监控主逻辑
实现三层过滤机制：
1. 第一层：交易金额过滤（单笔 >= 400 USDT OR 区块累计 >= 1000 USDT）
2. 第二层：数据库配置指标过滤（价格涨幅、交易量等）
3. 第三层：推送频率控制（同一代币 30 秒冷却期）
"""
import logging
import time
import json
from typing import Dict, List, Optional
from collections import defaultdict
from datetime import datetime

from ..collectors.bsc_collector import BSCBlockCollector
from ..core.redis_client import get_redis
from ..core.database import get_db
from ..notifiers.alert_recorder import get_alert_recorder
from ..notifiers.manager import get_notification_manager
from ..api.gmgn_api import get_gmgn_api
from .trigger_logic import TriggerLogic

logger = logging.getLogger(__name__)


class BSCMonitor:
    """BSC 链上交易监控器"""
    
    def __init__(self, config: Dict):
        """
        初始化 BSC 监控器
        
        Args:
            config: 配置字典
        """
        self.config = config
        
        # 全局监控配置（从数据库或 Redis 读取）
        self.global_config = self.load_global_config()
        
        # 第一层过滤：交易金额阈值
        self.single_max_usdt = self.global_config.get('min_transaction_usd', 400) if self.global_config else 400
        self.block_accumulate_usdt = 1000  # 写死
        
        # 第二层过滤：events_config（从配置解析）
        self.events_config = self.parse_events_config(
            self.global_config.get('events_config') if self.global_config else None
        )
        
        # 第三层控制：推送频率
        self.min_interval_seconds = 30  # 冷却期30秒
        
        # 通知配置
        self.enable_telegram = config.get('notification', {}).get('enable_telegram', True)
        self.enable_wechat = config.get('notification', {}).get('enable_wechat', True)
        
        # Redis 客户端（用于冷却期控制）
        self.redis_client = get_redis()
        
        # 数据库
        self.db = get_db()
        
        # 预警记录器
        self.alert_recorder = get_alert_recorder()
        
        # 通知管理器（用于 TG 推送）
        self.notification_manager = get_notification_manager()
        
        # GMGN API（用于获取 BSC 代币数据）
        self.gmgn_api = get_gmgn_api()
        
        # 区块收集器
        self.collector = BSCBlockCollector(config)
        self.collector.on_data_received = self.handle_block_events
        
        logger.info(f"✅ BSC 监控器初始化完成")
        if self.global_config:
            logger.info(f"   配置名称: {self.global_config.get('config_name')}")
            logger.info(f"   链类型: {self.global_config.get('chain_type')}")
        logger.info(f"   第一层过滤：单笔 >= {self.single_max_usdt} USDT OR 累计 >= {self.block_accumulate_usdt} USDT")
        if self.events_config:
            logger.info(f"   第二层过滤：价格涨跌 >= {self.events_config.get('priceChange', {}).get('risePercent')}%, 交易量变化 >= {self.events_config.get('volume', {}).get('increasePercent')}%")
        logger.info(f"   第三层控制：推送间隔 >= {self.min_interval_seconds} 秒")
    
    def load_global_config(self) -> Optional[Dict]:
        """
        加载全局监控配置（优先从 Redis 读取）
        
        Returns:
            配置字典或 None
        """
        chain_type = 'bsc'
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
                    'source': cached_config.get('source'),
                    'min_transaction_usd': cached_config.get('minTransactionUsd'),
                    'events_config': cached_config.get('eventsConfig'),
                    'trigger_logic': cached_config.get('triggerLogic'),
                    'notify_methods': cached_config.get('notifyMethods'),
                    'status': cached_config.get('status'),
                }
                return config
            
            # 2. Redis 没有，从数据库读取
            logger.debug("Redis 中未找到配置，从数据库加载...")
            db = get_db()
            sql = """
            SELECT id, config_name, chain_type, source, min_transaction_usd,
                   events_config, trigger_logic, notify_methods, status
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
                        'source': result.get('source'),
                        'minTransactionUsd': float(result.get('min_transaction_usd', 0)),
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
    
    def handle_block_events(self, events: List[Dict]):
        """
        处理区块事件（第一层过滤：按区块聚合）
        
        Args:
            events: 交易事件列表
        """
        if not events:
            return
        
        logger.info(f"🔄 处理 {len(events)} 个交易事件")
        
        # 按区块号聚合
        blocks = defaultdict(list)
        for event in events:
            blocks[event['block_number']].append(event)
        
        # 处理每个区块
        for block_number, block_events in blocks.items():
            logger.debug(f"   区块 {block_number}: {len(block_events)} 个事件")
            self.process_block_trades(block_number, block_events)
    
    def process_block_trades(self, block_number: int, events: List[Dict]):
        """
        处理单个区块的交易（第一层过滤 + Redis冷却期检查）
        
        Args:
            block_number: 区块号
            events: 该区块的交易事件
        """
        # 按代币地址聚合
        token_trades = defaultdict(list)
        for event in events:
            token_address = event['base_token']
            token_trades[token_address].append(event)
        
        # 对每个代币进行第一层过滤
        for token_address, trades in token_trades.items():
            # 计算单笔最大和累计
            usdt_amounts = [t['usdt_value'] for t in trades]
            single_max = max(usdt_amounts)
            total_sum = sum(usdt_amounts)
            
            # 第一层：判断是否触发金额条件
            if single_max >= self.single_max_usdt or total_sum >= self.block_accumulate_usdt:
                logger.info(
                    f"✅ [第一层触发] 区块 {block_number}, "
                    f"代币 {token_address[:10]}..., "
                    f"单笔最大: {single_max:.2f} USDT, "
                    f"累计: {total_sum:.2f} USDT, "
                    f"交易笔数: {len(trades)}"
                )
                
                # 提前检查 Redis 冷却期（减少 API 调用）
                cooldown_minutes = self.min_interval_seconds / 60
                if not self.check_alert_cooldown(token_address, cooldown_minutes):
                    logger.debug(f"⏭️  [冷却期拦截] 代币 {token_address[:10]}... 在冷却期内，跳过")
                    continue
                
                # 进入第二层过滤（调用 API）
                self.apply_second_layer_filter(
                    token_address,
                    trades[0]['pair_address'],
                    single_max,
                    total_sum,
                    block_number
                )
    
    def apply_second_layer_filter(
        self,
        token_address: str,
        pair_address: str,
        single_max: float,
        total_sum: float,
        block_number: int
    ):
        """
        第二层过滤：调用 GMGN API + events_config 判断
        
        Args:
            token_address: 代币地址
            pair_address: 交易对地址
            single_max: 单笔最大金额
            total_sum: 累计金额
            block_number: 区块号
        """
        try:
            # 1. 调用 GMGN API 获取代币数据
            gmgn_data_list = self.gmgn_api.get_token_info_batch('bsc', [token_address])
            
            if not gmgn_data_list or len(gmgn_data_list) == 0:
                logger.warning(f"⚠️  无法获取代币数据: {token_address[:10]}...")
                return
            
            # 2. 解析代币数据
            token_data = self.gmgn_api.parse_token_data(gmgn_data_list[0])
            if not token_data:
                logger.warning(f"⚠️  解析代币数据失败: {token_address[:10]}...")
                return
            
            # 3. 计算 5分钟涨跌幅和交易量
            price_5m = token_data.get('price_5m', 0)
            price_current = token_data.get('price', 0)
            volume_5m = token_data.get('volume_5m', 0)
            
            # 价格变化百分比
            if price_5m and price_5m > 0:
                price_change_5m = ((price_current - price_5m) / price_5m) * 100
            else:
                price_change_5m = 0
            
            # 构造 stats5m 数据（用于 TriggerLogic 评估）
            stats = {
                'priceChange': price_change_5m,
                'volume': volume_5m,
                'holderChange': 0  # GMGN API 没有提供 5m holder 变化，默认 0
            }
            
            # 4. 判断是否满足 events_config
            if not self.events_config:
                logger.warning("⚠️  events_config 未配置，跳过第二层判断")
                return
            
            # 使用 TriggerLogic 评估触发条件
            trigger_logic = self.global_config.get('trigger_logic', 'any') if self.global_config else 'any'
            should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
                stats, self.events_config, trigger_logic
            )
            
            if not should_trigger:
                logger.debug(f"⏭️  [第二层未触发] 代币 {token_address[:10]}... 不满足 events_config 条件")
                return
            
            # 5. 满足条件，准备推送
            logger.info(
                f"✅ [第二层触发] 代币 {token_address[:10]}..., "
                f"触发事件数: {len(triggered_events)}"
            )
            
            # 发送推送（包含数据库、WebSocket、TG）
            self.send_bsc_alert(
                token_address=token_address,
                token_data=token_data,
                triggered_events=triggered_events,
                single_max=single_max,
                total_sum=total_sum,
                block_number=block_number,
                pair_address=pair_address
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
            result = self.db.execute_query(sql, (token_address,), fetch_one=True)
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
            last_alert_data = self.redis_client.get(redis_key)
            
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
            last_alert_data = self.redis_client.get(redis_key)
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
            self.redis_client.set(
                redis_key,
                json.dumps(alert_data),
                ex=600  # 10 分钟
            )
        
        except Exception as e:
            logger.error(f"更新推送历史失败: {e}")
    
    def send_bsc_alert(
        self,
        token_address: str,
        token_data: Dict,
        triggered_events: List,
        single_max: float,
        total_sum: float,
        block_number: int,
        pair_address: str
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
        """
        try:
            # 获取代币信息
            symbol = token_data.get('symbol', 'Unknown')
            name = token_data.get('name', 'Unknown')
            stats = token_data.get('stats5m', {})
            
            # 获取当前价格（从储备量计算）
            try:
                token0, token1 = self.collector.get_token0_token1(pair_address)
                reserve0, reserve1 = self.collector.get_reserves(pair_address)
                
                # 判断哪个是基础代币
                if token0.lower() == token_address.lower():
                    # token0 是基础代币
                    decimals0 = self.collector.get_decimals(token0)
                    decimals1 = self.collector.get_decimals(token1)
                    qty0 = reserve0 / (10 ** decimals0)
                    qty1 = reserve1 / (10 ** decimals1)
                    price_in_quote = qty1 / qty0 if qty0 > 0 else 0
                    
                    # 转换为 USDT
                    price_usdt = self.collector.quote_to_usdt(token1, price_in_quote)
                else:
                    # token1 是基础代币
                    decimals0 = self.collector.get_decimals(token0)
                    decimals1 = self.collector.get_decimals(token1)
                    qty0 = reserve0 / (10 ** decimals0)
                    qty1 = reserve1 / (10 ** decimals1)
                    price_in_quote = qty0 / qty1 if qty1 > 0 else 0
                    
                    price_usdt = self.collector.quote_to_usdt(token0, price_in_quote)
            
            except Exception as e:
                logger.warning(f"计算价格失败: {e}")
                price_usdt = 0.0
            
            # 构建触发事件列表（转换为字典格式）
            trigger_events_list = [e.to_dict() for e in triggered_events]
            
            # 构建推送原因
            alert_reasons = [e.description for e in triggered_events]
            alert_reasons.append(f"💰 单笔最大: ${single_max:.2f} USDT")
            alert_reasons.append(f"📊 区块累计: ${total_sum:.2f} USDT")
            
            # 1. 数据库写入 + WebSocket 推送
            logger.info(f"📢 记录 BSC 监控预警: {symbol} ({token_address[:10]}...)")
            
            notify_result = self.alert_recorder.send_alert_notification(
                config_id=0,  # BSC 监控使用 config_id = 0
                ca=token_address,
                token_name=name,
                token_symbol=symbol,
                trigger_events=trigger_events_list,
                stats_data=stats,
                notify_methods="telegram,wechat"
            )
            
            success = notify_result['db_success']
            
            if success:
                logger.info(f"📝 数据库写入: ✅ 成功")
            else:
                logger.error(f"❌ BSC 预警记录写入失败")
                return
            
            if notify_result['realtime_success']:
                logger.info(f"🌐 WebSocket推送: ✅ 成功")
            else:
                logger.warning(f"🌐 WebSocket推送: ⚠️  失败")
            
            # 设置 Redis 冷却期
            self.update_alert_history(token_address)
            
            # 2. Telegram 推送
            if self.enable_telegram:
                message = self.format_bsc_tg_message(
                    token_address=token_address,
                    symbol=symbol,
                    name=name,
                    price_usdt=price_usdt,
                    single_max=single_max,
                    total_sum=total_sum,
                    alert_reasons=alert_reasons,
                    block_number=block_number,
                    pair_address=pair_address
                )
                
                # 异步发送 TG 消息
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                try:
                    from ..core.config import TELEGRAM_CONFIG
                    target_channel = str(TELEGRAM_CONFIG.get('target_channel_id'))
                    
                    tg_success = loop.run_until_complete(
                        self.notification_manager.send_telegram(
                            target=target_channel,
                            message=message
                        )
                    )
                    
                    if tg_success:
                        logger.info(f"📢 Telegram推送: ✅ 成功")
                    else:
                        logger.warning(f"📢 Telegram推送: ⚠️  失败")
                except Exception as e:
                    logger.warning(f"📢 Telegram推送异常: {e}")
        
        except Exception as e:
            logger.error(f"发送推送通知失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    
    def format_bsc_tg_message(
        self,
        token_address: str,
        symbol: str,
        name: str,
        price_usdt: float,
        single_max: float,
        total_sum: float,
        alert_reasons: List[str],
        block_number: int,
        pair_address: str
    ) -> str:
        """
        格式化 BSC 监控的 Telegram 消息
        
        Returns:
            HTML 格式的消息
        """
        # GMGN 和其他链接
        gmgn_url = f'https://gmgn.ai/bsc/token/{token_address}'
        bscscan_url = f'https://bscscan.com/token/{token_address}'
        pancake_url = f'https://pancakeswap.finance/swap?outputCurrency={token_address}'
        
        message = f"""<b>🟢 BSC 链上信号</b>

💰 代币: {symbol}
📝 名称: {name}
🔗 合约: <code>{token_address}</code>

📊 <b>实时数据</b>
💵 当前价格: ${price_usdt:.10f} USDT
🏦 交易对: {pair_address[:10]}...

📉 <b>交易数据</b>
💰 单笔最大: ${single_max:.2f} USDT
📊 区块累计: ${total_sum:.2f} USDT
🔢 区块号: #{block_number}

✨ <b>触发原因</b>
{chr(10).join('• ' + reason for reason in alert_reasons)}

⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔗 <b>链接</b>
📊 <a href="{gmgn_url}">GMGN</a> | <a href="{bscscan_url}">BscScan</a> | <a href="{pancake_url}">PancakeSwap</a>
"""
        
        return message
    
    def start(self):
        """启动监控"""
        logger.info("🚀 BSC 监控器启动中...")
        self.collector.collect()

