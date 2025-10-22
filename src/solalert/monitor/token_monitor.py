"""
Token监控引擎
核心监控逻辑，整合所有模块
"""
import asyncio
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from .jupiter_api import JupiterAPI
from .trigger_logic import TriggerLogic, TriggerEvent
from .notifiers import NotificationService, NotificationMessage
from ..core.database import get_db
from ..core.logger import get_logger
from ..core.config import REDIS_CONFIG
from ..api.gmgn_api import get_gmgn_api
from ..notifiers.alert_recorder import AlertRecorder
import redis.asyncio as aioredis

logger = get_logger(__name__)


class TokenMonitorEngine:
    """Token监控引擎"""
    
    def __init__(self):
        """初始化监控引擎"""
        self.db = get_db()
        self.jupiter_api = JupiterAPI(timeout=10, max_retries=3)
        self.gmgn_api = get_gmgn_api()  # 添加 GMGN API
        self.notification_service = NotificationService(
            telegram_enabled=True,
            telegram_chat_id=-1002569554228,
            wechat_enabled=False
        )
        self.alert_recorder = AlertRecorder()  # 用于数据库和WebSocket推送
        self.redis_client: Optional[aioredis.Redis] = None
    
    async def init_redis(self):
        """初始化Redis连接"""
        if not self.redis_client:
            self.redis_client = await aioredis.from_url(
                f"redis://:{REDIS_CONFIG['password']}@{REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}/{REDIS_CONFIG['db']}",
                decode_responses=True
            )
            logger.info("✅ Redis连接已初始化")
    
    async def close_redis(self):
        """关闭Redis连接"""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
            logger.info("🔒 Redis连接已关闭")
    
    async def convert_gmgn_to_stats5m(self, gmgn_data: Dict[str, Any], ca: str) -> Optional[Dict[str, Any]]:
        """
        将 GMGN API 数据转换为 stats5m 格式
        
        Args:
            gmgn_data: GMGN API 解析后的数据
            ca: Token 地址（用于 Redis 缓存）
            
        Returns:
            stats5m 格式的数据
        """
        try:
            price = gmgn_data['price']
            price_5m = gmgn_data['price_5m']
            price_1h = gmgn_data['price_1h']
            
            # 计算价格变化百分比
            price_5m_change = 0
            price_1h_change = 0
            
            if price_5m and price_5m > 0:
                price_5m_change = ((price - price_5m) / price_5m) * 100
            
            if price_1h and price_1h > 0:
                price_1h_change = ((price - price_1h) / price_1h) * 100
            
            # 获取当前数据
            current_holder = gmgn_data['holder_count']
            current_volume_5m = gmgn_data['volume_5m']
            current_volume_1h = gmgn_data['volume_1h']
            
            # 从 Redis 获取历史数据
            holder_5m_ago = await self.redis_client.get(f"holder:5m:{ca}")
            holder_1h_ago = await self.redis_client.get(f"holder:1h:{ca}")
            volume_5m_ago = await self.redis_client.get(f"volume:5m:{ca}")
            volume_1h_ago = await self.redis_client.get(f"volume:1h:{ca}")
            
            # 计算持有人变化（转换为百分比）
            holder_5m_change = 0
            holder_1h_change = 0
            holder_5m_absolute = 0  # 绝对值，用于日志
            holder_1h_absolute = 0
            
            if holder_5m_ago:
                old_holder = int(holder_5m_ago)
                if old_holder > 0:
                    holder_5m_absolute = current_holder - old_holder
                    holder_5m_change = ((current_holder - old_holder) / old_holder) * 100  # 百分比
                    logger.info(f"   📊 持有人5分钟前: {old_holder} → 当前: {current_holder} (变化: {holder_5m_absolute:+d}, {holder_5m_change:+.2f}%)")
            else:
                logger.info(f"   📊 持有人5分钟前: 无缓存 → 当前: {current_holder}")
            
            if holder_1h_ago:
                old_holder = int(holder_1h_ago)
                if old_holder > 0:
                    holder_1h_absolute = current_holder - old_holder
                    holder_1h_change = ((current_holder - old_holder) / old_holder) * 100  # 百分比
                    logger.info(f"   📊 持有人1小时前: {old_holder} → 当前: {current_holder} (变化: {holder_1h_absolute:+d}, {holder_1h_change:+.2f}%)")
            else:
                logger.info(f"   📊 持有人1小时前: 无缓存 → 当前: {current_holder}")
            
            # 计算交易量变化
            volume_5m_change = 0
            volume_1h_change = 0
            if volume_5m_ago:
                old_volume = float(volume_5m_ago)
                if old_volume > 0:
                    volume_5m_change = ((current_volume_5m - old_volume) / old_volume) * 100
                    logger.info(f"   📊 交易量5分钟前: ${old_volume:,.2f} → 当前: ${current_volume_5m:,.2f} (变化: {volume_5m_change:+.2f}%)")
            else:
                logger.info(f"   📊 交易量5分钟前: 无缓存 → 当前: ${current_volume_5m:,.2f}")
            
            if volume_1h_ago:
                old_volume = float(volume_1h_ago)
                if old_volume > 0:
                    volume_1h_change = ((current_volume_1h - old_volume) / old_volume) * 100
                    logger.info(f"   📊 交易量1小时前: ${old_volume:,.2f} → 当前: ${current_volume_1h:,.2f} (变化: {volume_1h_change:+.2f}%)")
            else:
                logger.info(f"   📊 交易量1小时前: 无缓存 → 当前: ${current_volume_1h:,.2f}")
            
            # 保存当前数据到 Redis（5分钟过期）
            await self.redis_client.set(f"holder:5m:{ca}", str(current_holder), ex=300)
            await self.redis_client.set(f"holder:1h:{ca}", str(current_holder), ex=3600)
            await self.redis_client.set(f"volume:5m:{ca}", str(current_volume_5m), ex=300)
            await self.redis_client.set(f"volume:1h:{ca}", str(current_volume_1h), ex=3600)
            
            # 构造 stats5m 格式（字段名需要与 TriggerLogic 保持一致）
            stats5m = {
                'price': price,
                'price_5m_change_percent': price_5m_change,
                'price_1h_change_percent': price_1h_change,
                'priceChange': price_5m_change,  # TriggerLogic 使用这个字段
                'volume_5m': current_volume_5m,
                'volume_1h': current_volume_1h,
                'volume_5m_change_percent': volume_5m_change,
                'volume_1h_change_percent': volume_1h_change,
                'volumeChange': volume_5m_change,  # TriggerLogic 使用这个字段
                'buys_5m': gmgn_data['buys_5m'],
                'sells_5m': gmgn_data['sells_5m'],
                'swaps_5m': gmgn_data['swaps_5m'],
                'liquidity': gmgn_data['liquidity'],
                'holder_count': current_holder,
                'holder_5m_change': holder_5m_change,
                'holder_1h_change': holder_1h_change,
                'holderChange': holder_5m_change,  # TriggerLogic 使用这个字段
            }
            
            return stats5m
            
        except Exception as e:
            logger.error(f"❌ 转换 GMGN 数据失败: {e}")
            return None
    
    def get_monitor_configs(self) -> List[Dict[str, Any]]:
        """
        查询启用的监控配置
        
        Returns:
            监控配置列表
        """
        try:
            sql = """
            SELECT id, ca, token_name, token_symbol, events_config, 
                   trigger_logic, notify_methods, remark, timer_interval
            FROM token_monitor_config
            WHERE status = '1' AND del_flag = '0'
            ORDER BY id ASC
            """
            
            result = self.db.execute_query(sql)
            return result if result else []
            
        except Exception as e:
            logger.error(f"❌ 查询监控配置失败: {e}")
            return []
    
    async def check_cooldown(self, ca: str, events_config_str: str) -> bool:
        """
        检查是否在冷却期内
        
        Args:
            ca: Token合约地址
            events_config_str: 事件配置JSON字符串
            
        Returns:
            True=在冷却期, False=可以发送
        """
        # 生成唯一key
        config_hash = hashlib.md5(events_config_str.encode()).hexdigest()[:8]
        key = f"alert:{ca}:{config_hash}"
        
        exists = await self.redis_client.exists(key)
        return bool(exists)
    
    async def set_cooldown(self, ca: str, events_config_str: str, cooldown_seconds: int = 1800):
        """
        设置冷却期
        
        Args:
            ca: Token合约地址
            events_config_str: 事件配置JSON字符串
            cooldown_seconds: 冷却时长（秒），默认30分钟
        """
        config_hash = hashlib.md5(events_config_str.encode()).hexdigest()[:8]
        key = f"alert:{ca}:{config_hash}"
        
        await self.redis_client.setex(key, cooldown_seconds, "1")
        logger.debug(f"🔒 设置冷却期: {key} ({cooldown_seconds}秒)")
    
    async def _send_sol_alert(
        self,
        config_id: int,
        ca: str,
        token_name: str,
        token_symbol: str,
        triggered_events: List[TriggerEvent],
        stats_data: Dict[str, Any],
        notify_methods: str
    ):
        """
        发送 SOL 链预警（数据库 + WebSocket）
        
        Args:
            config_id: 监控配置ID
            ca: Token合约地址
            token_name: Token名称
            token_symbol: Token符号
            triggered_events: 触发的事件列表
            stats_data: stats5m 数据
            notify_methods: 通知方式
        """
        try:
            # 获取价格、涨幅等信息
            price = stats_data.get('price', 0)
            price_change = stats_data.get('price_5m_change_percent', 0)
            volume_24h = stats_data.get('volume', 0)
            holders = stats_data.get('holder', 0)
            market_cap = stats_data.get('market_cap', 0)
            
            # 如果没有市值，用流动性代替
            if not market_cap:
                market_cap = stats_data.get('liquidity', 0)
            
            # 调用 alert_recorder 写入数据库和推送 WebSocket
            await self.alert_recorder.write_sol_alert(
                config_id=config_id,
                ca=ca,
                token_name=token_name,
                token_symbol=token_symbol,
                alert_reasons=[event.description for event in triggered_events],
                price=price,
                price_change=price_change,
                market_cap=market_cap,
                volume_24h=volume_24h,
                holders=holders,
                logo=stats_data.get('logo', ''),
                notify_methods=notify_methods
            )
            
            logger.info(f"   ✅ 已写入数据库并推送到 WebSocket")
            
        except Exception as e:
            logger.error(f"   ❌ 发送SOL预警失败: {e}", exc_info=True)
    
    def save_alert_log(
        self,
        config_id: int,
        ca: str,
        token_name: str,
        token_symbol: str,
        triggered_events: List[TriggerEvent],
        stats_data: Dict[str, Any],
        notify_methods: str,
        notify_results: Dict[str, bool]
    ) -> bool:
        """
        保存触发日志到数据库
        
        Args:
            config_id: 监控配置ID
            ca: Token合约地址
            token_name: Token名称
            token_symbol: Token符号
            triggered_events: 触发的事件列表
            stats_data: stats5m 数据
            notify_methods: 通知方式
            notify_results: 通知结果
            
        Returns:
            是否保存成功
        """
        try:
            # 转换触发事件为JSON（保留中文字符）
            trigger_events_json = json.dumps([e.to_dict() for e in triggered_events], ensure_ascii=False)
            
            # 转换stats数据为JSON（保留中文字符）
            stats_json = json.dumps(stats_data, ensure_ascii=False)
            
            # 判断通知状态
            if all(notify_results.values()):
                notify_status = "success"
                notify_error = None
            elif any(notify_results.values()):
                notify_status = "partial"
                notify_error = f"部分失败: {notify_results}"
            else:
                notify_status = "failed"
                notify_error = "所有通知方式都失败"
            
            sql = """
            INSERT INTO token_monitor_alert_log
            (config_id, ca, token_name, token_symbol, trigger_time,
             trigger_events, stats_data, notify_methods, notify_status, notify_error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            params = (
                config_id, ca, token_name, token_symbol, datetime.now(),
                trigger_events_json, stats_json, notify_methods,
                notify_status, notify_error
            )
            
            rowcount = self.db.execute_update(sql, params)
            if rowcount > 0:
                logger.info(f"✅ 保存触发日志成功: config_id={config_id}")
                return True
            else:
                logger.warning(f"⚠️ 保存触发日志失败: rowcount=0")
                return False
                
        except Exception as e:
            logger.error(f"❌ 保存触发日志异常: {e}")
            return False
    
    def update_notification_stats(self, config_id: int) -> bool:
        """
        更新监控配置的通知统计
        
        Args:
            config_id: 监控配置ID
            
        Returns:
            是否更新成功
        """
        try:
            sql = """
            UPDATE token_monitor_config
            SET last_notification_time = %s,
                notification_count = notification_count + 1,
                update_time = %s
            WHERE id = %s
            """
            
            params = (datetime.now(), datetime.now(), config_id)
            rowcount = self.db.execute_update(sql, params)
            
            return rowcount > 0
            
        except Exception as e:
            logger.error(f"❌ 更新通知统计失败: {e}")
            return False
    
    async def monitor_single_token(
        self,
        config: Dict[str, Any],
        token_data: Dict[str, Any]
    ) -> bool:
        """
        监控单个Token
        
        Args:
            config: 监控配置
            token_data: Jupiter API返回的Token数据
            
        Returns:
            是否触发并发送通知
        """
        config_id = config['id']
        ca = config['ca']
        token_name = config.get('token_name') or token_data.get('name', 'Unknown')
        token_symbol = config.get('token_symbol') or token_data.get('symbol', 'Unknown')
        
        # 打印Token基本信息
        logger.info(f"\n{'─'*80}")
        logger.info(f"🔍 监控 Token: {token_name} ({token_symbol})")
        logger.info(f"   地址: {ca}")
        
        # 获取stats5m数据
        stats = token_data.get('stats5m')
        if not stats:
            logger.warning(f"   ⏭️ 无stats5m数据，跳过")
            return False
        
        # 打印实时数据
        try:
            price = float(stats.get('price', 0))
            price_5m_change = float(stats.get('price_5m_change_percent', 0))
            price_1h_change = float(stats.get('price_1h_change_percent', 0))
            
            logger.info(f"   💰 当前价格: ${price:.8f}")
            logger.info(f"   📈 价格变化: 5分钟 {price_5m_change:+.2f}% | 1小时 {price_1h_change:+.2f}%")
            # 交易量和持有人的详细变化已经在 convert_gmgn_to_stats5m 中打印了
        except Exception as e:
            logger.debug(f"   ⚠️  打印实时数据失败: {e}")
        
        # 解析事件配置
        events_config_str = config['events_config']
        events_config = TriggerLogic.parse_events_config(events_config_str)
        if not events_config:
            logger.warning(f"   ⚠️ 解析events_config失败")
            return False
        
        # 打印监控条件
        logger.info(f"   🎯 监控条件:")
        for event_type, threshold in events_config.items():
            logger.info(f"      - {event_type}: {threshold}")
        
        # 触发判断
        trigger_logic = config['trigger_logic']
        should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
            stats, events_config, trigger_logic
        )
        
        if not should_trigger:
            logger.info(f"   ✅ 未触发条件（触发逻辑: {trigger_logic}）")
            return False
        
        # 打印触发的事件
        logger.info(f"   🚨 触发监控！触发逻辑: {trigger_logic}")
        for event in triggered_events:
            logger.info(f"      ✓ {event.type}: 当前值={event.value}, 阈值={event.threshold}")
        
        # 检查冷却期
        in_cooldown = await self.check_cooldown(ca, events_config_str)
        if in_cooldown:
            logger.info(f"   ⏸️ 在冷却期内，跳过通知")
            return False
        
        # 发送通知
        notify_methods = config['notify_methods'].split(',')
        notification_msg = NotificationMessage(
            ca=ca,
            token_name=token_name,
            token_symbol=token_symbol,
            triggered_events=triggered_events,  # 直接传TriggerEvent对象列表
            token_data=token_data,  # 传入完整的token数据
            remark=config.get('remark', '')
        )
        
        notify_results = await self.notification_service.send_alert(
            notification_msg, notify_methods
        )
        
        # 设置冷却期
        await self.set_cooldown(ca, events_config_str, cooldown_seconds=1800)
        
        # 保存日志到数据库并推送到WebSocket
        await self._send_sol_alert(
            config_id, ca, token_name, token_symbol,
            triggered_events, stats, notify_methods
        )
        
        # 保存日志（仅用于统计）
        self.save_alert_log(
            config_id, ca, token_name, token_symbol,
            triggered_events, stats, config['notify_methods'], notify_results
        )
        
        # 更新统计
        self.update_notification_stats(config_id)
        
        return True
    
    async def run_monitor_once(self) -> Dict[str, int]:
        """
        执行一次监控任务
        
        Returns:
            统计信息
        """
        logger.info("=" * 80)
        logger.info("🚀 开始执行Token监控任务")
        logger.info("=" * 80)
        
        start_time = datetime.now()
        
        # 初始化Redis
        await self.init_redis()
        
        # 查询监控配置
        configs = self.get_monitor_configs()
        if not configs:
            logger.warning("⚠️ 没有启用的监控配置")
            await self.close_redis()
            return {'total': 0, 'triggered': 0, 'notified': 0}
        
        logger.info(f"📋 发现 {len(configs)} 个启用的监控配置")
        
        # 按链分组CA地址（通过查询 token_launch_history 表获取 source）
        ca_list = [config['ca'] for config in configs]
        ca_source_map = {}
        
        try:
            if ca_list:
                placeholders = ','.join(['%s'] * len(ca_list))
                sql = f"SELECT ca, source FROM token_launch_history WHERE ca IN ({placeholders})"
                results = self.db.execute_query(sql, tuple(ca_list))
                
                if results:
                    for row in results:
                        # row 可能是字典或元组，兼容处理
                        if isinstance(row, dict):
                            ca_source_map[row['ca']] = row['source']
                        else:
                            # 元组形式: (ca, source)
                            ca_source_map[row[0]] = row[1]
        except Exception as e:
            logger.error(f"❌ 查询Token来源失败: {e}", exc_info=True)
        
        # 分组
        sol_tokens = []
        bsc_tokens = []
        
        for ca in ca_list:
            source = ca_source_map.get(ca, '')
            if source in ['pump', 'bonk']:
                sol_tokens.append(ca)
            elif source in ['fourmeme', 'fourmeme_tg']:
                bsc_tokens.append(ca)
            else:
                # 默认尝试用 Jupiter API
                sol_tokens.append(ca)
        
        logger.info(f"📊 Token分布: SOL={len(sol_tokens)}, BSC={len(bsc_tokens)}")
        
        # 批量获取Token数据
        tokens_data = {}
        
        # 统一使用 GMGN API 获取所有链的数据
        # 1. 获取 Solana 链数据
        if sol_tokens:
            logger.info(f"🔍 使用 GMGN API 查询 {len(sol_tokens)} 个 Solana Token...")
            batch_size = 5  # 每批 5 个
            for i in range(0, len(sol_tokens), batch_size):
                batch = sol_tokens[i:i + batch_size]
                gmgn_data_list = self.gmgn_api.get_token_info_batch('sol', batch)
                
                if gmgn_data_list:
                    for token_data in gmgn_data_list:
                        parsed_data = self.gmgn_api.parse_token_data(token_data)
                        if parsed_data:
                            ca = parsed_data['address']
                            # 转换为 stats5m 格式（传入 ca 用于 Redis 缓存）
                            stats5m = await self.convert_gmgn_to_stats5m(parsed_data, ca)
                            if stats5m:
                                # 构造与 Jupiter API 相同的数据结构
                                tokens_data[ca] = {
                                    'address': ca,
                                    'symbol': parsed_data['symbol'],
                                    'name': parsed_data['name'],
                                    'stats5m': stats5m,
                                    'source': 'gmgn'
                                }
                
                await asyncio.sleep(0.5)  # 避免请求过快
        
        # 2. 获取 BSC 链数据
        if bsc_tokens:
            logger.info(f"🔍 使用 GMGN API 查询 {len(bsc_tokens)} 个 BSC Token...")
            batch_size = 5  # 每批 5 个
            for i in range(0, len(bsc_tokens), batch_size):
                batch = bsc_tokens[i:i + batch_size]
                gmgn_data_list = self.gmgn_api.get_token_info_batch('bsc', batch)
                
                if gmgn_data_list:
                    # 记录返回的地址
                    returned_addresses = set(token_data.get('address') for token_data in gmgn_data_list)
                    
                    # 检查哪些地址没有返回数据
                    for ca in batch:
                        if ca.lower() not in [addr.lower() for addr in returned_addresses]:
                            logger.warning(f"⚠️  BSC Token {ca[:10]}... 在 GMGN 中查询不到数据（可能已过期或不存在）")
                    
                    for token_data in gmgn_data_list:
                        parsed_data = self.gmgn_api.parse_token_data(token_data)
                        if parsed_data:
                            ca = parsed_data['address']
                            # 转换为 stats5m 格式（传入 ca 用于 Redis 缓存）
                            stats5m = await self.convert_gmgn_to_stats5m(parsed_data, ca)
                            if stats5m:
                                # 构造与 Jupiter API 相同的数据结构
                                tokens_data[ca] = {
                                    'address': ca,
                                    'symbol': parsed_data['symbol'],
                                    'name': parsed_data['name'],
                                    'stats5m': stats5m,
                                    'source': 'gmgn'
                                }
                else:
                    # API 返回空或失败
                    for ca in batch:
                        logger.warning(f"⚠️  BSC Token {ca[:10]}... API查询失败或返回空数据")
                
                await asyncio.sleep(0.5)  # 避免请求过快
        
        # 逐个判断触发
        triggered_count = 0
        notified_count = 0
        
        for config in configs:
            ca = config['ca']
            # BSC 地址不区分大小写，需要统一转换为小写查找
            lookup_ca = ca.lower() if ca.startswith('0x') else ca
            token_data = tokens_data.get(lookup_ca) or tokens_data.get(ca)
            
            if not token_data:
                logger.warning(f"⏭️ Token {ca[:8]}... 未获取到数据，跳过")
                continue
            
            try:
                is_notified = await self.monitor_single_token(config, token_data)
                if is_notified:
                    notified_count += 1
                    triggered_count += 1
                else:
                    # 可能触发了但在冷却期
                    pass
            except Exception as e:
                logger.error(f"❌ 监控Token {ca[:8]}... 异常: {e}", exc_info=True)
        
        # 关闭Redis
        await self.close_redis()
        
        # 统计
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        logger.info("\n" + "=" * 80)
        logger.info("📊 监控任务完成")
        logger.info(f"   配置总数: {len(configs)}")
        logger.info(f"   获取数据: {len([v for v in tokens_data.values() if v])}/{len(configs)}")
        logger.info(f"   触发监控: {triggered_count}")
        logger.info(f"   发送通知: {notified_count}")
        logger.info(f"   耗时: {duration:.2f}秒")
        logger.info("=" * 80)
        
        return {
            'total': len(configs),
            'triggered': triggered_count,
            'notified': notified_count
        }
    
    async def run_monitor_schedule(self, interval_minutes: int = 1):
        """
        定时执行监控任务
        
        Args:
            interval_minutes: 执行间隔（分钟），默认1分钟
        """
        interval_seconds = interval_minutes * 60
        logger.info(f"🔄 启动定时监控任务，执行间隔: {interval_minutes}分钟")
        
        try:
            while True:
                try:
                    await self.run_monitor_once()
                except Exception as e:
                    logger.error(f"❌ 监控任务执行失败: {e}", exc_info=True)
                
                # 等待下次执行
                logger.info(f"\n⏰ 等待 {interval_minutes} 分钟后执行下一次监控...\n")
                await asyncio.sleep(interval_seconds)
                
        except KeyboardInterrupt:
            logger.info("\n⏹️  用户停止定时监控任务")
        except Exception as e:
            logger.error(f"❌ 定时监控任务异常退出: {e}", exc_info=True)

