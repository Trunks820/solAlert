"""
测试分批次订阅 Batch 1-2
验证多次订阅是否会累加（而不是覆盖）
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import asyncio
import websockets
import json
import logging
from datetime import datetime
from solalert.core.database import DatabaseManager
from solalert.core.redis_client import RedisClient
from solalert.core.config import REDIS_CONFIG
from solalert.monitor.sol_alert_checker import SolAlertChecker
from solalert.notifiers.telegram import TelegramNotifier

# WebSocket配置
WS_URL = "wss://api-data-v1.dbotx.com/data/ws/"
API_KEY = "i1o3elfavv59ds02fggj9rsd0eg8w657"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_batches_pairs(max_batches=1):
    """从数据库加载前N个批次的pair地址"""
    db = DatabaseManager()
    
    query = """
        SELECT batch_id, ca, token_symbol, pair_address
        FROM sol_ws_batch_pool
        WHERE batch_id <= %s AND is_active = 1
        ORDER BY batch_id, sort_order
    """
    
    configs = db.execute_query(query, (max_batches,))
    
    if not configs:
        logger.error(f"❌ 未找到前{max_batches}个批次的配置")
        return {}
    
    # 按批次分组，并保存完整配置信息
    batches = {}
    pair_to_config = {}  # pair地址 -> 完整配置
    
    for config in configs:
        batch_id = config['batch_id']
        if batch_id not in batches:
            batches[batch_id] = []
        
        pair_addr = config['pair_address']
        batches[batch_id].append({
            "pair": pair_addr,
            "token": config['ca']
        })
        
        # 保存映射关系
        pair_to_config[pair_addr] = {
            'batch_id': batch_id,
            'ca': config['ca'],
            'symbol': config['token_symbol']
        }
    
    total_pairs = sum(len(pairs) for pairs in batches.values())
    logger.info(f"✅ 从数据库加载了 {len(batches)} 个批次，共 {total_pairs} 个pair")
    for batch_id in sorted(batches.keys()):
        logger.info(f"   Batch {batch_id}: {len(batches[batch_id])} 个pair")
    
    return batches, pair_to_config


async def test_separate_subscription():
    """测试订阅Batch 1并接入告警过滤"""
    logger.info("=" * 80)
    logger.info("测试订阅 Batch 1 + 告警过滤 + TG推送")
    logger.info("=" * 80)
    logger.info("")
    
    # 加载pair地址
    batches, pair_to_config = load_batches_pairs(max_batches=1)
    if not batches:
        return
    
    total_pairs = sum(len(pairs) for pairs in batches.values())
    logger.info(f"准备订阅 {len(batches)} 个批次，共 {total_pairs} 个pair")
    logger.info("")
    
    # 初始化告警检查器和Telegram
    redis_client = RedisClient(config=REDIS_CONFIG)
    alert_checker = SolAlertChecker(redis_client)
    telegram = TelegramNotifier()
    alert_count = 0
    
    try:
        # 连接WebSocket
        logger.info(f"连接: {WS_URL}")
        async with websockets.connect(
            WS_URL,
            additional_headers={'x-api-key': API_KEY},
            ping_interval=30,
            ping_timeout=60,  # ping超时时间60秒
            close_timeout=10   # 关闭超时10秒
        ) as ws:
            logger.info("✅ 连接成功")
            logger.info("")
            
            # 🔥 逐个批次订阅（测试是否会累加）
            for batch_id in sorted(batches.keys()):
                pairs = batches[batch_id]
                
                subscribe_msg = {
                    "method": "subscribe",
                    "type": "pairsInfo",
                    "args": {
                        "pairsInfoInterval": "1m",
                        "pairs": pairs
                    }
                }
                
                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"✅ Batch {batch_id}: 已发送订阅请求 ({len(pairs)} 个pair)")
                await asyncio.sleep(0.5)  # 批次间延迟
            
            logger.info("")
            logger.info("✅ 所有批次订阅请求已发送")
            logger.info("")
            
            # 监听消息
            message_count = 0
            data_message_count = 0
            subscription_confirms = 0
            start_time = datetime.now()
            
            # 记录收到数据的pair
            received_pairs = set()
            
            logger.info("开始监听（30分钟）...")
            logger.info("")
            
            timeout_seconds = 12000  # 30分钟
            
            last_heartbeat = datetime.now()
            
            while True:
                try:
                    # 检查是否超时
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed > timeout_seconds:
                        logger.info(f"\n⏱️ 测试时间到（{timeout_seconds}秒）")
                        break
                    
                    # 每30秒打印心跳
                    if (datetime.now() - last_heartbeat).total_seconds() >= 30:
                        logger.info(
                            f"💓 心跳 | 运行: {int(elapsed)}秒 | "
                            f"消息: {message_count} | 确认: {subscription_confirms} | "
                            f"数据: {data_message_count} | 活跃pair: {len(received_pairs)}"
                        )
                        last_heartbeat = datetime.now()
                    
                    # 接收消息
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    message_count += 1
                    
                    # 解析消息
                    data = json.loads(message)
                    msg_type = data.get('type')
                    
                    # 订阅确认
                    if data.get('method') == 'subscribeResponse':
                        subscription_confirms += 1
                        logger.info(f"📨 订阅确认 #{subscription_confirms}: {data.get('result', {}).get('message')}")
                        continue
                    
                    # 其他确认消息（心跳）
                    if data.get('status') == 'ack':
                        continue  # 静默跳过心跳消息
                    
                    # pairsInfo数据
                    if msg_type == 'pairsInfo':
                        results = data.get('result', [])
                        data_message_count += 1
                        
                        logger.info(f"\n{'='*80}")
                        logger.info(f"🎉 收到数据推送 #{data_message_count} | {len(results)} 个pair")
                        logger.info(f"{'='*80}\n")
                        
                        for item in results:
                            pair = item.get('p', 'Unknown')
                            received_pairs.add(pair)
                            
                            # 获取配置信息
                            config_info = pair_to_config.get(pair, {})
                            batch_id = config_info.get('batch_id', '?')
                            ca = config_info.get('ca', 'Unknown')
                            symbol = config_info.get('symbol', 'Unknown')
                            
                            # 原始指标数据
                            tp = item.get('tp', 0)  # 价格
                            mp = item.get('mp', 0)  # 市值
                            pc1m_raw = item.get('pc1m', 0)  # 价格变化1m (小数，原始值)
                            pc5m_raw = item.get('pc5m', 0)  # 价格变化5m
                            pc1h_raw = item.get('pc1h', 0)  # 价格变化1h
                            
                            # 转换为百分比
                            pc1m = pc1m_raw * 100
                            pc5m = pc5m_raw * 100
                            pc1h = pc1h_raw * 100
                            
                            # 打印原始值用于调试
                            if abs(pc1m) > 100:  # 如果变化超过100%，打印原始值
                                logger.info(f"   ⚠️ 异常数据: pc1m_raw={pc1m_raw}, pc1m={pc1m}%")
                            
                            # 交易量：优先使用bsv（当前时间区间的总交易量）
                            bsv = item.get('bsv', 0)  # 当前时间区间的买入+卖出总额
                            bv1m = item.get('bv1m', 0)  # 买入金额1m
                            sv1m = item.get('sv1m', 0)  # 卖出金额1m
                            bv5m = item.get('bv5m', 0)  # 买入金额5m
                            sv5m = item.get('sv5m', 0)  # 卖出金额5m
                            bv1h = item.get('bv1h', 0)  # 买入金额1h
                            sv1h = item.get('sv1h', 0)  # 卖出金额1h
                            
                            # 计算总交易量（根据订阅的时间区间）
                            # 我们订阅的是1m，所以用1m的数据
                            total_volume_1m = bsv if bsv > 0 else (bv1m + sv1m)
                            total_volume_5m = bv5m + sv5m
                            total_volume_1h = bv1h + sv1h
                            
                            t10 = item.get('t10') or 0   # TOP10持仓（处理None）
                            tr = item.get('tr') or 0     # 流动性（处理None）
                            h = item.get('h') or 0       # 持有者（处理None）
                            
                            # 格式化市值（K/M/B）
                            def format_large_number(num):
                                if num is None:
                                    return "$0"
                                if num >= 1_000_000_000:
                                    return f"${num/1_000_000_000:.2f}B"
                                elif num >= 1_000_000:
                                    return f"${num/1_000_000:.2f}M"
                                elif num >= 1_000:
                                    return f"${num/1_000:.2f}K"
                                else:
                                    return f"${num:.0f}"
                            
                            # 显示数据
                            logger.info(f"📊 Batch {batch_id} | {symbol:10s} | CA: {ca}")
                            logger.info(f"   价格: ${tp:.10f} | 市值: {format_large_number(mp)}")
                            logger.info(f"   变化(原始): 1m={pc1m_raw:+.6f} | 5m={pc5m_raw:+.6f} | 1h={pc1h_raw:+.6f}")
                            logger.info(f"   变化(%): 1m={pc1m:+.2f}% | 5m={pc5m:+.2f}% | 1h={pc1h:+.2f}%")
                            logger.info(f"   交易量(原始): 1m={total_volume_1m} | 5m={total_volume_5m} | 1h={total_volume_1h}")
                            logger.info(f"   交易量: 1m={format_large_number(total_volume_1m)} | 5m={format_large_number(total_volume_5m)} | 1h={format_large_number(total_volume_1h)}")
                            logger.info(f"   买卖: 买={format_large_number(bv1m)} + 卖={format_large_number(sv1m)}")
                            logger.info(f"   TOP10: {t10*100:.2f}% | 流动性: {format_large_number(tr)} | 持有者: {h}")
                            
                            # 🔥 检查告警条件
                            # 数据合理性验证
                            is_valid_data = True
                            if abs(pc1m) > 500:  # 价格变化超过500%认为异常
                                logger.info(f"   ⚠️ 数据异常，跳过告警检查（价格变化: {pc1m:+.2f}%）")
                                is_valid_data = False
                            elif total_volume_1m < 1:  # 交易量几乎为0也可能是异常
                                logger.info(f"   ⚠️ 交易量过低，跳过告警检查（${total_volume_1m:,.0f}）")
                                is_valid_data = False
                            
                            if is_valid_data:
                                # 从数据库获取完整配置
                                db = DatabaseManager()
                                full_config_query = """
                                    SELECT * FROM sol_ws_batch_pool
                                    WHERE pair_address = %s
                                    LIMIT 1
                                """
                                full_configs = db.execute_query(full_config_query, (pair,))
                                
                                if full_configs:
                                    config = full_configs[0]
                                    should_alert, reasons, metrics = alert_checker.check_alert_conditions(
                                        item, config
                                    )
                                    
                                    if should_alert:
                                        logger.info(f"   🔔 触发告警！")
                                        for reason in reasons:
                                            logger.info(f"      • {reason}")
                                        
                                        # 发送Telegram（带按钮）
                                        message = alert_checker.format_alert_message(config, metrics, reasons)
                                        buttons = alert_checker.create_sol_buttons(ca, pair)  # 传入pair地址
                                        result = await telegram.send(
                                            target=-1003291885712,
                                            message=message,
                                            parse_mode=None,
                                            reply_markup=buttons
                                        )
                                        
                                        if result:
                                            logger.info(f"      ✅ Telegram推送成功")
                                            alert_count += 1
                                        else:
                                            logger.info(f"      ❌ Telegram推送失败")
                                        
                                        # 设置冷却期
                                        alert_checker.set_cooldown(ca)
                            
                            logger.info("")
                        
                        logger.info(f"{'='*80}\n")
                    else:
                        # 其他消息类型
                        logger.debug(f"🔍 其他消息: type={msg_type}")
                    
                except asyncio.TimeoutError:
                    # 超时，继续等待
                    continue
                
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"⚠️ WebSocket连接断开: {e}")
                    logger.info("测试结束（连接断开）")
                    break
                
                except Exception as e:
                    logger.error(f"❌ 处理消息失败: {e}")
                    break
            
            # 统计
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("\n" + "=" * 80)
            logger.info("测试完成")
            logger.info("=" * 80)
            logger.info(f"运行时长: {int(elapsed)}秒 ({elapsed/60:.1f}分钟)")
            logger.info(f"订阅批次: {len(batches)}")
            logger.info(f"订阅pair数: {total_pairs}")
            logger.info(f"订阅确认: {subscription_confirms}")
            logger.info(f"消息总数: {message_count}")
            logger.info(f"数据推送: {data_message_count}")
            logger.info(f"活跃pair数: {len(received_pairs)}/{total_pairs}")
            logger.info(f"触发告警: {alert_count}")
            if data_message_count > 0:
                logger.info(f"平均: {elapsed/data_message_count:.1f}秒/次")
            logger.info("=" * 80)
            
            # 判断结果
            logger.info("")
            if len(received_pairs) > 0:
                logger.info(f"✅ 测试成功！收到{len(received_pairs)}个活跃pair的数据")
                if alert_count > 0:
                    logger.info(f"🔔 触发{alert_count}次告警并推送到Telegram")
            else:
                logger.info("⚠️ 未收到数据，可能时间段不活跃")
            
    except Exception as e:
        logger.error(f"❌ 测试失败: {e}", exc_info=True)
    
    finally:
        # 清理
        redis_client.close()


if __name__ == "__main__":
    asyncio.run(test_separate_subscription())

