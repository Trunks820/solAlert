#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动 BSC WebSocket 监控器
"""
import asyncio
import logging
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.solalert.monitor.bsc_websocket_monitor import BSCWebSocketMonitor

# Prometheus Metrics Server
try:
    from prometheus_client import start_http_server as prometheus_start_http_server
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

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
    
    # 启动 Prometheus Metrics Server
    if HAS_PROMETHEUS:
        try:
            prometheus_start_http_server(8001)
            logger.info(f"📊 Prometheus Metrics: http://0.0.0.0:8001/metrics")
        except Exception as e:
            logger.warning(f"⚠️ Prometheus Server 启动失败: {e}")
    else:
        logger.warning("⚠️ Prometheus未安装，Metrics功能不可用")
    
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

