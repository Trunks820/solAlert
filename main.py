"""
solAlert 主入口文件
启动各个模块的服务
"""
import asyncio
import sys
import argparse
import logging
from pathlib import Path

# 添加src目录到Python路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from solalert.core.logger import setup_logger
from solalert.core.config import get_config_summary, BSC_MONITOR_CONFIG
from solalert.core.database import test_database_connection
from solalert.collectors.pump_listener import PumpListener
from solalert.collectors.bonk_collector import BonkCollector
from solalert.collectors.fourmeme_listener import FourMemeListener
from solalert.collectors.bsc_collector import BSCBlockCollector
from solalert.tasks.twitter_push_sync import TwitterPushSyncService
from solalert.monitor.token_monitor import TokenMonitorEngine
from solalert.monitor.bsc_monitor import BSCMonitor

# 设置日志
logger = setup_logger()


def print_banner():
    """打印启动横幅"""
    banner = """
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║      _____ ____  _        _    _           _               ║
║     / ____/ __ \\| |      / \\  | |         | |              ║
║    | (___| |  | | |     / _ \\ | | ___ _ __| |_             ║
║     \\___ \\| |  | | |    / ___ \\| |/ _ \\ '__| __|            ║
║     ____) | |__| | |___/ /   \\ \\ |  __/ |  | |_             ║
║    |_____/ \\____/|_____/_/     \\_\\_|\\___|_|   \\__|            ║
║                                                            ║
║      多链Token监控预警系统 v0.2.0                           ║
║      Solana + BSC | Data Collection + Monitoring           ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
    """
    print(banner)


def check_dependencies():
    """检查依赖和配置"""
    logger.info("🔍 检查系统依赖和配置...")
    
    # 检查数据库连接
    logger.info("检查数据库连接...")
    if test_database_connection():
        logger.info("✅ 数据库连接正常")
    else:
        logger.error("❌ 数据库连接失败")
        return False
    
    # 打印配置摘要
    config_summary = get_config_summary()
    logger.info("📋 配置摘要:")
    logger.info(f"   - 数据库: {config_summary['database']['host']}:{config_summary['database']['port']}")
    logger.info(f"   - Redis: {config_summary['redis']['host']}:{config_summary['redis']['port']}")
    logger.info(f"   - Telegram代理: {'启用' if config_summary['telegram']['proxy_enabled'] else '禁用'}")
    logger.info(f"   - 微信通知: {'启用' if config_summary['wechat']['enabled'] else '禁用'}")
    
    return True


async def run_pump_listener(mode: str = "listen"):
    """
    运行Pump监听器
    
    Args:
        mode: 运行模式 (listen: 实时监听, history: 历史采集)
    """
    logger.info(f"🚀 启动 Pump 监听器 (模式: {mode})")
    
    listener = PumpListener()
    
    try:
        if mode == "history":
            # 采集历史数据
            await listener.collect_history()
        else:
            # 实时监听
            await listener.start()
    except KeyboardInterrupt:
        logger.info("⏹️  用户停止服务")
    except Exception as e:
        logger.error(f"❌ 服务运行失败: {e}", exc_info=True)
    finally:
        await listener.stop()


async def run_bonk_collector(poll_interval: int = 60):
    """
    运行BONK采集器
    
    Args:
        poll_interval: 轮询间隔（秒），默认60秒
    """
    logger.info(f"🚀 启动 BONK 采集器 (轮询间隔: {poll_interval}秒)")
    
    collector = BonkCollector(poll_interval=poll_interval)
    
    try:
        await collector.start()
    except KeyboardInterrupt:
        logger.info("⏹️  用户停止服务")
    except Exception as e:
        logger.error(f"❌ 服务运行失败: {e}", exc_info=True)
    finally:
        await collector.stop()


async def run_fourmeme_listener(mode: str = "listen", days: int = None, limit: int = None):
    """
    运行Four.meme Telegram监听器
    
    Args:
        mode: 运行模式 (listen=实时监听, history=历史采集)
        days: 历史采集天数 (None=全部历史)
        limit: 历史采集最大消息数 (None=不限制)
    """
    listener = FourMemeListener()
    
    try:
        if mode == "history":
            logger.info(f"🚀 启动 Four.meme Telegram 历史采集器")
            await listener.collect_history(days=days, limit=limit)
        else:
            logger.info(f"🚀 启动 Four.meme Telegram 实时监听器")
            await listener.start()
    except KeyboardInterrupt:
        logger.info("⏹️  用户停止服务")
    except Exception as e:
        logger.error(f"❌ 服务运行失败: {e}", exc_info=True)
    finally:
        await listener.stop()


def run_twitter_push_sync(interval: int = 600, once: bool = False):
    """
    运行Twitter推送同步任务
    
    Args:
        interval: 执行间隔（秒），默认600秒（10分钟）
        once: 是否只执行一次
    """
    service = TwitterPushSyncService()
    
    if once:
        logger.info(f"🚀 执行 Twitter推送同步任务（一次）")
        service.run_sync_once()
    else:
        logger.info(f"🚀 启动 Twitter推送同步任务 (间隔: {interval}秒 = {interval//60}分钟)")
        service.run_schedule(interval_seconds=interval)


async def run_token_monitor(interval: int = 1, once: bool = False):
    """
    运行Token监控任务（支持 Jupiter API 和 GMGN API）
    
    Args:
        interval: 执行间隔（分钟），默认1分钟
        once: 是否只执行一次
    """
    monitor = TokenMonitorEngine()
    
    if once:
        logger.info(f"🚀 执行 Token监控任务（一次）")
        await monitor.run_monitor_once()
    else:
        logger.info(f"🚀 启动 Token监控任务 (间隔: {interval}分钟)")
        await monitor.run_monitor_schedule(interval_minutes=interval)


def run_bsc_monitor():
    """
    运行BSC链监控任务（Alchemy Webhook 实时推送 + 三层过滤）
    """
    logger.info("🚀 启动 BSC Webhook 监控服务")
    
    # 优化第三方库日志
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('web3').setLevel(logging.ERROR)
    logging.getLogger('asyncio').setLevel(logging.CRITICAL)  # 禁用 asyncio 错误日志（Windows噪音）
    logging.getLogger('httpx').setLevel(logging.ERROR)
    logging.getLogger('httpcore').setLevel(logging.ERROR)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.error').setLevel(logging.WARNING)
    
    try:
        # 1. 初始化监控器
        from solalert.api.alchemy_webhook import webhook_handler, start_webhook_server
        
        monitor = BSCMonitor(config=BSC_MONITOR_CONFIG)
        webhook_handler.set_monitor(monitor)
        
        # 2. 显示配置
        logger.info("=" * 80)
        logger.info(f"📊 监控配置:")
        logger.info(f"   单笔阈值: {monitor.single_max_usdt} USDT | 累计阈值: {monitor.block_accumulate_usdt} USDT | 冷却: {monitor.min_interval_seconds}秒")
        logger.info(f"   平台: fourmeme | 数据源: DBotX API (1分钟实时)")
        logger.info(f"\n📡 服务地址:")
        logger.info(f"   Webhook: http://0.0.0.0:8001/webhook/alchemy/bsc")
        logger.info(f"   健康检查: http://0.0.0.0:8001/health")
        logger.info(f"\n💡 过滤流程: 交易对 → 金额 → fourmeme → DBotX指标 → 冷却期")
        logger.info("=" * 80)
        logger.info("✅ 服务已启动，等待 Alchemy Webhook 推送...")
        
        # 3. 启动 Webhook 服务器
        start_webhook_server(host="0.0.0.0", port=8001)
        
    except KeyboardInterrupt:
        logger.info("\n⏹️  用户停止服务")
    except Exception as e:
        logger.error(f"❌ BSC Webhook 监控运行失败: {e}", exc_info=True)
        raise


async def run_all_services():
    """运行所有服务（数据采集器 + Token监控 + BSC Webhook监控）"""
    logger.info("🚀 启动所有服务...")
    
    # 优化第三方库日志
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('web3').setLevel(logging.ERROR)
    logging.getLogger('asyncio').setLevel(logging.CRITICAL)
    logging.getLogger('httpx').setLevel(logging.ERROR)
    logging.getLogger('httpcore').setLevel(logging.ERROR)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.error').setLevel(logging.WARNING)
    
    # 创建采集器和监控实例
    pump_listener = PumpListener()
    bonk_collector = BonkCollector(poll_interval=60)
    fourmeme_listener = FourMemeListener()
    token_monitor = TokenMonitorEngine()
    
    # 初始化 BSC Webhook 监控
    from solalert.api.alchemy_webhook import webhook_handler, start_webhook_server_async
    bsc_monitor = BSCMonitor(config=BSC_MONITOR_CONFIG)
    webhook_handler.set_monitor(bsc_monitor)
    
    logger.info("=" * 80)
    logger.info("📊 BSC监控配置:")
    logger.info(f"   单笔阈值: {bsc_monitor.single_max_usdt} USDT | 累计阈值: {bsc_monitor.block_accumulate_usdt} USDT")
    logger.info(f"   Webhook: http://0.0.0.0:8001/webhook/alchemy/bsc")
    logger.info("=" * 80)
    
    # 并发运行所有服务（包括 BSC Webhook）
    try:
        await asyncio.gather(
            pump_listener.start(),
            bonk_collector.start(),
            fourmeme_listener.start(),
            token_monitor.run_monitor_schedule(interval_minutes=1),  # 1分钟间隔监控
            start_webhook_server_async(host="0.0.0.0", port=8001),  # BSC Webhook 监控
            return_exceptions=True
        )
    except KeyboardInterrupt:
        logger.info("⏹️  用户停止所有服务")
    except Exception as e:
        logger.error(f"❌ 服务运行失败: {e}", exc_info=True)
    finally:
        # 停止所有服务
        await asyncio.gather(
            pump_listener.stop(),
            bonk_collector.stop(),
            fourmeme_listener.stop(),
            return_exceptions=True
        )


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="solAlert - Solana Token 监控预警系统")
    parser.add_argument(
        "--module",
        choices=["pump_listener", "bonk_collector", "fourmeme_listener", "twitter_push_sync", "token_monitor", "bsc_monitor", "all"],
        default="pump_listener",
        help="要启动的模块 (默认: pump_listener)"
    )
    parser.add_argument(
        "--mode",
        choices=["listen", "history"],
        default="listen",
        help="监听器模式: listen(实时) 或 history(历史采集)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="采集器/任务轮询间隔(秒): BONK默认60, Twitter推送同步默认600"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Twitter推送同步：只执行一次，不循环"
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="跳过依赖检查"
    )
    
    args = parser.parse_args()
    
    # 打印横幅
    print_banner()
    
    # 检查依赖
    if not args.no_check:
        if not check_dependencies():
            logger.error("❌ 依赖检查失败，程序退出")
            sys.exit(1)
    
    # 根据参数启动对应模块
    try:
        if args.module == "pump_listener":
            asyncio.run(run_pump_listener(args.mode))
        elif args.module == "bonk_collector":
            asyncio.run(run_bonk_collector(args.interval))
        elif args.module == "fourmeme_listener":
            asyncio.run(run_fourmeme_listener(args.mode))
        elif args.module == "twitter_push_sync":
            # Twitter推送同步任务，默认600秒（10分钟）
            interval = args.interval if args.interval != 60 else 600
            run_twitter_push_sync(interval, once=args.once)
        elif args.module == "token_monitor":
            # Token监控任务，默认1分钟间隔
            interval = args.interval if args.interval != 60 else 1
            asyncio.run(run_token_monitor(interval, once=args.once))
        elif args.module == "bsc_monitor":
            # BSC Webhook 监控任务（Alchemy 实时推送）
            run_bsc_monitor()
        elif args.module == "all":
            asyncio.run(run_all_services())
    except KeyboardInterrupt:
        logger.info("\n⏹️  服务已停止")
    except Exception as e:
        logger.error(f"❌ 程序异常退出: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

