"""
Token监控系统测试脚本
测试各个模块的功能
"""
import sys
from pathlib import Path
import asyncio
import os

# 添加src目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from solalert.core.logger import setup_logger, get_logger
from solalert.core.database import get_db
from solalert.monitor.jupiter_api import JupiterAPI
from solalert.monitor.trigger_logic import TriggerLogic
from solalert.monitor.token_monitor import TokenMonitorEngine

logger = setup_logger()

# ============================================================
# 本地测试代理配置
# ============================================================
USE_PROXY = True  # 本地测试设置为True
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 1081

if USE_PROXY:
    # 设置环境变量，httpx和telegram会自动使用
    os.environ['HTTP_PROXY'] = f'socks5://{PROXY_HOST}:{PROXY_PORT}'
    os.environ['HTTPS_PROXY'] = f'socks5://{PROXY_HOST}:{PROXY_PORT}'
    logger.info(f"🔧 本地测试模式：使用代理 socks5://{PROXY_HOST}:{PROXY_PORT}")
    logger.info(f"   ✅ httpx 和 telegram 会自动使用代理")
# ============================================================


def test_database_connection():
    """测试数据库连接"""
    logger.info("=" * 80)
    logger.info("测试1: 数据库连接")
    logger.info("=" * 80)
    
    try:
        db = get_db()
        
        # 测试查询监控配置
        sql = """
        SELECT COUNT(*) as total
        FROM token_monitor_config
        WHERE status = '1' AND del_flag = '0'
        """
        result = db.execute_query(sql)
        
        if result:
            total = result[0]['total']
            logger.info(f"✅ 数据库连接成功")
            logger.info(f"📊 启用的监控配置数量: {total}")
            
            # 显示配置详情
            if total > 0:
                sql_detail = """
                SELECT id, ca, token_name, token_symbol, 
                       notify_methods, timer_interval
                FROM token_monitor_config
                WHERE status = '1' AND del_flag = '0'
                LIMIT 5
                """
                configs = db.execute_query(sql_detail)
                
                logger.info("\n前5个配置：")
                for config in configs:
                    logger.info(f"  ID={config['id']} | "
                              f"CA={config['ca'][:8]}... | "
                              f"Token={config.get('token_name', 'N/A')} | "
                              f"间隔={config.get('timer_interval', 1)}分钟")
        else:
            logger.warning("⚠️ 查询结果为空")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ 数据库测试失败: {e}")
        return False


async def test_jupiter_api():
    """测试Jupiter API"""
    logger.info("\n" + "=" * 80)
    logger.info("测试2: Jupiter API")
    logger.info("=" * 80)
    
    # 测试CA（DEAR token）
    test_ca = "3vreYfM6AxbEKdisdpuGpkPpxmtXn6Ema9unhxympump"
    
    try:
        async with JupiterAPI() as api:
            logger.info(f"🔍 测试获取Token数据: {test_ca[:16]}...")
            
            token_data = await api.get_token_data(test_ca)
            
            if token_data:
                logger.info("✅ API调用成功")
                logger.info(f"📊 Token信息:")
                logger.info(f"  名称: {token_data.get('name', 'N/A')}")
                logger.info(f"  符号: {token_data.get('symbol', 'N/A')}")
                logger.info(f"  价格: ${token_data.get('usdPrice', 0):.8f}")
                logger.info(f"  市值: ${token_data.get('mcap', 0):,.2f}")
                logger.info(f"  持币人数: {token_data.get('holderCount', 0)}")
                
                # stats5m数据
                stats = token_data.get('stats5m', {})
                if stats:
                    logger.info(f"\n📈 5分钟统计:")
                    logger.info(f"  价格变化: {stats.get('priceChange', 0):.2f}%")
                    logger.info(f"  持币人变化: {stats.get('holderChange', 0):.2f}%")
                    logger.info(f"  交易量变化: {stats.get('volumeChange', 0):.2f}%")
                    logger.info(f"  买入次数: {stats.get('numBuys', 0)}")
                    logger.info(f"  卖出次数: {stats.get('numSells', 0)}")
                else:
                    logger.warning("⚠️ 无stats5m数据")
                
                return True
            else:
                logger.error("❌ 未获取到Token数据")
                return False
                
    except Exception as e:
        logger.error(f"❌ Jupiter API测试失败: {e}")
        return False


def test_trigger_logic():
    """测试触发逻辑"""
    logger.info("\n" + "=" * 80)
    logger.info("测试3: 触发逻辑判断")
    logger.info("=" * 80)
    
    # 模拟stats数据
    stats = {
        "priceChange": 15.5,      # 上涨15.5%
        "holderChange": 8.2,      # 持币人增加8.2%
        "volumeChange": -20.3     # 交易量下降20.3%
    }
    
    # 测试配置
    events_config = {
        "priceChange": {
            "enabled": True,
            "risePercent": 10,
            "fallPercent": 10
        },
        "holders": {
            "enabled": True,
            "increasePercent": 30,
            "decreasePercent": 20
        },
        "volume": {
            "enabled": False,
            "increasePercent": None,
            "decreasePercent": None
        }
    }
    
    logger.info("📊 测试数据:")
    logger.info(f"  价格变化: {stats['priceChange']}%")
    logger.info(f"  持币人变化: {stats['holderChange']}%")
    logger.info(f"  交易量变化: {stats['volumeChange']}%")
    
    logger.info("\n📋 触发配置:")
    logger.info(f"  价格上涨阈值: ≥{events_config['priceChange']['risePercent']}%")
    logger.info(f"  持币人增加阈值: ≥{events_config['holders']['increasePercent']}%")
    
    # 测试any逻辑
    logger.info("\n🔍 测试触发逻辑 (any):")
    should_trigger, events = TriggerLogic.evaluate_trigger(
        stats, events_config, "any"
    )
    
    logger.info(f"  结果: {'✅ 触发' if should_trigger else '❌ 未触发'}")
    if events:
        logger.info(f"  触发事件数: {len(events)}")
        for event in events:
            logger.info(f"    - {event.description}")
    
    # 测试all逻辑
    logger.info("\n🔍 测试触发逻辑 (all):")
    should_trigger, events = TriggerLogic.evaluate_trigger(
        stats, events_config, "all"
    )
    
    logger.info(f"  结果: {'✅ 触发' if should_trigger else '❌ 未触发'}")
    
    return True


async def test_monitor_engine():
    """测试监控引擎（完整流程）"""
    logger.info("\n" + "=" * 80)
    logger.info("测试4: 监控引擎完整流程")
    logger.info("=" * 80)
    
    try:
        engine = TokenMonitorEngine()
        
        logger.info("🚀 执行一次监控任务...")
        result = await engine.run_monitor_once()
        
        logger.info("\n📊 执行结果:")
        logger.info(f"  配置总数: {result['total']}")
        logger.info(f"  触发监控: {result['triggered']}")
        logger.info(f"  发送通知: {result['notified']}")
        
        if result['total'] == 0:
            logger.warning("\n⚠️ 没有启用的监控配置")
            logger.info("💡 请在数据库中添加监控配置：")
            logger.info("   INSERT INTO token_monitor_config (...) VALUES (...)")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ 监控引擎测试失败: {e}", exc_info=True)
        return False


async def test_telegram_notification():
    """测试5: 完整流程模拟（触发逻辑+Telegram通知）"""
    logger.info("\n" + "=" * 80)
    logger.info("测试5: 完整监控流程模拟（使用测试数据触发真实通知）")
    logger.info("=" * 80)
    
    try:
        from solalert.monitor.notifiers import NotificationService
        from solalert.monitor.trigger_logic import TriggerLogic
        from datetime import datetime
        
        # 1. 构造模拟的监控配置（阈值设置低一点，容易触发）
        mock_config = {
            "ca": "3vreYfM6AxbEKdisdpuGpkPpxmtXn6Ema9unhxympump",
            "token_name": "DEAR",
            "token_symbol": "DEAR",
            "events_config": {
                "priceChange": {
                    "enabled": True,
                    "risePercent": 10,    # 上涨阈值10%
                    "fallPercent": 10
                },
                "holders": {
                    "enabled": True,
                    "increasePercent": 5,  # 持币人增加阈值5%
                    "decreasePercent": 20
                },
                "volume": {
                    "enabled": False
                }
            },
            "trigger_logic": "any",  # 任一条件满足即触发
            "remark": "测试监控"
        }
        
        # 2. 构造模拟的stats数据（满足触发条件）
        mock_stats = {
            "priceChange": 15.5,      # 价格上涨15.5% > 10%，会触发
            "holderChange": 8.2,      # 持币人增加8.2% > 5%，会触发
            "volumeChange": -20.3,
            "buyVolume": 1000,
            "sellVolume": 800,
            "numBuys": 50,
            "numSells": 30
        }
        
        logger.info("📊 模拟配置:")
        logger.info(f"  Token: {mock_config['token_name']} ({mock_config['token_symbol']})")
        logger.info(f"  触发逻辑: {mock_config['trigger_logic']}")
        logger.info(f"  价格上涨阈值: ≥{mock_config['events_config']['priceChange']['risePercent']}%")
        logger.info(f"  持币人增加阈值: ≥{mock_config['events_config']['holders']['increasePercent']}%")
        logger.info("")
        logger.info("📈 模拟数据:")
        logger.info(f"  价格变化: {mock_stats['priceChange']}%")
        logger.info(f"  持币人变化: {mock_stats['holderChange']}%")
        logger.info("")
        
        # 3. 使用TriggerLogic判断是否触发
        logger.info("🔍 执行触发判断...")
        should_trigger, triggered_events = TriggerLogic.evaluate_trigger(
            mock_stats,
            mock_config['events_config'],
            mock_config['trigger_logic']
        )
        
        if not should_trigger:
            logger.warning("⚠️ 未满足触发条件，跳过通知")
            return False
        
        logger.info(f"✅ 触发条件满足！触发事件数: {len(triggered_events)}")
        for event in triggered_events:
            logger.info(f"  - {event.description}")
        logger.info("")
        
        # 4. 构造模拟的token数据
        mock_token_data = {
            "usdPrice": 0.001234567890123456,
            "mcap": 1234567.89,
            "holderCount": 1234,
            "liquidity": 56789.12,
        }
        
        # 5. 使用NotificationMessage格式化消息
        logger.info("📤 构造并发送Telegram通知...")
        logger.info(f"   频道ID: -1002926135363")
        
        from solalert.monitor.notifiers import TelegramNotifier, NotificationMessage
        from solalert.core.config import TELEGRAM_CONFIG
        
        # 创建通知消息对象
        notification_msg = NotificationMessage(
            ca=mock_config['ca'],
            token_name=mock_config['token_name'],
            token_symbol=mock_config['token_symbol'],
            triggered_events=triggered_events,
            token_data=mock_token_data,
            remark=mock_config['remark']
        )
        
        # 格式化消息
        message = notification_msg.format_message()
        
        # 发送消息
        telegram = TelegramNotifier(
            bot_token=TELEGRAM_CONFIG['bot_token'],
            chat_id=-1002926135363
        )
        
        success = await telegram.send_message(message, ca=mock_config['ca'])
        
        if success:
            logger.info("✅ Telegram通知发送成功！")
            logger.info("💡 请在Telegram频道中查看测试消息")
            return True
        else:
            logger.error("❌ Telegram通知发送失败")
            return False
        
    except Exception as e:
        logger.error(f"❌ 完整流程测试失败: {e}", exc_info=True)
        return False


async def main():
    """主测试函数"""
    logger.info("🧪 开始Token监控系统测试\n")
    
    # 测试1: 数据库连接
    test1 = test_database_connection()
    
    # 测试2: Jupiter API
    test2 = await test_jupiter_api()
    
    # 测试3: 触发逻辑
    test3 = test_trigger_logic()
    
    # 测试4: 监控引擎
    test4 = await test_monitor_engine()
    
    # 测试5: Telegram通知
    test5 = await test_telegram_notification()
    
    # 总结
    logger.info("\n" + "=" * 80)
    logger.info("📊 测试总结")
    logger.info("=" * 80)
    logger.info(f"数据库连接: {'✅ 通过' if test1 else '❌ 失败'}")
    logger.info(f"Jupiter API: {'✅ 通过' if test2 else '❌ 失败'}")
    logger.info(f"触发逻辑: {'✅ 通过' if test3 else '❌ 失败'}")
    logger.info(f"监控引擎: {'✅ 通过' if test4 else '❌ 失败'}")
    logger.info(f"Telegram通知: {'✅ 通过' if test5 else '❌ 失败'}")
    
    all_passed = test1 and test2 and test3 and test4 and test5
    
    if all_passed:
        logger.info("\n🎉 所有测试通过！监控系统可以正常使用。")
        logger.info("\n使用方法:")
        logger.info("  python main.py --module token_monitor --once")
    else:
        logger.warning("\n⚠️ 部分测试失败，请检查配置和日志。")
    
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

