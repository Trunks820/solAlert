"""
SOL WebSocket 监控 - 21条连接版本
每个批次一条 WebSocket 连接（因为每条 WS 最多 99 个 pair）
采用分组启动策略，避免突发压力

🚀 性能优化：
- 配置一次性加载到内存，避免热路径同步查库
- 通知返回值健壮判断（兼容bool/dict/状态码）
- 重连指数退避 + 抖动（1s→2s→4s...→60s，±20%）
- 无数据自愈：5分钟无数据自动断开重连
- 批次失败监控：超过50%批次失败时发送Telegram告警
- DatabaseManager自动关闭，避免连接泄漏

📝 配置热更新说明：
当前配置在启动时一次性加载，若需调整批次配置需重启脚本。
如需热更新，可考虑：
1. 添加 reload_config() 定时任务（如每30分钟）
2. 增加外部信号钩子（如监听 Redis 配置变更事件）
3. 实现优雅重启机制（先加载新配置再切换连接）
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import asyncio
import websockets
import json
import logging
from datetime import datetime
from typing import List, Dict
from solalert.core.database import DatabaseManager
from solalert.core.redis_client import RedisClient
from solalert.core.config import REDIS_CONFIG
from solalert.monitor.sol_alert_checker import SolAlertChecker
from solalert.notifiers.telegram import TelegramNotifier
from solalert.notifiers.wechat import WeChatNotifier

# WebSocket配置
WS_URL = "wss://api-data-v1.dbotx.com/data/ws/"
API_KEY = "i1o3elfavv59ds02fggj9rsd0eg8w657"


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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/sol_ws_monitor.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# 修复 Windows 控制台 emoji 显示问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# 使用统一的层级logger命名
logger = logging.getLogger('solalert.monitor.sol_ws')


def load_batch_data(batch_id: int) -> tuple:
    """
    加载单个批次的完整配置数据（一次性加载，避免热路径查库）
    
    Args:
        batch_id: 批次ID
    
    Returns:
        (pairs列表, pair_to_full_config映射)
    """
    db = DatabaseManager()
    
    try:
        # 🚀 优化：一次性加载所有字段，缓存到内存
        query = """
            SELECT *
            FROM sol_ws_batch_pool
            WHERE batch_id = %s AND is_active = 1
            ORDER BY sort_order
        """
        
        configs = db.execute_query(query, (batch_id,))
        
        if not configs:
            return [], {}
        
        pairs = []
        pair_to_full_config = {}  # 存储完整配置，避免热路径查库
        
        for config in configs:
            pair_addr = config['pair_address']
            ca = config['ca']
            
            pairs.append({
                "pair": pair_addr,
                "token": ca
            })
            
            # 存储完整配置到内存
            pair_to_full_config[pair_addr] = config  # 包含所有字段
        
        return pairs, pair_to_full_config
    
    finally:
        # 📝 DatabaseManager 使用连接池，无需手动关闭
        pass


async def batch_ws_handler(
    batch_id: int,
    alert_checker: SolAlertChecker,
    telegram: TelegramNotifier,
    wechat: WeChatNotifier,
    stats: dict
):
    """
    单个批次的 WebSocket 处理器（带自动重连）
    
    Args:
        batch_id: 批次ID
        alert_checker: 告警检查器
        telegram: Telegram通知器
        wechat: 微信通知器
        stats: 全局统计字典
    """
    conn_name = f"Batch{batch_id}"
    logger.info(f"🚀 [{conn_name}] 初始化...")
    
    # 🚀 优化：一次性加载完整配置到内存，避免热路径查库
    pairs, pair_to_full_config = load_batch_data(batch_id)
    
    if not pairs:
        logger.error(f"❌ [{conn_name}] 无数据")
        return
    
    logger.info(f"✅ [{conn_name}] 加载 {len(pairs)} 个pair（配置已缓存到内存）")
    
    # 统计
    message_count = 0
    data_count = 0
    alert_count = 0
    received_pairs = set()
    reconnect_count = 0
    max_reconnects = 100
    
    # 🚀 重连指数退避
    reconnect_delay = 1  # 初始延迟
    max_reconnect_delay = 60  # 最大延迟60秒
    
    # 🚀 无数据自愈（假活连接检测）
    last_data_time = datetime.now()
    no_data_timeout = 300  # 5分钟无数据 → 自断重连
    
    # 初始化统计
    stats[batch_id] = {
        'pairs': len(pairs),
        'messages': 0,
        'data': 0,
        'alerts': 0,
        'active_pairs': 0,
        'reconnects': 0,
        'status': 'connecting',
        'last_data_time': datetime.now(),
        'failed': False  # 🚀 标记批次是否永久失败
    }
    
    while reconnect_count < max_reconnects:
        try:
            logger.info(f"🔌 [{conn_name}] 正在连接 {WS_URL}...")
            
            # websockets 12.0 使用 extra_headers
            async with websockets.connect(
                WS_URL,
                extra_headers=={'x-api-key': API_KEY},
                ping_interval=30,
                ping_timeout=60,
                close_timeout=10
            ) as ws:
                logger.info(f"✅ [{conn_name}] 已连接")
                stats[batch_id]['status'] = 'connected'
                
                # 订阅
                subscribe_msg = {
                    "method": "subscribe",
                    "type": "pairsInfo",
                    "args": {
                        "pairsInfoInterval": "1m",
                        "pairs": pairs
                    }
                }
                
                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"📡 [{conn_name}] 已订阅")
                
                last_heartbeat = datetime.now()
                
                # 消息循环
                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        message_count += 1
                        stats[batch_id]['messages'] = message_count
                        
                        data = json.loads(message)
                        msg_type = data.get('type')
                        
                        # 订阅确认
                        if data.get('method') == 'subscribeResponse':
                            logger.info(f"📨 [{conn_name}] 订阅确认")
                            stats[batch_id]['status'] = 'subscribed'
                            continue
                        
                        # 心跳
                        if data.get('status') == 'ack':
                            continue
                        
                        # 定期心跳日志（每2分钟）
                        if (datetime.now() - last_heartbeat).total_seconds() >= 120:
                            logger.info(
                                f"💓 [{conn_name}] "
                                f"消息:{message_count} 数据:{data_count} "
                                f"活跃:{len(received_pairs)}/{len(pairs)} 告警:{alert_count}"
                            )
                            last_heartbeat = datetime.now()
                        
                        # 🚀 无数据自愈：检测假活连接
                        now = datetime.now()
                        if msg_type == 'pairsInfo':
                            last_data_time = now
                            stats[batch_id]['last_data_time'] = now
                        elif (now - last_data_time).total_seconds() > no_data_timeout:
                            logger.warning(
                                f"⚠️  [{conn_name}] 假活连接：{no_data_timeout}秒无数据，主动断开重连"
                            )
                            stats[batch_id]['status'] = 'no_data_restart'
                            break  # 跳出内层循环，触发重连
                        
                        # 数据处理
                        if msg_type == 'pairsInfo':
                            results = data.get('result', [])
                            data_count += 1
                            stats[batch_id]['data'] = data_count
                            
                            for item in results:
                                pair = item.get('p')
                                if not pair:
                                    continue
                                
                                received_pairs.add(pair)
                                stats[batch_id]['active_pairs'] = len(received_pairs)
                                
                                # 🚀 优化：直接从内存获取完整配置，无需查库
                                full_config = pair_to_full_config.get(pair)
                                if not full_config:
                                    continue
                                
                                ca = full_config['ca']
                                symbol = full_config['token_symbol']

                                # 🚀 健壮的数据验证（防止 None 值导致 TypeError）
                                try:
                                    pc1m_raw = to_float(item.get('pc1m'), 0)
                                    volume_raw = to_float(item.get('bsv'), 0)
                                    pc1m = (pc1m_raw if pc1m_raw is not None else 0) * 100
                                    volume = volume_raw if volume_raw is not None else 0
                                except (TypeError, ValueError) as e:
                                    logger.debug(f"⚠️  [{conn_name}] 数据转换失败: {e}, 跳过")
                                    continue
                                
                                # 📊 详细日志：显示收到的数据（每5条输出一次汇总）
                                if data_count % 5 == 0:
                                    logger.info(
                                        f"📊 [{conn_name}] 数据汇总 | "
                                        f"{symbol}: 涨跌幅{pc1m:+.2f}% 交易量${volume:,.0f} | "
                                        f"活跃:{len(received_pairs)}/{len(pairs)}"
                                    )
                                
                                if abs(pc1m) > 500 or volume < 1:
                                    continue
                                
                                # 🚀 优化：直接使用内存中的完整配置，避免查库
                                # 检查告警（配置已在内存中）
                                should_alert, reasons, metrics = alert_checker.check_alert_conditions(
                                    item, full_config
                                )
                                
                                if should_alert:
                                    logger.info(
                                        f"🔔 [{conn_name}] {symbol} 告警触发！"
                                        f"涨跌幅:{pc1m:+.2f}% 交易量:${volume:,.0f} | "
                                        f"原因: {', '.join(reasons)}"
                                    )
                                    
                                    # 格式化消息
                                    msg_text = alert_checker.format_alert_message(
                                        full_config, metrics, reasons
                                    )
                                    buttons = alert_checker.create_sol_buttons(ca, pair)
                                    
                                    # 并发发送
                                    tg_task = telegram.send(
                                        target=-1003291885712,
                                        message=msg_text,
                                        parse_mode="HTML",  # 🚀 使用HTML格式，支持CA蓝色链接
                                        reply_markup=buttons
                                    )
                                    wechat_task = wechat.send(
                                        target="default",
                                        message=msg_text
                                    )
                                    
                                    tg_result, wechat_result = await asyncio.gather(
                                        tg_task, wechat_task,
                                        return_exceptions=True
                                    )
                                    
                                    # ✅ 健壮判断：兼容布尔值、字典、状态码
                                    def is_send_success(result) -> bool:
                                        if isinstance(result, Exception):
                                            return False
                                        if isinstance(result, bool):
                                            return result
                                        if isinstance(result, dict):
                                            return result.get('success', False)
                                        # 对于其他类型（如状态码），非None/0视为成功
                                        return bool(result)
                                    
                                    tg_success = is_send_success(tg_result)
                                    wechat_success = is_send_success(wechat_result)
                                    
                                    if tg_success or wechat_success:
                                        alert_count += 1
                                        stats[batch_id]['alerts'] = alert_count
                                        alert_checker.set_cooldown(ca)
                                        
                                        # 📝 保存到数据库
                                        try:
                                            # 准备数据库记录
                                            alert_time = datetime.now()
                                            
                                            # 提取 telegram message_id
                                            tg_msg_id = None
                                            tg_error = None
                                            if tg_success and isinstance(tg_result, dict):
                                                tg_msg_id = str(tg_result.get('result', {}).get('message_id', ''))
                                            elif isinstance(tg_result, Exception):
                                                tg_error = str(tg_result)
                                            
                                            # 提取 wechat message_id
                                            wechat_msg_id = None
                                            wechat_error = None
                                            if wechat_success and isinstance(wechat_result, dict):
                                                wechat_msg_id = str(wechat_result.get('message_id', ''))
                                            elif isinstance(wechat_result, Exception):
                                                wechat_error = str(wechat_result)
                                            
                                            # 构建 trigger_reasons JSON
                                            trigger_reasons = json.dumps(reasons, ensure_ascii=False)
                                            
                                            insert_sql = """
                                                INSERT INTO sol_ws_alert_log (
                                                    batch_id, ca, token_symbol, token_name, pair_address,
                                                    template_id, template_name,
                                                    price, market_cap,
                                                    price_change, price_change_1m, price_change_5m, price_change_1h,
                                                    volume_1h, buy_volume_1h, sell_volume_1h,
                                                    txs_1h, buy_txs_1h, sell_txs_1h,
                                                    top10_percent,
                                                    trigger_reasons, trigger_time_interval, trigger_logic,
                                                    alert_message,
                                                    telegram_sent, telegram_success, telegram_message_id, telegram_error,
                                                    wechat_sent, wechat_success, wechat_message_id, wechat_error,
                                                    alert_time
                                                ) VALUES (
                                                    %s, %s, %s, %s, %s,
                                                    %s, %s,
                                                    %s, %s,
                                                    %s, %s, %s, %s,
                                                    %s, %s, %s,
                                                    %s, %s, %s,
                                                    %s,
                                                    %s, %s, %s,
                                                    %s,
                                                    %s, %s, %s, %s,
                                                    %s, %s, %s, %s,
                                                    %s
                                                )
                                            """
                                            
                                            db_params = (
                                                batch_id, ca, full_config['token_symbol'], full_config['token_name'], pair,
                                                full_config['template_id'], full_config['template_name'],
                                                to_float(metrics.get('price')), to_float(metrics.get('market_cap')),
                                                to_float(metrics.get('pc1h')), to_float(metrics.get('pc1m')), to_float(metrics.get('pc5m')), to_float(metrics.get('pc1h')),
                                                to_float(metrics.get('bsv')), to_float(metrics.get('bv1h')), to_float(metrics.get('sv1h')),
                                                to_float(metrics.get('bst')), to_float(metrics.get('bt1h')), to_float(metrics.get('st1h')),
                                                to_float(metrics.get('t10')),
                                                trigger_reasons, full_config['time_interval'], full_config['trigger_logic'],
                                                msg_text,
                                                1, 1 if tg_success else 0, tg_msg_id, tg_error,
                                                1, 1 if wechat_success else 0, wechat_msg_id, wechat_error,
                                                alert_time
                                            )
                                            
                                            db_temp = DatabaseManager()
                                            db_temp.execute_update(insert_sql, db_params)
                                            logger.info(f"   ✅ 告警记录已保存到数据库")
                                            
                                        except Exception as db_err:
                                            logger.error(f"   ❌ 保存数据库失败: {db_err}")
                                        
                                        if isinstance(tg_result, Exception):
                                            logger.warning(f"   ⚠️ TG发送失败: {tg_result}")
                                        if isinstance(wechat_result, Exception):
                                            logger.warning(f"   ⚠️ WeChat发送失败: {wechat_result}")
                                    else:
                                        logger.error(f"   ❌ 所有通知渠道发送失败")
                    
                    except asyncio.TimeoutError:
                        continue
                    
                    except websockets.exceptions.ConnectionClosed as e:
                        logger.warning(f"⚠️  [{conn_name}] 断开: {e}")
                        stats[batch_id]['status'] = 'disconnected'
                        break
                    
                    except Exception as e:
                        logger.error(f"❌ [{conn_name}] 消息处理错误: {e}")
                        continue
        
        except Exception as e:
            logger.error(f"❌ [{conn_name}] 连接失败: {type(e).__name__}: {e}")
            stats[batch_id]['status'] = 'error'
        
        # 🚀 重连：指数退避 + 抖动
        reconnect_count += 1
        stats[batch_id]['reconnects'] = reconnect_count
        
        if reconnect_count < max_reconnects:
            # 指数退避：1s → 2s → 4s → 8s → ... → 60s（上限）
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
            
            # 添加抖动（±20%），避免所有批次同时重连
            import random
            jitter = reconnect_delay * 0.2
            wait_time = reconnect_delay + random.uniform(-jitter, jitter)
            
            logger.info(
                f"🔄 [{conn_name}] {wait_time:.1f}秒后重连 "
                f"(第{reconnect_count}次，指数退避={reconnect_delay}s)"
            )
            stats[batch_id]['status'] = 'reconnecting'
            await asyncio.sleep(wait_time)
        else:
            logger.error(f"❌ [{conn_name}] 达到最大重连次数 ({max_reconnects}次)")
            stats[batch_id]['status'] = 'failed'
            stats[batch_id]['failed'] = True  # 🚀 标记永久失败
            break
    
    logger.info(f"📊 [{conn_name}] 结束 - 消息:{message_count} 告警:{alert_count}")


async def print_stats_periodically(stats: dict, interval: int = 300, telegram=None):
    """
    定期打印全局统计（每5分钟）并监控批次健康状态
    
    Args:
        stats: 全局统计字典
        interval: 打印间隔（秒）
        telegram: Telegram通知器（用于发送告警）
    """
    failure_alert_sent = False  # 避免重复告警
    
    while True:
        await asyncio.sleep(interval)
        
        logger.info("\n" + "=" * 80)
        logger.info("📊 全局统计（5分钟汇总）")
        logger.info("=" * 80)
        
        total_messages = 0
        total_data = 0
        total_alerts = 0
        total_active = 0
        total_pairs = 0
        
        status_count = {
            'subscribed': 0,
            'connected': 0,
            'reconnecting': 0,
            'disconnected': 0,
            'error': 0,
            'failed': 0
        }
        
        for batch_id in sorted(stats.keys()):
            s = stats[batch_id]
            total_messages += s['messages']
            total_data += s['data']
            total_alerts += s['alerts']
            total_active += s['active_pairs']
            total_pairs += s['pairs']
            
            status = s['status']
            if status in status_count:
                status_count[status] += 1
        
        logger.info(f"活跃连接: {status_count['subscribed']}/{len(stats)}")
        logger.info(f"总消息数: {total_messages:,}")
        logger.info(f"总数据推送: {total_data:,}")
        logger.info(f"活跃pair: {total_active:,}/{total_pairs:,}")
        logger.info(f"总告警数: {total_alerts:,}")
        
        if status_count['reconnecting'] > 0:
            logger.warning(f"⚠️  重连中: {status_count['reconnecting']} 个")
        if status_count['error'] > 0 or status_count['failed'] > 0:
            logger.error(f"❌ 异常: {status_count['error'] + status_count['failed']} 个")
        
        # 📊 详细批次统计：标注无消息或数据少的批次
        logger.info("")
        logger.info("📋 批次详情:")
        
        # 分类批次
        no_message_batches = []  # 无消息
        low_data_batches = []    # 消息有但数据少（<5条）
        active_batches = []      # 正常活跃
        
        for batch_id in sorted(stats.keys()):
            s = stats[batch_id]
            messages = s['messages']
            data = s['data']
            active = s['active_pairs']
            total = s['pairs']
            status = s['status']
            
            if messages == 0:
                no_message_batches.append((batch_id, status, total))
            elif data < 5:
                low_data_batches.append((batch_id, messages, data, active, total, status))
            else:
                active_batches.append((batch_id, messages, data, active, total, s['alerts']))
        
        # 输出正常活跃的批次
        if active_batches:
            logger.info(f"✅ 正常活跃批次 ({len(active_batches)} 个):")
            for batch_id, msgs, data, active, total, alerts in active_batches:
                logger.info(
                    f"   Batch #{batch_id:2d}: 消息{msgs:4d} 数据{data:4d} "
                    f"活跃{active:3d}/{total:3d} 告警{alerts:2d}"
                )
        
        # 输出数据少的批次
        if low_data_batches:
            logger.warning(f"⚠️  数据少的批次 ({len(low_data_batches)} 个):")
            for batch_id, msgs, data, active, total, status in low_data_batches:
                logger.warning(
                    f"   Batch #{batch_id:2d}: 消息{msgs:4d} 数据{data:4d} "
                    f"活跃{active:3d}/{total:3d} [{status}]"
                )
        
        # 输出无消息的批次
        if no_message_batches:
            logger.error(f"❌ 无消息批次 ({len(no_message_batches)} 个):")
            for batch_id, status, total in no_message_batches:
                logger.error(f"   Batch #{batch_id:2d}: 0消息 [{status}] (共{total}个pair)")
        
        logger.info("")
        
        # 🚀 批次健康监控：所有批次都失败时发送告警
        failed_count = sum(1 for s in stats.values() if s.get('failed', False))
        if failed_count > 0:
            logger.error(f"💀 永久失败的批次: {failed_count}/{len(stats)}")
            
            # 如果超过50%批次失败，发送告警
            if failed_count >= len(stats) * 0.5 and not failure_alert_sent and telegram:
                failure_alert_sent = True
                alert_msg = (
                    f"🚨 SOL WebSocket 监控严重告警\n\n"
                    f"永久失败: {failed_count}/{len(stats)} 个批次\n"
                    f"正常运行: {status_count['subscribed']} 个\n"
                    f"重连中: {status_count['reconnecting']} 个\n\n"
                    f"建议立即检查并重启服务！"
                )
                try:
                    await telegram.send(
                        target=-1003291885712,
                        message=alert_msg,
                        parse_mode="HTML"
                    )
                    logger.error("📤 已发送批次失败告警")
                except Exception as e:
                    logger.error(f"❌ 发送批次失败告警失败: {e}")
        
        logger.info("=" * 80 + "\n")


async def main():
    """主函数：分组启动 21 条 WebSocket 连接"""
    logger.info("=" * 80)
    logger.info("🚀 SOL WebSocket 监控 - 21条连接版本")
    logger.info("   每个批次一条独立的 WebSocket 连接")
    logger.info("   采用分组启动策略，避免突发压力")
    logger.info("=" * 80)
    logger.info("")
    
    # 获取批次列表
    db = DatabaseManager()
    
    try:
        batch_query = """
            SELECT DISTINCT batch_id 
            FROM sol_ws_batch_pool 
            WHERE is_active = 1 
            ORDER BY batch_id
        """
        batch_results = db.execute_query(batch_query)
        
        if not batch_results:
            logger.error("❌ 未找到活跃批次")
            return
        
        batch_ids = [row['batch_id'] for row in batch_results]
        total_batches = len(batch_ids)
    
    finally:
        # 📝 DatabaseManager 使用连接池，无需手动关闭
        pass
    
    logger.info(f"📊 找到 {total_batches} 个活跃批次: {batch_ids}")
    logger.info("")
    
    # 分组
    groups = []
    for i in range(0, total_batches, BATCHES_PER_GROUP):
        group = batch_ids[i:i + BATCHES_PER_GROUP]
        groups.append(group)
    
    logger.info(f"📦 分为 {len(groups)} 组启动:")
    for idx, group in enumerate(groups, 1):
        logger.info(f"   组{idx}: Batch {group}")
    logger.info("")
    
    # 初始化共享资源
    redis_client = RedisClient(config=REDIS_CONFIG)
    alert_checker = SolAlertChecker(redis_client)
    telegram = TelegramNotifier()
    wechat = WeChatNotifier()
    
    # 全局统计（多协程共享）
    # 📝 并发安全性说明：
    # - asyncio 事件循环是单线程的，不会出现真正的并发写冲突
    # - 字典操作在 Python 中是原子的，多个协程串行执行
    # - 如需迁移到多线程/多进程，可改用：
    #   1. asyncio.Lock 加锁保护
    #   2. 封装成 StatsManager 对象（内部管理锁）
    #   3. multiprocessing.Manager().dict()（跨进程）
    stats = {}
    
    try:
        # 创建所有任务
        tasks = []
        
        # 分组启动
        for group_idx, group in enumerate(groups):
            if group_idx > 0:
                # 组间延迟
                logger.info(f"⏳ 等待 {GROUP_START_DELAY} 秒后启动组{group_idx + 1}...")
                await asyncio.sleep(GROUP_START_DELAY)
            
            logger.info(f"🚀 启动组{group_idx + 1}: Batch {group}")
            
            # 🚀 为这组的每个批次创建任务（批次间添加小延迟，避免瞬间冲击）
            for idx, batch_id in enumerate(group):
                if idx > 0:
                    # 同组内的批次之间也稍微错开（0.5秒）
                    await asyncio.sleep(0.5)
                
                task = asyncio.create_task(
                    batch_ws_handler(
                        batch_id,
                        alert_checker,
                        telegram,
                        wechat,
                        stats
                    )
                )
                tasks.append(task)
            
            logger.info(f"✅ 组{group_idx + 1} 已启动")
            logger.info("")
        
        logger.info(f"🎉 所有 {len(tasks)} 个连接已启动")
        logger.info("")
        
        # 🚀 启动统计任务（传入telegram用于批次失败告警）
        stats_task = asyncio.create_task(print_stats_periodically(stats, telegram=telegram))
        tasks.append(stats_task)
        
        # 等待所有任务（永久运行）
        # 🚀 移除 return_exceptions=True，让异常能被看到和记录
        results = await asyncio.gather(*tasks, return_exceptions=False)
    
    except KeyboardInterrupt:
        logger.info("\n⚠️  收到中断信号，正在关闭...")
    
    except Exception as e:
        logger.error(f"❌ 主程序错误: {e}", exc_info=True)
    
    finally:
        redis_client.close()
        logger.info("👋 监控已停止")


if __name__ == "__main__":
    # 确保日志目录存在
    os.makedirs('logs', exist_ok=True)
    
    asyncio.run(main())
