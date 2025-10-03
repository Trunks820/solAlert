"""
MySQL 数据库连接池管理
提供统一的数据库连接和操作接口
使用pymysql替代mysql-connector-python以提高稳定性
"""
import pymysql
from pymysql.cursors import DictCursor
from typing import Optional, Dict, Any, List, Tuple
import logging
import queue
import threading
import time
from .config import DB_CONFIG

logger = logging.getLogger(__name__)


def _get_db_connection_config():
    """获取标准数据库连接配置"""
    return {
        'host': DB_CONFIG['host'],
        'port': DB_CONFIG['port'],
        'user': DB_CONFIG['user'],
        'password': DB_CONFIG['password'],
        'database': DB_CONFIG['database'],
        'charset': DB_CONFIG.get('charset', 'utf8mb4'),
        'cursorclass': DictCursor,
        'connect_timeout': 10,
        'read_timeout': 15,
        'write_timeout': 15,
        'autocommit': True,
        'use_unicode': True
    }


class SimpleConnectionPool:
    """简单的数据库连接池实现"""
    
    def __init__(self, max_size=20):
        self.max_size = max_size
        self.pool = queue.Queue(maxsize=max_size)
        self.created_connections = 0
        self.lock = threading.Lock()
        logger.info(f"数据库连接池初始化完成，最大连接数: {max_size}")
    
    def _create_connection(self):
        """创建新的数据库连接"""
        try:
            connection = pymysql.connect(**_get_db_connection_config())
            self.created_connections += 1
            logger.debug("数据库连接创建成功")
            return connection
        except pymysql.err.OperationalError as e:
            logger.error(f"数据库连接失败（网络或认证问题）: {e}")
            return None
        except Exception as e:
            logger.error(f"创建数据库连接失败: {e}")
            return None
    
    def get_connection(self):
        """获取连接，带重试机制"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 尝试从池中获取连接
                connection = self.pool.get_nowait()
                
                # 检查连接是否有效
                if self._is_connection_valid(connection):
                    return connection
                else:
                    # 连接无效，创建新连接
                    new_conn = self._create_connection()
                    if new_conn:
                        return new_conn
                    
            except queue.Empty:
                # 池中没有连接，创建新连接
                with self.lock:
                    if self.created_connections < self.max_size:
                        new_conn = self._create_connection()
                        if new_conn:
                            return new_conn
                    else:
                        # 达到最大连接数，等待并重试
                        try:
                            connection = self.pool.get(timeout=10)
                            if self._is_connection_valid(connection):
                                return connection
                        except queue.Empty:
                            logger.warning("连接池等待超时(10秒)")
            
            # 如果连接失败，记录并重试
            logger.warning(f"无法获取数据库连接 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(1)
        
        logger.error("所有数据库连接尝试都失败了")
        return None
    
    def _is_connection_valid(self, connection):
        """检查连接是否有效"""
        if not connection:
            return False
        try:
            connection.ping(reconnect=True)
            return True
        except Exception:
            return False
    
    def return_connection(self, connection):
        """归还连接到池中"""
        if connection and self._is_connection_valid(connection):
            try:
                self.pool.put_nowait(connection)
            except queue.Full:
                connection.close()
                with self.lock:
                    self.created_connections -= 1
        elif connection:
            connection.close()
            with self.lock:
                self.created_connections -= 1


class DatabaseManager:
    """数据库管理类，提供数据库连接和基本操作"""
    _instance = None
    _pool = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(DatabaseManager, cls).__new__(cls)
                    cls._instance._initialize_pool()
        return cls._instance

    def _initialize_pool(self):
        """初始化连接池"""
        try:
            self._pool = SimpleConnectionPool(max_size=20)
            logger.info(f"✅ 数据库连接池初始化成功: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
        except Exception as e:
            logger.error(f"❌ 初始化数据库连接池失败: {e}")
            raise

    def get_connection(self):
        """从连接池获取数据库连接"""
        try:
            if self._pool is None:
                self._initialize_pool()
            return self._pool.get_connection()
        except Exception as e:
            logger.error(f"从连接池获取数据库连接失败: {e}")
            logger.warning("连接池获取失败，创建临时连接")
            return pymysql.connect(**_get_db_connection_config())

    def execute_query(self, query: str, params: Tuple = (), fetch_one: bool = False) -> Optional[List[Dict]]:
        """执行查询操作，带重试机制"""
        max_retries = 3
        for attempt in range(max_retries):
            conn = None
            try:
                conn = self.get_connection()
                if conn is None:
                    logger.error(f"无法获取数据库连接 (尝试 {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    return [] if not fetch_one else None
                
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    if fetch_one:
                        return cursor.fetchone()
                    result = cursor.fetchall()
                    return result if result else []
                    
            except (pymysql.OperationalError, pymysql.InterfaceError) as e:
                logger.warning(f"数据库连接错误 (尝试 {attempt + 1}/{max_retries}): {e}")
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
                    conn = None
                
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    logger.error(f"查询执行失败，已重试{max_retries}次 - SQL: {query}, 错误: {e}")
                    return [] if not fetch_one else None
                    
            except Exception as e:
                logger.error(f"查询执行失败 - SQL: {query}, 错误: {e}")
                return [] if not fetch_one else None
            finally:
                if conn and self._pool:
                    self._pool.return_connection(conn)
                elif conn:
                    conn.close()
            
            break
        
        return [] if not fetch_one else None

    def execute_update(self, query: str, params: Tuple = ()) -> int:
        """执行更新操作"""
        conn = None
        try:
            conn = self.get_connection()
            if conn is None:
                logger.error("无法获取数据库连接")
                return 0
                
            with conn.cursor() as cursor:
                affected_rows = cursor.execute(query, params)
                conn.commit()
                return affected_rows
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ 数据库操作失败: {e}")
            raise
        finally:
            if conn and self._pool:
                self._pool.return_connection(conn)
            elif conn:
                conn.close()

    def test_connection(self) -> bool:
        """测试数据库连接"""
        try:
            result = self.execute_query("SELECT 1 as test", fetch_one=True)
            if result and result.get('test') == 1:
                logger.info("✅ 数据库连接测试成功")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ 数据库连接测试失败: {e}")
            return False


# 全局数据库管理器实例
db_manager = DatabaseManager()


# 便捷函数
def get_db() -> DatabaseManager:
    """获取数据库管理器实例"""
    return db_manager


def test_database_connection() -> bool:
    """测试数据库连接"""
    return db_manager.test_connection()
