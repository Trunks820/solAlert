"""
MySQL 数据库连接池管理
提供统一的数据库连接和操作接口
"""
import mysql.connector
from mysql.connector import pooling, Error
from typing import Optional, Dict, Any, List, Tuple
from contextlib import contextmanager
import logging
from .config import DB_CONFIG

logger = logging.getLogger(__name__)


class DatabaseManager:
    """数据库连接池管理器"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化数据库管理器
        
        Args:
            config: 数据库配置字典，如果为None则使用默认配置
        """
        self.config = config or DB_CONFIG
        self.pool: Optional[pooling.MySQLConnectionPool] = None
        self._init_pool()
    
    def _init_pool(self):
        """初始化连接池"""
        try:
            pool_config = {
                'pool_name': 'solalert_pool',
                'pool_size': 10,
                'pool_reset_session': True,
                'host': self.config['host'],
                'port': self.config['port'],
                'user': self.config['user'],
                'password': self.config['password'],
                'database': self.config['database'],
                'charset': self.config.get('charset', 'utf8mb4'),
                'autocommit': False,
                'use_unicode': True,
            }
            
            self.pool = pooling.MySQLConnectionPool(**pool_config)
            logger.info(f"✅ 数据库连接池初始化成功: {self.config['host']}:{self.config['port']}/{self.config['database']}")
            
        except Error as e:
            logger.error(f"❌ 数据库连接池初始化失败: {e}")
            raise
    
    def get_connection(self):
        """
        从连接池获取数据库连接
        
        Returns:
            数据库连接对象
        """
        try:
            if not self.pool:
                self._init_pool()
            return self.pool.get_connection()
        except Error as e:
            logger.error(f"❌ 获取数据库连接失败: {e}")
            raise
    
    @contextmanager
    def get_cursor(self, dictionary: bool = True):
        """
        上下文管理器：获取数据库游标
        
        Args:
            dictionary: 是否返回字典格式的结果
            
        Yields:
            数据库游标对象
        """
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=dictionary)
            yield cursor
            connection.commit()
        except Error as e:
            if connection:
                connection.rollback()
            logger.error(f"❌ 数据库操作失败: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def execute_query(self, query: str, params: Optional[Tuple] = None, fetch_one: bool = False) -> Optional[List[Dict]]:
        """
        执行查询SQL
        
        Args:
            query: SQL查询语句
            params: 查询参数
            fetch_one: 是否只返回一条记录
            
        Returns:
            查询结果列表或单条记录
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute(query, params or ())
                if fetch_one:
                    return cursor.fetchone()
                return cursor.fetchall()
        except Error as e:
            logger.error(f"❌ 查询执行失败: {e}\nSQL: {query}")
            raise
    
    def execute_insert(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        执行插入SQL
        
        Args:
            query: SQL插入语句
            params: 插入参数
            
        Returns:
            插入记录的ID
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute(query, params or ())
                return cursor.lastrowid
        except Error as e:
            logger.error(f"❌ 插入执行失败: {e}\nSQL: {query}")
            raise
    
    def execute_update(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        执行更新SQL
        
        Args:
            query: SQL更新语句
            params: 更新参数
            
        Returns:
            影响的行数
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute(query, params or ())
                return cursor.rowcount
        except Error as e:
            logger.error(f"❌ 更新执行失败: {e}\nSQL: {query}")
            raise
    
    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """
        批量执行SQL
        
        Args:
            query: SQL语句
            params_list: 参数列表
            
        Returns:
            影响的行数
        """
        try:
            with self.get_cursor() as cursor:
                cursor.executemany(query, params_list)
                return cursor.rowcount
        except Error as e:
            logger.error(f"❌ 批量执行失败: {e}\nSQL: {query}")
            raise
    
    def test_connection(self) -> bool:
        """
        测试数据库连接
        
        Returns:
            连接是否成功
        """
        try:
            result = self.execute_query("SELECT 1 as test", fetch_one=True)
            if result and result.get('test') == 1:
                logger.info("✅ 数据库连接测试成功")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ 数据库连接测试失败: {e}")
            return False
    
    def close(self):
        """关闭连接池（通常不需要手动调用）"""
        if self.pool:
            logger.info("🔒 关闭数据库连接池")
            # MySQL连接池没有close方法，连接会自动管理


# 全局数据库管理器实例
db_manager = DatabaseManager()


# 便捷函数
def get_db() -> DatabaseManager:
    """获取数据库管理器实例"""
    return db_manager


def test_database_connection() -> bool:
    """测试数据库连接"""
    return db_manager.test_connection()

