#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动 BSC WebSocket 监控器
"""
import asyncio
import logging
import sys
import os
import threading

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.solalert.monitor.bsc_websocket_monitor import BSCWebSocketMonitor

# Health Check Service
try:
    from src.solalert.monitoring.health import get_health_service
    HAS_HEALTH_CHECK = True
except ImportError:
    HAS_HEALTH_CHECK = False
    get_health_service = None

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/bsc_websocket.log', encoding='utf-8')
    ]
)

# 禁用第三方库日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def main():
    """主函数"""
    # WebSocket 和 RPC 配置
    # Chainstack 端点（25 RPS限制，已配置队列+令牌桶适配）
    WS_URL = "wss://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e"
    RPC_URL = "https://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e"
    

    logger.info("="*80)
    logger.info("🚀 启动 BSC WebSocket 监控器")
    logger.info("="*80)
    logger.info(f"📡 WebSocket: {WS_URL[:50]}...")
    logger.info(f"🔗 RPC: {RPC_URL[:50]}...")
    
    # 启动 Health Check Service（独立线程）
    if HAS_HEALTH_CHECK:
        try:
            health_service = get_health_service()
            health_port = int(os.getenv('HEALTH_CHECK_PORT', '8080'))
            
            # 在独立线程中启动健康检查服务（不阻塞主线程）
            health_thread = threading.Thread(
                target=health_service.run,
                kwargs={'host': '0.0.0.0', 'port': health_port},
                daemon=True,  # 守护线程，主程序退出时自动退出
                name='HealthCheckService'
            )
            health_thread.start()
            logger.info(f"🏥 Health Check Service: http://0.0.0.0:{health_port}")
            logger.info(f"   ├─ Liveness:  http://0.0.0.0:{health_port}/health")
            logger.info(f"   ├─ Readiness: http://0.0.0.0:{health_port}/ready")
            logger.info(f"   └─ Metrics:   http://0.0.0.0:{health_port}/metrics/health")
        except Exception as e:
            logger.warning(f"⚠️ Health Check Service 启动失败: {e}")
    else:
        logger.warning("⚠️ Health Check 模块未安装")
    
    logger.info("="*80)
    
    # 创建监控器
    monitor = BSCWebSocketMonitor(
        ws_url=WS_URL,
        rpc_url=RPC_URL,
        enable_telegram=True
    )
    
    # 启动监控
    await monitor.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⚠️  用户中断")
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}", exc_info=True)

