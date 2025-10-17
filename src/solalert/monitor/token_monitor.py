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
import redis.asyncio as aioredis

logger = get_logger(__name__)


class TokenMonitorEngine:
    """Token监控引擎"""
    
    def __init__(self):
        """初始化监控引擎"""
        self.db = get_db()
        self.jupiter_api = JupiterAPI(timeout=10, max_retries=3)
        self.notification_service = NotificationService(
            telegram_enabled=True,
            telegram_chat_id=-1002926135363,
            wechat_enabled=False
        )
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
            # 转换触发事件为JSON
            trigger_events_json = json.dumps([e.to_dict() for e in triggered_events])
            
            # 转换stats数据为JSON
            stats_json = json.dumps(stats_data)
            
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
        
        # 获取stats5m数据
        stats = token_data.get('stats5m')
        if not stats:
            logger.warning(f"⏭️ Token {ca[:8]}... 无stats5m数据，跳过")
            return False
        
        # 解析事件配置
        events_config_str = config['events_config']
        events_config = TriggerLogic.parse_events_config(events_config_str)
        if not events_config:
            logger.warning(f"⚠️ 解析events_config失败: config_id={config_id}")
            return False
        
        # 触发判断
        trigger_logic = config['trigger_logic']
        should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
            stats, events_config, trigger_logic
        )
        
        if not should_trigger:
            logger.debug(f"⏭️ Token {ca[:8]}... 未触发条件")
            return False
        
        logger.info(f"🚨 Token {ca[:8]}... 触发监控！事件数: {len(triggered_events)}")
        
        # 检查冷却期
        in_cooldown = await self.check_cooldown(ca, events_config_str)
        if in_cooldown:
            logger.info(f"⏸️ Token {ca[:8]}... 在冷却期内，跳过通知")
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
        
        # 保存日志
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
        
        # 提取所有CA地址
        ca_list = [config['ca'] for config in configs]
        
        # 批量获取Token数据
        async with self.jupiter_api as api:
            tokens_data = await api.get_multiple_tokens(ca_list, delay=0.1)
        
        # 逐个判断触发
        triggered_count = 0
        notified_count = 0
        
        for config in configs:
            ca = config['ca']
            token_data = tokens_data.get(ca)
            
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

