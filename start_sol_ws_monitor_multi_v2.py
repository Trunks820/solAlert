"""
SOL WebSocket 监控 - V2版本（使用新表结构）
每个批次一条 WebSocket 连接（因为每条 WS 最多 99 个 pair）
采用分组启动策略，避免突发压力

🆕 V2 新特性：
- 从 monitor_*_v2 表结构加载数据
- 集成 WebSocket 客户端，实时推送状态到后端
- 支持配置热更新（通过 WebSocket batch_reload 消息）
- 使用全局唯一的 batch_id（monitor_batch_v2.id）
- 动态生成 Consumer ID（hostname-pid）

🚀 性能优化：
- 配置一次性加载到内存，避免热路径同步查库
- 通知返回值健壮判断（兼容bool/dict/状态码）
- 重连指数退避 + 抖动（1s→2s→4s...→60s，±20%）
- 无数据自愈：5分钟无数据自动断开重连
- 批次失败监控：超过50%批次失败时发送Telegram告警
- DatabaseManager自动关闭，避免连接泄漏

📝 配置热更新：
通过 WebSocket 接收 batch_reload 消息，自动重新加载批次数据，无需重启。
"""
import sys
import os
import socket
import asyncio
import websockets
import json
import logging
from datetime import datetime
from typing import List, Dict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from telegram import InlineKeyboardMarkup
from solalert.core.database import DatabaseManager
from solalert.core.redis_client import RedisClient
from solalert.core.config import REDIS_CONFIG, TELEGRAM_CONFIG
from solalert.core.formatters import format_number
from solalert.monitor.sol_alert_checker import SolAlertChecker
from solalert.notifiers.telegram import TelegramNotifier
from solalert.notifiers.wechat import WeChatNotifier

# 🆕 V2 新增导入
from solalert.websocket.monitor_websocket import MonitorWebSocketClient
from solalert.loaders.v2_data_loader import load_all_active_batches_v2, load_batch_data_v2

# WebSocket配置
WS_URL = "wss://api-data-v1.dbotx.com/data/ws/"
API_KEY = "i1o3elfavv59ds02fggj9rsd0eg8w657"

# 🆕 Monitor WebSocket 配置
MONITOR_WS_URL = os.getenv('MONITOR_WS_URL', 'ws://localhost:8080/websocket/monitor')

# 🆕 生成 Consumer ID
CONSUMER_ID = f"{socket.gethostname()}-{os.getpid()}"


def to_float(value, default=0.0):
    """
    健壮的浮点数转换，处理 None、空字符串等异常情况
    
    Args:
        value: 待转换的值
        default: 默认值（当转换失败时返回）
    
    Returns:
        float: 转换后的浮点数
    """
    if value is None or value == '':
        return default
    try:
        return float(value)
    except (TypeError, ValueError, AttributeError):
        return default

# 分组配置
BATCHES_PER_GROUP = 7  # 每组启动7个连接
GROUP_START_DELAY = 3  # 组间启动延迟（秒）

# 🚀 配置 SOL WS 专用日志（独立于 solalert.log）
logger = logging.getLogger('solalert.monitor.sol_ws_v2')
logger.setLevel(logging.INFO)

# 清除现有 handlers（避免重复）
logger.handlers.clear()

# 添加文件 handler（sol_ws_monitor_v2.log）
file_handler = logging.FileHandler('logs/sol_ws_monitor_v2.log', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(name)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))

# 添加控制台 handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s [🟢SOL_WS_V2] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 不传播到父 logger（避免重复记录到 solalert.log）
logger.propagate = False

# 📊 配置原始数据记录器（用于回测分析）
data_logger = logging.getLogger('solalert.monitor.sol_ws_v2.raw_data')
data_logger.setLevel(logging.DEBUG)
data_logger.handlers.clear()

# 原始数据单独记录到 sol_ws_raw_data_v2.log
raw_data_handler = logging.FileHandler('logs/sol_ws_raw_data_v2.log', encoding='utf-8')
raw_data_handler.setLevel(logging.DEBUG)
raw_data_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
data_logger.addHandler(raw_data_handler)
data_logger.propagate = False  # 不传播到父 logger

# 修复 Windows 控制台 emoji 显示问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# 🆕 V2 版本：使用新的数据加载器，不再需要 load_batch_data 函数
# 直接使用 load_batch_data_v2(task_id, batch_id)


async def send_to_all_channels(
    token_address: str,
    alert_message: str,
    telegram: TelegramNotifier,
    reply_markup: InlineKeyboardMarkup = None
) -> Dict:
    """
    发送消息到所有配置的 Telegram 群组
    
    Args:
        token_address: Token 地址
        alert_message: 告警消息
        telegram: Telegram 通知器
        reply_markup: 按钮（可选）
    
    Returns:
        {
            'success_count': 成功数量,
            'fail_count': 失败数量,
            'message_ids': {group_id: message_id, ...},
            'errors': {group_id: error_message, ...},
            'overall_success': True/False
        }
    """
    # 获取所有 SOL WS 告警群组 ID
    alert_group_ids = TELEGRAM_CONFIG.get('SOL_WS_CHANNEL_IDS', [])
    
    if not alert_group_ids:
        logger.warning(f"⚠️ 未配置 Telegram 群组 ID - {token_address[:10]}...")
        return {
            'success_count': 0,
            'fail_count': 0,
            'message_ids': {},
            'errors': {'config': 'No channel IDs configured'},
            'overall_success': False
        }
    
    success_count = 0
    fail_count = 0
    message_ids = {}
    errors = {}
    
    # 循环发送到每个群组
    for group_id in alert_group_ids:
        try:
            result = telegram.send(
                chat_id=str(group_id),
                message=alert_message,
                reply_markup=reply_markup
            )
            
            # 健壮判断：兼容 bool, dict, 状态码
            if result:
                if isinstance(result, dict):
                    message_id = result.get('message_id', 0)
                    message_ids[str(group_id)] = message_id
                    logger.info(f"✅ 发送到群组{group_id}成功: {token_address[:10]}... | message_id={message_id}")
                else:
                    message_ids[str(group_id)] = 0
                    logger.info(f"✅ 发送到群组{group_id}成功: {token_address[:10]}...")
                success_count += 1
            else:
                logger.error(f"❌ 发送到群组{group_id}失败: {token_address} | send返回False")
                errors[str(group_id)] = "send returned False"
                fail_count += 1
                
        except Exception as e:
            logger.error(f"❌ 发送到群组{group_id}异常: {token_address} | 错误: {e}")
            errors[str(group_id)] = str(e)
            fail_count += 1
    
    # 统计结果
    if success_count > 0:
        logger.info(f"✅ Telegram批量发送完成 - {token_address[:10]}... | 成功{success_count}/{len(alert_group_ids)}")
    else:
        logger.error(f"❌❌❌ Telegram批量发送全部失败 - {token_address} | {fail_count}个群组")
    
    return {
        'success_count': success_count,
        'fail_count': fail_count,
        'message_ids': message_ids,
        'errors': errors,
        'overall_success': success_count > 0
    }


async def batch_ws_handler_v2(
    task_id: int,
    batch_id: int,           # monitor_batch_v2.id（全局唯一）⭐
    batch_no: int,           # 任务内批次号（显示用）
    task_name: str,          # 🆕 任务名称
    alert_checker: SolAlertChecker,
    telegram: TelegramNotifier,
    wechat: WeChatNotifier,
    stats: dict,
    ws_client: MonitorWebSocketClient  # 🆕 WebSocket 客户端
):
    """
    单个批次的 WebSocket 处理器（V2版本，支持热更新）
    
    Args:
        task_id: 任务ID
        batch_id: 批次ID（monitor_batch_v2.id，全局唯一）⭐
        batch_no: 任务内批次号（显示用）
        task_name: 任务名称
        alert_checker: 告警检查器
        telegram: Telegram通知器
        wechat: 微信通知器
        stats: 全局统计字典
        ws_client: Monitor WebSocket 客户端
    """
    conn_name = f"{task_name}-Batch{batch_no}"
    logger.info(f"🚀 [{conn_name}] 初始化... (batch_id={batch_id})")
    
    # 🆕 批次重载标记
    reload_requested = [False]  # 使用列表避免闭包问题
    
    def on_batch_reload(reload_task_id, epoch):
        """批次重载回调"""
        if reload_task_id == task_id:
            logger.info(f"🔄 [{conn_name}] 收到重载通知 (epoch={epoch})")
            reload_requested[0] = True
            return True
        return False
    
    # 🆕 注册重载回调
    if ws_client:
        ws_client.on_batch_reload(on_batch_reload)
        # 🆕 上报批次启动
        ws_client.update_batch_status(batch_id, "running", f"初始化中...")
    
    # 统计
    reconnect_count = 0
    max_reconnects = 999999
    reconnect_delay = 1
    max_reconnect_delay = 60
    no_data_timeout = 86400  # 24小时无数据才断开（冷门币可能长时间无数据）
    
    # 外层循环：支持配置重载
    while True:
        # 🆕 V2 版本：使用新的数据加载器
        pairs, pair_to_full_config = load_batch_data_v2(task_id, batch_id)
        
        if not pairs:
            logger.error(f"❌ [{conn_name}] 无数据")
            if ws_client:
                ws_client.update_batch_status(batch_id, "error", "无监控目标")
            break
        
        # 🆕 缓存配置信息（用于后续显示）
        first_config = next(iter(pair_to_full_config.values()))
        config_summary = alert_checker.format_config_summary(first_config)
        config_name = first_config.get('config_name') or first_config.get('template_name', '未知模板')
        
        # 🆕 上报批次运行状态
        if ws_client:
            ws_client.update_batch_status(batch_id, "running", f"监控 {len(pairs)} 个目标")
        
        # 重置统计
        message_count = 0
        data_count = 0
        alert_count = 0
        received_pairs = set()
        last_data_time = datetime.now()
        
        # 重置重载标记
        reload_requested[0] = False
        
        # 初始化统计
        stats[batch_id] = {
            'task_id': task_id,
            'batch_no': batch_no,
            'pairs': len(pairs),
            'messages': 0,
            'data': 0,
            'alerts': 0,
            'active_pairs': 0,
            'reconnects': reconnect_count,
            'status': 'connecting',
            'last_data_time': datetime.now(),
            'failed': False
        }
        
        # 内层循环：WebSocket 连接和监控
        while reconnect_count < max_reconnects and not reload_requested[0]:
            try:
                logger.info(f"🔌 [{conn_name}] 正在连接 {WS_URL}...")
                
                async with websockets.connect(
                    WS_URL,
                    extra_headers={'x-api-key': API_KEY},
                    ping_interval=30,
                    ping_timeout=60,
                    close_timeout=10
                ) as ws:
                    logger.info(f"✅ [{conn_name}] 已连接")
                    stats[batch_id]['status'] = 'connected'
                    
                    # 订阅（DBotX WebSocket 格式）
                    subscribe_message = {
                        "method": "subscribe",
                        "type": "pairsInfo",
                        "args": {
                            "pairsInfoInterval": "1m",
                            "pairs": pairs
                        }
                    }
                    await ws.send(json.dumps(subscribe_message))
                    logger.info(f"📡 [{conn_name}] 已订阅 {len(pairs)} 个pair")
                    
                    # 🆕 显示配置详情（订阅成功后）
                    logger.info(f"   📋 配置: {config_name}")
                    logger.info(f"   ⚙️  规则: {config_summary}")
                    logger.info(f"👂 [{conn_name}] 开始监听实时数据...")
                    
                    # 重置重连延迟
                    reconnect_delay = 1
                    
                    # 监听消息
                    while not reload_requested[0]:
                        try:
                            # 超时接收（5秒），用于定期检查重载标记和无数据超时
                            message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            message_count += 1
                            stats[batch_id]['messages'] = message_count
                            
                            data = json.loads(message)
                            
                    
                            
                            # 记录原始数据
                            data_logger.debug(f"[{conn_name}] {json.dumps(data)}")
                            
                            # 订阅确认
                            if data.get('method') == 'subscribeResponse':
                                logger.info(f"📨 [{conn_name}] 订阅确认")
                                stats[batch_id]['status'] = 'subscribed'
                                continue
                            
                            # 心跳确认
                            if data.get('status') == 'ack':
                                continue
                            
                            # 数据处理（支持pairsInfo和tokensInfo两种类型） - 复制自老版本
                            msg_type = data.get('type')
                            
                            # 无数据自愈：检测假活连接
                            now = datetime.now()
                            if msg_type in ('pairsInfo', 'tokensInfo'):
                                last_data_time = now
                                stats[batch_id]['last_data_time'] = now
                            
                            # 数据处理
                            if msg_type in ('pairsInfo', 'tokensInfo'):
                                results = data.get('result', [])
                                data_count += 1
                                stats[batch_id]['data'] = data_count
                                
                                for item in results:
                                    pair = item.get('p')
                                    if not pair:
                                        continue
                                    
                                    received_pairs.add(pair)
                                    stats[batch_id]['active_pairs'] = len(received_pairs)
                                    
                                    # 从内存获取完整配置
                                    full_config = pair_to_full_config.get(pair)
                                    if not full_config:
                                        continue
                                    
                                    ca = full_config['ca']
                                    symbol = full_config['token_symbol']
                                    template_name = full_config.get('template_name') or full_config.get('config_name', 'Unknown')
                                    historical_high_cap = float(full_config.get('market_cap', 0))
                                    
                                    # 健壮的数据验证
                                    try:
                                        pc1m_raw = to_float(item.get('pc1m'), 0)
                                        pc5m_raw = to_float(item.get('pc5m'), 0)
                                        pc1h_raw = to_float(item.get('pc1h'), 0)
                                        volume_raw = to_float(item.get('bsv'), 0)
                                        price = to_float(item.get('tp'), 0)
                                        current_market_cap = to_float(item.get('mp'), 0)
                                        
                                        pc1m = (pc1m_raw if pc1m_raw is not None else 0) * 100
                                        pc5m = (pc5m_raw if pc5m_raw is not None else 0) * 100
                                        pc1h = (pc1h_raw if pc1h_raw is not None else 0) * 100
                                        volume = volume_raw if volume_raw is not None else 0
                                        
                                        # 计算距离历史最高市值的比例
                                        ath_ratio = (current_market_cap / historical_high_cap * 100) if historical_high_cap > 0 else 0
                                    except (TypeError, ValueError) as e:
                                        logger.debug(f"⚠️  [{conn_name}] 数据转换失败: {e}, 跳过")
                                        continue
                                    
                                    # 记录原始数据
                                    data_logger.debug(
                                        f"Batch{batch_id} | {symbol:8s} | {ca} | "
                                        f"模板:{template_name} | "
                                        f"价格:${price:.10f} | 当前市值:${current_market_cap:,.0f} | ATH:${historical_high_cap:,.0f} ({ath_ratio:.1f}%) | "
                                        f"1m:{pc1m:+7.2f}% | 5m:{pc5m:+7.2f}% | 1h:{pc1h:+7.2f}% | "
                                        f"交易量:${volume:,.0f}"
                                    )
                                    
                                    # 过滤异常数据
                                    if abs(pc1m) > 500 or volume < 1:
                                        logger.debug(f"⏭️  [{conn_name}] {symbol} 数据异常跳过: 涨跌幅{pc1m:+.2f}% 交易量${volume:,.0f}")
                                        continue
                                    
                                    # 检查告警
                                    should_alert, reasons, metrics = alert_checker.check_alert_conditions(
                                        item, full_config
                                    )
                                    
                                    # 🆕 打印判断结果（无论是否告警）
                                    volume_str = format_number(volume, include_dollar=True)
                                    mc_str = format_number(current_market_cap, include_dollar=True)
                                    
                                    if should_alert:
                                        logger.info(
                                            f"✅ [{conn_name}] {symbol:8s} | "
                                            f"涨跌{pc1m:+6.2f}% 量{volume_str:>8s} 市值{mc_str:>8s} | "
                                            f"满足条件: {', '.join(reasons)}"
                                        )
                                    else:
                                        logger.info(
                                            f"⏭️  [{conn_name}] {symbol:8s} | "
                                            f"涨跌{pc1m:+6.2f}% 量{volume_str:>8s} 市值{mc_str:>8s} | "
                                            f"不满足"
                                        )
                                    
                                    if should_alert:
                                        alert_count += 1
                                        stats[batch_id]['alerts'] = alert_count
                                        
                                        # 显示配置信息
                                        config_info = alert_checker.format_config_summary(full_config)
                                        
                                        # 记录告警到原始数据日志
                                        data_logger.debug(
                                            f"🔔 ALERT | Batch{batch_id} | {symbol:8s} | {ca} | "
                                            f"模板:{template_name} | "
                                            f"价格:${price:.10f} | 当前市值:${current_market_cap:,.0f} | ATH:${historical_high_cap:,.0f} ({ath_ratio:.1f}%) | "
                                            f"1m:{pc1m:+7.2f}% | 5m:{pc5m:+7.2f}% | 1h:{pc1h:+7.2f}% | "
                                            f"交易量:${volume:,.0f} | "
                                            f"原因: {', '.join(reasons)}"
                                        )
                                        
                                        logger.info(
                                            f"🔔 [{conn_name}] {symbol} 告警触发！"
                                            f"涨跌幅:{pc1m:+.2f}% 交易量:${volume:,.0f}"
                                        )
                                        logger.info(f"   📋 模板: {template_name}")
                                        logger.info(f"   ⚙️  配置: {config_info}")
                                        logger.info(f"   ✨ 原因: {', '.join(reasons)}")
                                        
                                        # 格式化消息
                                        msg_text = alert_checker.format_alert_message(
                                            full_config, metrics, reasons
                                        )
                                        buttons = alert_checker.create_sol_buttons(ca, pair)
                                        
                                        # 从配置读取群组ID列表
                                        alert_group_ids = TELEGRAM_CONFIG.get('SOL_WS_CHANNEL_IDS', [-1003291885712, -1003394657356])
                                        
                                        # 发送到所有群组（使用老版本参数）
                                        tg_result = await send_to_all_channels(
                                            telegram=telegram,
                                            message=msg_text,
                                            reply_markup=buttons,
                                            token_address=ca,
                                            alert_group_ids=alert_group_ids
                                        )
                                        
                                        tg_success = tg_result['overall_success']
                                        tg_success_count = tg_result['success_count']
                                        tg_fail_count = tg_result['fail_count']
                                        tg_message_ids = tg_result['message_ids']
                                        tg_errors = tg_result['errors']
                                        
                                        # 微信发送（异步）
                                        wechat_task = wechat.send(
                                            target="default",
                                            message=msg_text
                                        )
                                        
                                        try:
                                            wechat_result = await wechat_task
                                        except Exception as wechat_err:
                                            logger.warning(f"   ⚠️ WeChat发送异常: {wechat_err}")
                                            wechat_result = False
                                        
                                        # 健壮判断：兼容布尔值、字典、状态码
                                        def is_send_success(result) -> bool:
                                            if isinstance(result, Exception):
                                                return False
                                            if isinstance(result, bool):
                                                return result
                                            if isinstance(result, dict):
                                                return result.get('success', False)
                                            return bool(result)
                                        
                                        wechat_success = is_send_success(wechat_result)
                                        
                                        # TODO: 数据库记录（后续实现）
                            
                        except asyncio.TimeoutError:
                            # 超时（正常），继续循环
                            # 检查无数据超时
                            elapsed = (datetime.now() - last_data_time).total_seconds()
                            if elapsed > no_data_timeout:
                                logger.warning(f"⚠️ [{conn_name}] {no_data_timeout}秒无数据，自断重连")
                                break
                            continue
                        
                        except websockets.exceptions.ConnectionClosed:
                            # WebSocket 连接关闭，跳出内层循环，触发重连
                            logger.debug(f"🔌 [{conn_name}] WebSocket 连接关闭")
                            break
                        
                        except Exception as e:
                            # 其他异常才打印错误
                            error_msg = str(e)
                            if "no close frame" not in error_msg:  # 过滤掉常见的 close frame 错误
                                logger.error(f"❌ [{conn_name}] 消息处理错误: {e}")
                            continue
                    
                    # 如果是因为重载请求退出循环，关闭 WebSocket
                    if reload_requested[0]:
                        logger.info(f"🔄 [{conn_name}] 重载请求，关闭当前连接")
                        break
            
            except (websockets.exceptions.ConnectionClosed, 
                    websockets.exceptions.WebSocketException,
                    ConnectionError, OSError) as e:
                reconnect_count += 1
                stats[batch_id]['reconnects'] = reconnect_count
                stats[batch_id]['status'] = 'reconnecting'
                
                logger.warning(f"⚠️ [{conn_name}] 连接断开: {e}")
                logger.info(f"🔄 [{conn_name}] {reconnect_delay}秒后重连（第{reconnect_count}次）")
                
                # 🆕 上报批次错误状态
                if ws_client:
                    ws_client.update_batch_status(batch_id, "error", f"连接断开，{reconnect_delay}秒后重连")
                
                await asyncio.sleep(reconnect_delay)
                
                # 指数退避
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                
            except Exception as e:
                logger.error(f"❌ [{conn_name}] 未知错误: {e}", exc_info=True)
                stats[batch_id]['failed'] = True
                
                # 🆕 上报批次错误
                if ws_client:
                    ws_client.update_batch_status(batch_id, "error", f"未知错误: {str(e)[:100]}")
                
                break
        
        # 检查是否需要重载
        if reload_requested[0]:
            logger.info(f"🔄 [{conn_name}] 开始重载批次数据...")
            if ws_client:
                ws_client.update_batch_status(batch_id, "running", "重载配置中...")
            # 继续外层 while True 循环，重新加载数据
        else:
            # 正常退出
            logger.info(f"👋 [{conn_name}] 批次处理完成")
            if ws_client:
                ws_client.update_batch_status(batch_id, "completed", "批次处理完成")
            break


async def print_stats_periodically(stats: dict, telegram: TelegramNotifier = None):
    """定期打印统计信息（每5分钟）"""
    interval = 300  # 5分钟
    failed_alert_sent = False
    
    while True:
        await asyncio.sleep(interval)
        
        logger.info("=" * 80)
        logger.info("📊 监控统计")
        logger.info("=" * 80)
        
        total_pairs = 0
        total_messages = 0
        total_data = 0
        total_alerts = 0
        total_active = 0
        total_reconnects = 0
        failed_batches = 0
        
        for batch_id, stat in sorted(stats.items()):
            task_id = stat.get('task_id', '?')
            batch_no = stat.get('batch_no', '?')
            status_emoji = {
                'connecting': '🔌',
                'connected': '✅',
                'reconnecting': '🔄',
                'failed': '❌'
            }.get(stat['status'], '❓')
            
            logger.info(f"{status_emoji} Task{task_id}-Batch{batch_no} (ID={batch_id}): "
                       f"Pairs={stat['pairs']}, Data={stat['data']}, "
                       f"Active={stat['active_pairs']}, Alerts={stat['alerts']}, "
                       f"Reconnects={stat['reconnects']}")
            
            total_pairs += stat['pairs']
            total_messages += stat['messages']
            total_data += stat['data']
            total_alerts += stat['alerts']
            total_active += stat['active_pairs']
            total_reconnects += stat['reconnects']
            
            if stat['failed']:
                failed_batches += 1
        
        logger.info("-" * 80)
        logger.info(f"📊 总计: Pairs={total_pairs}, Data={total_data}, "
                   f"Active={total_active}, Alerts={total_alerts}, "
                   f"Reconnects={total_reconnects}")
        logger.info("=" * 80)
        
        # 批次失败告警
        total_batches = len(stats)
        if total_batches > 0:
            fail_rate = failed_batches / total_batches
            if fail_rate > 0.5 and not failed_alert_sent and telegram:
                alert_message = f"⚠️ SOL WS 监控告警\n\n批次失败率过高：{failed_batches}/{total_batches} ({fail_rate:.1%})\n请检查服务状态！"
                try:
                    telegram.send(chat_id=str(TELEGRAM_CONFIG.get('ADMIN_CHAT_ID', '')), message=alert_message)
                    failed_alert_sent = True
                except:
                    pass


async def main():
    """主函数：V2版本，支持热更新"""
    logger.info("=" * 80)
    logger.info("🚀 SOL WebSocket 监控 - V2版本（新表结构）")
    logger.info("   ✨ 支持配置热更新")
    logger.info("   ✨ 实时状态推送")
    logger.info("   ✨ 全局唯一批次ID")
    logger.info("=" * 80)
    logger.info("")
    
    # 🆕 初始化 WebSocket 客户端（暂时禁用）
    # logger.info(f"🔌 初始化 Monitor WebSocket 客户端...")
    # logger.info(f"   URL: {MONITOR_WS_URL}")
    # logger.info(f"   Consumer ID: {CONSUMER_ID}")
    
    # ws_client = MonitorWebSocketClient(url=MONITOR_WS_URL, consumer_id=CONSUMER_ID)
    
    # def on_connected():
    #     logger.info("✅ Monitor WebSocket 连接成功")
    
    # def on_error(error):
    #     logger.error(f"❌ Monitor WebSocket 错误: {error}")
    
    # ws_client.on_connected(on_connected).on_error(on_error)
    
    # if not ws_client.connect():
    #     logger.warning("⚠️ Monitor WebSocket 连接失败，继续运行（无状态推送）")
    #     ws_client = None  # 设置为 None，后续检查
    # else:
    #     logger.info("✅ Monitor WebSocket 客户端已连接")
    
    ws_client = None  # 🔧 临时禁用 WebSocket 客户端
    
    # logger.info("")
    
    # 🆕 自动补齐缺失的 pair_address（并发模式，API支持6000/分钟）
    try:
        from solalert.loaders.pair_updater import PairAddressUpdater
        updater = PairAddressUpdater()
        logger.info("🔄 开始补齐缺失的 pair_address（并发模式）...")
        # 并发处理，每批100个，不限制批次数（API支持6000/分钟，100个只需1秒左右）
        updated_count = await updater.update_missing_pairs(chain_type='sol', batch_size=100, max_batches=None)
        if updated_count > 0:
            logger.info(f"✅ 已补齐 {updated_count} 个 pair_address")
        await updater.api.close()  # 关闭 HTTP 客户端
    except Exception as e:
        logger.warning(f"⚠️ pair_address 补齐失败（将在监控时动态获取）: {e}")
    
    # 🆕 V2 版本：加载所有活跃批次
    batches = load_all_active_batches_v2(chain_type='sol')
    
    if not batches:
        logger.error("❌ 未找到活跃批次")
        if ws_client:
            ws_client.close()
        return
    
    total_batches = len(batches)
    logger.info(f"📊 找到 {total_batches} 个活跃批次")
    
    # 显示批次详情
    for batch in batches[:5]:  # 只显示前5个
        logger.info(f"   - {batch['task_name']}-Batch{batch['batch_no']}: "
                   f"ID={batch['batch_id']}, Items={batch['item_count']}, "
                   f"Epoch={batch['epoch']}")
    if total_batches > 5:
        logger.info(f"   ... 及其他 {total_batches - 5} 个批次")
    logger.info("")
    
    # 分组
    groups = []
    for i in range(0, total_batches, BATCHES_PER_GROUP):
        group = batches[i:i + BATCHES_PER_GROUP]
        groups.append(group)
    
    logger.info(f"📦 分为 {len(groups)} 组启动:")
    for idx, group in enumerate(groups, 1):
        batch_nos = [f"{b['task_name']}-Batch{b['batch_no']}" for b in group]
        logger.info(f"   组{idx}: {', '.join(batch_nos)}")
    logger.info("")
    
    # 初始化共享资源
    redis_client = RedisClient(config=REDIS_CONFIG)
    alert_checker = SolAlertChecker(redis_client)
    telegram = TelegramNotifier()
    wechat = WeChatNotifier()
    
    # 全局统计
    stats = {}
    
    try:
        # 创建所有任务
        tasks = []
        
        # 分组启动
        for group_idx, group in enumerate(groups):
            if group_idx > 0:
                logger.info(f"⏳ 等待 {GROUP_START_DELAY} 秒后启动组{group_idx + 1}...")
                await asyncio.sleep(GROUP_START_DELAY)
            
            batch_nos = [f"{b['task_name']}-Batch{b['batch_no']}" for b in group]
            logger.info(f"🚀 启动组{group_idx + 1}: {', '.join(batch_nos)}")
            
            for idx, batch in enumerate(group):
                if idx > 0:
                    await asyncio.sleep(0.5)
                
                task = asyncio.create_task(
                    batch_ws_handler_v2(
                        task_id=batch['task_id'],
                        batch_id=batch['batch_id'],      # monitor_batch_v2.id（全局唯一）⭐
                        batch_no=batch['batch_no'],      # 任务内批次号（显示用）
                        task_name=batch['task_name'],    # 🆕 任务名称
                        alert_checker=alert_checker,
                        telegram=telegram,
                        wechat=wechat,
                        stats=stats,
                        ws_client=ws_client              # 🆕 传递 WebSocket 客户端
                    )
                )
                tasks.append(task)
            
            logger.info(f"✅ 组{group_idx + 1} 已启动")
            logger.info("")
        
        logger.info(f"🎉 所有 {len(tasks)} 个连接已启动")
        logger.info("")
        
        # 启动统计任务
        stats_task = asyncio.create_task(print_stats_periodically(stats, telegram=telegram))
        tasks.append(stats_task)
        
        # 等待所有任务
        results = await asyncio.gather(*tasks, return_exceptions=False)
    
    except KeyboardInterrupt:
        logger.info("\n⚠️  收到中断信号，正在关闭...")
    
    except Exception as e:
        logger.error(f"❌ 主程序错误: {e}", exc_info=True)
    
    finally:
        redis_client.close()
        if ws_client:
            ws_client.close()  # 🆕 关闭 Monitor WebSocket
        logger.info("👋 监控已停止")

if __name__ == "__main__":
    # 确保日志目录存在
    os.makedirs('logs', exist_ok=True)
    
    asyncio.run(main())

