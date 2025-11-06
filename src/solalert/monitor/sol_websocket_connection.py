"""
SOL WebSocket 连接管理类
负责WebSocket连接、订阅、消息接收和处理
"""
import asyncio
import websockets
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from .sol_alert_checker import SolAlertChecker
from .sol_field_mapper import SolFieldMapper

logger = logging.getLogger(__name__)


class SolWebSocketConnection:
    """
    单个WebSocket连接
    负责连接管理、批次订阅、消息处理
    """
    
    def __init__(
        self,
        ws_url: str,
        api_key: str,
        redis_client,
        db_manager,
        notification_service,
        alert_recorder
    ):
        """
        初始化WebSocket连接
        
        Args:
            ws_url: WebSocket URL
            api_key: DBotX API Key
            redis_client: Redis客户端
            db_manager: 数据库管理器
            notification_service: 通知服务（Telegram/微信）
            alert_recorder: 告警记录器
        """
        self.ws_url = ws_url
        self.api_key = api_key
        self.redis = redis_client
        self.db = db_manager
        self.notification_service = notification_service
        self.alert_recorder = alert_recorder
        
        # WebSocket连接
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        
        # 批次配置
        self.batch_configs: List[Dict[str, Any]] = []
        self.config_cache: Dict[str, Dict[str, Any]] = {}  # {pair_address: config}
        
        # 告警检查器
        self.alert_checker = SolAlertChecker(redis_client)
        
        # 统计信息
        self.message_count = 0
        self.alert_count = 0
        self.error_count = 0
        self.start_time = None
        
        # 运行状态
        self.running = False
    
    async def initialize(self):
        """初始化：从数据库加载配置"""
        logger.info("=" * 80)
        logger.info("初始化WebSocket连接")
        logger.info("=" * 80)
        
        # 从数据库加载所有批次配置
        query = """
            SELECT * FROM sol_ws_batch_pool
            WHERE is_active = 1
            ORDER BY batch_id, sort_order
        """
        
        self.batch_configs = self.db.execute_query(query)
        
        if not self.batch_configs:
            logger.error("❌ 未找到活跃的批次配置")
            return False
        
        logger.info(f"✅ 成功加载 {len(self.batch_configs)} 个CA配置")
        
        # 统计批次数量
        batch_ids = set(config['batch_id'] for config in self.batch_configs)
        logger.info(f"📦 批次数量: {len(batch_ids)}")
        
        # 加载配置到内存缓存
        for config in self.batch_configs:
            pair = config['pair_address']
            self.config_cache[pair] = config
        
        logger.info(f"✅ 配置已加载到内存缓存: {len(self.config_cache)} 个")
        logger.info("")
        
        return True
    
    async def connect(self):
        """连接WebSocket"""
        logger.info("=" * 80)
        logger.info("连接WebSocket")
        logger.info("=" * 80)
        logger.info(f"URL: {self.ws_url}")
        
        try:
            self.ws = await websockets.connect(
                self.ws_url,
                additional_headers={'x-api-key': self.api_key},
                ping_interval=30,  # 每30秒自动ping
                ping_timeout=10,   # ping超时时间
            )
            
            logger.info("✅ WebSocket连接成功")
            logger.info("")
            return True
            
        except Exception as e:
            logger.error(f"❌ WebSocket连接失败: {e}")
            return False
    
    async def subscribe_all_batches(self):
        """订阅所有批次"""
        logger.info("=" * 80)
        logger.info("订阅所有批次")
        logger.info("=" * 80)
        
        # 按batch_id分组
        batches = {}
        for config in self.batch_configs:
            batch_id = config['batch_id']
            if batch_id not in batches:
                batches[batch_id] = []
            batches[batch_id].append(config)
        
        logger.info(f"总批次数: {len(batches)}")
        logger.info("")
        
        # 逐个批次订阅
        for batch_id in sorted(batches.keys()):
            await self._subscribe_batch(batch_id, batches[batch_id])
            await asyncio.sleep(0.5)  # 批次间延迟0.5秒
        
        logger.info("✅ 所有批次订阅完成")
        logger.info("")
    
    async def _subscribe_batch(self, batch_id: int, configs: List[Dict[str, Any]]):
        """
        订阅单个批次
        
        Args:
            batch_id: 批次ID
            configs: 该批次的配置列表
        """
        # 构造订阅消息
        pairs = []
        for config in configs:
            pairs.append({
                "pair": config['pair_address'],
                "token": config['ca']
            })
        
        subscribe_msg = {
            "method": "subscribe",
            "type": "pairsInfo",
            "args": {
                "pairsInfoInterval": "1m",  # 统一1m间隔
                "pairs": pairs
            }
        }
        
        # 发送订阅
        try:
            await self.ws.send(json.dumps(subscribe_msg))
            logger.info(f"✅ Batch {batch_id:2d}: 已订阅 {len(pairs)} 个pair")
            
        except Exception as e:
            logger.error(f"❌ Batch {batch_id:2d}: 订阅失败 - {e}")
            self.error_count += 1
    
    async def listen_messages(self):
        """监听并处理WebSocket消息"""
        logger.info("=" * 80)
        logger.info("开始监听消息")
        logger.info("=" * 80)
        logger.info("")
        
        self.running = True
        self.start_time = datetime.now()
        
        last_stats_time = datetime.now()
        last_heartbeat_time = datetime.now()
        
        try:
            while self.running:
                try:
                    # 接收消息（超时5秒）
                    message = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
                    self.message_count += 1
                    
                    # 处理消息
                    await self._handle_message(message)
                    
                    # 每60秒打印一次统计
                    now = datetime.now()
                    if (now - last_stats_time).total_seconds() >= 60:
                        self._print_stats()
                        last_stats_time = now
                    
                    # 每30秒打印一次心跳（表示系统还在运行）
                    if (now - last_heartbeat_time).total_seconds() >= 30:
                        elapsed = int((now - self.start_time).total_seconds())
                        logger.info(f"💓 心跳检测 | 运行: {elapsed}秒 | 消息: {self.message_count} | 告警: {self.alert_count}")
                        last_heartbeat_time = now
                    
                except asyncio.TimeoutError:
                    # 超时继续等待，但检查是否需要打印心跳
                    now = datetime.now()
                    if (now - last_heartbeat_time).total_seconds() >= 30:
                        elapsed = int((now - self.start_time).total_seconds())
                        logger.info(f"💓 心跳检测 | 运行: {elapsed}秒 | 消息: {self.message_count} | 告警: {self.alert_count}")
                        last_heartbeat_time = now
                    continue
                    
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("⚠️ WebSocket连接已关闭，尝试重连...")
                    if await self._reconnect():
                        continue
                    else:
                        break
                
                except Exception as e:
                    logger.error(f"❌ 处理消息出错: {e}", exc_info=True)
                    self.error_count += 1
                    
        except KeyboardInterrupt:
            logger.info("\n⚠️ 收到中断信号，正在停止...")
            
        finally:
            self.running = False
            await self.close()
    
    async def _handle_message(self, message: str):
        """
        处理WebSocket消息
        
        Args:
            message: WebSocket消息（JSON字符串）
        """
        try:
            data = json.loads(message)
            msg_type = data.get('type')

            logger.info(f"🔍 收到消息: {data}")
            
            # 订阅响应
            if data.get('method') == 'subscribeResponse':
                status = data.get('status')
                if status == 'ack':
                    logger.info("📨 订阅确认收到")
                return
            
            # pairsInfo数据
            if msg_type == 'pairsInfo':
                results = data.get('result', [])
                
                if results:
                    logger.info(f"📊 收到 {len(results)} 个pair的数据更新")
                
                for item in results:
                    await self._process_pair_data(item)
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {e}")
            
        except Exception as e:
            logger.error(f"❌ 处理消息失败: {e}", exc_info=True)
    
    async def _process_pair_data(self, data: Dict[str, Any]):
        """
        处理单个pair的数据
        
        Args:
            data: pair数据
        """
        pair = data.get('p')
        if not pair:
            return
        
        # 从缓存获取配置
        config = self.config_cache.get(pair)
        if not config:
            # 未找到配置，可能是测试数据
            logger.debug(f"⚠️ 收到未配置的pair数据: {pair[:10]}...")
            return
        
        # 提取基础信息用于日志
        ca = config.get('ca', '')
        token_symbol = config.get('token_symbol', 'Unknown')
        token_name = config.get('token_name', '')
        price = data.get('tp', 0)
        market_cap = data.get('mp', 0)
        
        # 提取价格变化（根据time_interval）
        time_interval = config.get('time_interval', '1m')
        from .sol_field_mapper import SolFieldMapper
        price_change = SolFieldMapper.extract_price_change(data, time_interval)
        
        # 提取交易量
        volume_data = SolFieldMapper.extract_volume(data, time_interval)
        total_volume = volume_data['total_volume']
        
        # 输出收到数据的详细日志
        logger.info(
            f"📨 {token_symbol:8s} | "
            f"价格: ${price:12.8f} | "
            f"市值: ${market_cap:12,.0f} | "
            f"变化: {price_change:+7.2f}% | "
            f"量: ${total_volume:10,.0f} | "
            f"CA: {ca}"
        )
        
        # 检查告警条件
        should_alert, reasons, metrics = self.alert_checker.check_alert_conditions(
            data, config
        )
        
        if should_alert:
            await self._send_alert(config, metrics, reasons, pair)
    
    async def _send_alert(
        self,
        config: Dict[str, Any],
        metrics: Dict[str, Any],
        reasons: List[str],
        pair_address: str = None
    ):
        """
        发送告警
        
        Args:
            config: CA配置
            metrics: 监控指标
            reasons: 触发原因
            pair_address: Pair地址（用于AXIOM按钮）
        """
        ca = config['ca']
        token_symbol = config.get('token_symbol', 'Unknown')
        template_name = config.get('template_name', '未知')
        
        # 🚀 显示配置信息
        config_info = self.alert_checker.format_config_summary(config)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"🔔 告警触发！")
        logger.info(f"{'='*80}")
        logger.info(f"Token: {token_symbol} ({ca[:10]}...{ca[-6:]})")
        logger.info(f"📋 模板: {template_name}")
        logger.info(f"⚙️  配置: {config_info}")
        logger.info(f"价格变化: {metrics['price_change']:+.2f}%")
        logger.info(f"交易量: ${metrics['total_volume']:,.0f}")
        logger.info(f"✨ 触发原因:")
        for reason in reasons:
            logger.info(f"  • {reason}")
        logger.info(f"{'='*80}\n")
        
        try:
            # 格式化告警消息
            message = self.alert_checker.format_alert_message(config, metrics, reasons)
            
            # 推送告警
            telegram_success = False
            wechat_success = False
            telegram_msg_id = None
            wechat_msg_id = None
            telegram_error = None
            wechat_error = None
            
            # Telegram推送（使用token_monitor.py中的频道ID，带按钮）
            try:
                # 直接使用telegram client推送到指定频道
                from ..notifiers.telegram import TelegramNotifier
                telegram = TelegramNotifier()
                
                # 创建按钮（传入pair地址用于AXIOM）
                buttons = self.alert_checker.create_sol_buttons(ca, pair_address)
                
                # 使用send方法（异步），target参数传入频道ID
                result = await telegram.send(
                    target=-1003291885712,  # token_monitor.py中的频道ID
                    message=message,
                    parse_mode=None,  # 纯文本
                    reply_markup=buttons  # 添加按钮
                )
                telegram_success = result
                telegram_msg_id = "sent" if result else None
                
                if result:
                    logger.info(f"✅ Telegram推送成功（含按钮）")
                else:
                    logger.warning(f"⚠️ Telegram推送失败")
                
            except Exception as e:
                logger.error(f"❌ Telegram推送异常: {e}", exc_info=True)
                telegram_error = str(e)
            
            # 微信推送（暂时跳过）
            wechat_success = False
            wechat_msg_id = None
            wechat_error = "Not implemented"
            
            # 记录告警日志
            await self._save_alert_log(
                config, metrics, reasons,
                telegram_success, telegram_msg_id, telegram_error,
                wechat_success, wechat_msg_id, wechat_error
            )
            
            # 设置冷却期
            self.alert_checker.set_cooldown(ca)
            
            # 更新统计
            self.alert_count += 1
            
        except Exception as e:
            logger.error(f"发送告警失败: {e}", exc_info=True)
    
    async def _save_alert_log(
        self,
        config: Dict[str, Any],
        metrics: Dict[str, Any],
        reasons: List[str],
        telegram_success: bool,
        telegram_msg_id: Optional[str],
        telegram_error: Optional[str],
        wechat_success: bool,
        wechat_msg_id: Optional[str],
        wechat_error: Optional[str]
    ):
        """保存告警日志到数据库"""
        try:
            insert_sql = """
                INSERT INTO sol_ws_alert_log (
                    batch_id, ca, token_symbol, token_name, pair_address,
                    template_id, template_name,
                    price, market_cap, price_change,
                    price_change_1m, price_change_5m, price_change_1h,
                    volume_1h, buy_volume_1h, sell_volume_1h,
                    txs_1h, buy_txs_1h, sell_txs_1h,
                    top10_percent, trigger_reasons,
                    trigger_time_interval, trigger_logic,
                    telegram_sent, telegram_success, telegram_message_id, telegram_error,
                    wechat_sent, wechat_success, wechat_message_id, wechat_error,
                    alert_time
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    NOW()
                )
            """
            
            # 获取不同时间窗口的价格变化（用于记录）
            time_interval = config.get('time_interval', '1m')
            
            self.db.execute_update(insert_sql, (
                config['batch_id'], config['ca'], config.get('token_symbol'),
                config.get('token_name'), config['pair_address'],
                config['template_id'], config.get('template_name'),
                metrics['price'], metrics['market_cap'], metrics['price_change'],
                metrics['price_change'], 0, 0,  # 暂时只记录当前interval的变化
                metrics['total_volume'], metrics['buy_volume'], metrics['sell_volume'],
                metrics['total_txs'], metrics['buy_txs'], metrics['sell_txs'],
                metrics['top10_percent'], json.dumps(reasons),
                time_interval, config.get('trigger_logic'),
                1, int(telegram_success), telegram_msg_id, telegram_error,
                1, int(wechat_success), wechat_msg_id, wechat_error
            ))
            
        except Exception as e:
            logger.error(f"保存告警日志失败: {e}", exc_info=True)
    
    async def _reconnect(self, max_retries: int = 5) -> bool:
        """
        重连WebSocket
        
        Args:
            max_retries: 最大重试次数
        
        Returns:
            是否重连成功
        """
        for i in range(max_retries):
            try:
                logger.info(f"尝试重连 ({i+1}/{max_retries})...")
                
                await asyncio.sleep(2 ** i)  # 指数退避
                
                if await self.connect():
                    await self.subscribe_all_batches()
                    logger.info("✅ 重连成功")
                    return True
                    
            except Exception as e:
                logger.error(f"重连失败: {e}")
        
        logger.error(f"❌ 重连失败，已尝试 {max_retries} 次")
        return False
    
    def _print_stats(self):
        """打印统计信息"""
        if not self.start_time:
            return
        
        elapsed = (datetime.now() - self.start_time).total_seconds()
        rate = self.message_count / elapsed if elapsed > 0 else 0
        
        logger.info(f"\n{'='*80}")
        logger.info(f"📊 运行统计")
        logger.info(f"{'='*80}")
        logger.info(f"运行时长: {int(elapsed)}秒 ({elapsed/60:.1f}分钟)")
        logger.info(f"消息总数: {self.message_count}")
        logger.info(f"消息速率: {rate:.2f} 条/秒")
        logger.info(f"告警次数: {self.alert_count}")
        logger.info(f"错误次数: {self.error_count}")
        logger.info(f"{'='*80}\n")
    
    async def close(self):
        """关闭WebSocket连接"""
        self.running = False
        
        if self.ws:
            try:
                await self.ws.close()
                logger.info("✅ WebSocket连接已关闭")
            except:
                pass
        
        # 打印最终统计
        self._print_stats()
    
    async def run(self):
        """运行WebSocket监控（完整流程）"""
        try:
            # 1. 初始化
            if not await self.initialize():
                return
            
            # 2. 连接
            if not await self.connect():
                return
            
            # 3. 订阅
            await self.subscribe_all_batches()
            
            # 4. 监听消息
            await self.listen_messages()
            
        except Exception as e:
            logger.error(f"运行出错: {e}", exc_info=True)
            
        finally:
            await self.close()

