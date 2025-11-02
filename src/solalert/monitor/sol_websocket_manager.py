"""
SOL WebSocket 监控管理器（主入口）
负责整体协调和管理
"""
import asyncio
import logging
from typing import Optional

from ..core.database import DatabaseManager
from ..core.redis_client import RedisClient
from ..core.config import REDIS_CONFIG
from ..notifiers.manager import get_notification_manager
from ..notifiers.alert_recorder import get_alert_recorder
from .sol_websocket_connection import SolWebSocketConnection

logger = logging.getLogger(__name__)


class SolWebSocketManager:
    """
    SOL WebSocket 监控管理器
    负责整体协调和管理
    """
    
    # DBotX WebSocket配置
    WS_URL = "wss://api-data-v1.dbotx.com/data/ws/"
    API_KEY = "i1o3elfavv59ds02fggj9rsd0eg8w657"
    
    def __init__(self):
        """初始化管理器"""
        self.db = None
        self.redis = None
        self.notification_service = None
        self.alert_recorder = None
        self.connection: Optional[SolWebSocketConnection] = None
    
    async def initialize(self):
        """初始化所有组件"""
        logger.info("\n" + "=" * 80)
        logger.info(" " * 20 + "SOL WebSocket 监控系统")
        logger.info("=" * 80)
        logger.info("")
        
        try:
            # 1. 初始化数据库
            logger.info("初始化数据库连接...")
            self.db = DatabaseManager()
            logger.info("✅ 数据库连接成功")
            
            # 2. 初始化Redis
            logger.info("初始化Redis连接...")
            self.redis = RedisClient(config=REDIS_CONFIG)
            logger.info("✅ Redis连接成功")
            
            # 3. 初始化通知服务
            logger.info("初始化通知服务...")
            self.notification_service = get_notification_manager()
            logger.info("✅ 通知服务初始化成功")
            
            # 4. 初始化告警记录器
            logger.info("初始化告警记录器...")
            self.alert_recorder = get_alert_recorder()
            logger.info("✅ 告警记录器初始化成功")
            
            # 5. 创建WebSocket连接
            logger.info("创建WebSocket连接...")
            self.connection = SolWebSocketConnection(
                ws_url=self.WS_URL,
                api_key=self.API_KEY,
                redis_client=self.redis,
                db_manager=self.db,
                notification_service=self.notification_service,
                alert_recorder=self.alert_recorder
            )
            logger.info("✅ WebSocket连接创建成功")
            logger.info("")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 初始化失败: {e}", exc_info=True)
            return False
    
    async def start(self):
        """启动监控"""
        try:
            # 初始化
            if not await self.initialize():
                logger.error("初始化失败，无法启动")
                return
            
            # 运行WebSocket连接
            await self.connection.run()
            
        except KeyboardInterrupt:
            logger.info("\n⚠️ 收到中断信号")
            await self.stop()
            
        except Exception as e:
            logger.error(f"❌ 运行出错: {e}", exc_info=True)
            await self.stop()
    
    async def stop(self):
        """停止监控"""
        logger.info("\n" + "=" * 80)
        logger.info("正在停止监控...")
        logger.info("=" * 80)
        
        try:
            # 关闭WebSocket连接
            if self.connection:
                await self.connection.close()
            
            # 关闭Redis连接
            if self.redis:
                self.redis.close()
                logger.info("✅ Redis连接已关闭")
            
            logger.info("✅ 监控已停止")
            logger.info("=" * 80)
            logger.info("")
            
        except Exception as e:
            logger.error(f"停止时出错: {e}", exc_info=True)


async def main():
    """主函数"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 创建并启动管理器
    manager = SolWebSocketManager()
    await manager.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出")

