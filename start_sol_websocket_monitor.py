"""
SOL WebSocket 监控启动脚本
"""
import sys
import os
import asyncio
import logging
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from solalert.monitor.sol_websocket_manager import SolWebSocketManager


def setup_logging():
    """配置日志"""
    # 创建logs目录
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 日志文件名（按日期）
    log_file = os.path.join(log_dir, f'sol_websocket_{datetime.now().strftime("%Y%m%d")}.log')
    
    # 配置日志格式
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # 同时输出到文件和控制台
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    # 设置第三方库日志级别
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    # 设置我们自己的模块日志级别为INFO（显示详细信息）
    logging.getLogger('solalert.monitor').setLevel(logging.INFO)
    
    logger = logging.getLogger(__name__)
    logger.info(f"日志文件: {log_file}")
    
    return logger


async def main():
    """主函数"""
    logger = setup_logging()
    
    logger.info("=" * 80)
    logger.info(" " * 20 + "SOL WebSocket 监控系统")
    logger.info("=" * 80)
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)
    logger.info("")
    
    try:
        # 创建管理器
        manager = SolWebSocketManager()
        
        # 启动监控
        await manager.start()
        
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，正在退出...")
        
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
        
    finally:
        logger.info("程序已退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出")

