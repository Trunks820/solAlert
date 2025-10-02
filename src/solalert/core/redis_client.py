"""
Redis 客户端管理
提供统一的 Redis 连接和操作接口
"""
import redis
from typing import Optional, Any, Dict, List
import json
import logging
from .config import REDIS_CONFIG

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis 客户端管理器"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化 Redis 客户端
        
        Args:
            config: Redis配置字典，如果为None则使用默认配置
        """
        self.config = config or REDIS_CONFIG
        self.client: Optional[redis.Redis] = None
        self._init_client()
    
    def _init_client(self):
        """初始化 Redis 客户端"""
        try:
            pool = redis.ConnectionPool(
                host=self.config['host'],
                port=self.config['port'],
                password=self.config.get('password'),
                db=self.config.get('db', 0),
                decode_responses=self.config.get('decode_responses', True),
                socket_timeout=self.config.get('socket_timeout', 15),
                socket_connect_timeout=self.config.get('socket_connect_timeout', 15),
                retry_on_timeout=self.config.get('retry_on_timeout', True),
                health_check_interval=self.config.get('health_check_interval', 60),
                max_connections=self.config.get('max_connections', 20),
            )
            
            self.client = redis.Redis(connection_pool=pool)
            logger.info(f"✅ Redis客户端初始化成功: {self.config['host']}:{self.config['port']}")
            
        except Exception as e:
            logger.error(f"❌ Redis客户端初始化失败: {e}")
            raise
    
    def ping(self) -> bool:
        """
        测试 Redis 连接
        
        Returns:
            连接是否成功
        """
        try:
            result = self.client.ping()
            if result:
                logger.info("✅ Redis连接测试成功")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Redis连接测试失败: {e}")
            return False
    
    # ==================== 基础操作 ====================
    
    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """
        设置键值
        
        Args:
            key: 键名
            value: 值（自动JSON序列化）
            ex: 过期时间（秒）
            
        Returns:
            是否成功
        """
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            return self.client.set(key, value, ex=ex)
        except Exception as e:
            logger.error(f"❌ Redis SET 失败: {key}, {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取键值
        
        Args:
            key: 键名
            default: 默认值
            
        Returns:
            键值或默认值
        """
        try:
            value = self.client.get(key)
            if value is None:
                return default
            
            # 尝试JSON反序列化
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"❌ Redis GET 失败: {key}, {e}")
            return default
    
    def delete(self, *keys: str) -> int:
        """
        删除键
        
        Args:
            keys: 键名列表
            
        Returns:
            删除的键数量
        """
        try:
            return self.client.delete(*keys)
        except Exception as e:
            logger.error(f"❌ Redis DELETE 失败: {keys}, {e}")
            return 0
    
    def exists(self, *keys: str) -> int:
        """
        检查键是否存在
        
        Args:
            keys: 键名列表
            
        Returns:
            存在的键数量
        """
        try:
            return self.client.exists(*keys)
        except Exception as e:
            logger.error(f"❌ Redis EXISTS 失败: {keys}, {e}")
            return 0
    
    def expire(self, key: str, seconds: int) -> bool:
        """
        设置键过期时间
        
        Args:
            key: 键名
            seconds: 秒数
            
        Returns:
            是否成功
        """
        try:
            return self.client.expire(key, seconds)
        except Exception as e:
            logger.error(f"❌ Redis EXPIRE 失败: {key}, {e}")
            return False
    
    def ttl(self, key: str) -> int:
        """
        获取键剩余过期时间
        
        Args:
            key: 键名
            
        Returns:
            剩余秒数，-1表示永不过期，-2表示不存在
        """
        try:
            return self.client.ttl(key)
        except Exception as e:
            logger.error(f"❌ Redis TTL 失败: {key}, {e}")
            return -2
    
    # ==================== Hash 操作 ====================
    
    def hset(self, name: str, key: str, value: Any) -> int:
        """设置哈希表字段"""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            return self.client.hset(name, key, value)
        except Exception as e:
            logger.error(f"❌ Redis HSET 失败: {name}.{key}, {e}")
            return 0
    
    def hget(self, name: str, key: str, default: Any = None) -> Any:
        """获取哈希表字段"""
        try:
            value = self.client.hget(name, key)
            if value is None:
                return default
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"❌ Redis HGET 失败: {name}.{key}, {e}")
            return default
    
    def hgetall(self, name: str) -> Dict:
        """获取哈希表所有字段"""
        try:
            return self.client.hgetall(name)
        except Exception as e:
            logger.error(f"❌ Redis HGETALL 失败: {name}, {e}")
            return {}
    
    # ==================== Set 操作 ====================
    
    def sadd(self, name: str, *values: Any) -> int:
        """添加集合成员"""
        try:
            return self.client.sadd(name, *values)
        except Exception as e:
            logger.error(f"❌ Redis SADD 失败: {name}, {e}")
            return 0
    
    def srem(self, name: str, *values: Any) -> int:
        """移除集合成员"""
        try:
            return self.client.srem(name, *values)
        except Exception as e:
            logger.error(f"❌ Redis SREM 失败: {name}, {e}")
            return 0
    
    def smembers(self, name: str) -> set:
        """获取集合所有成员"""
        try:
            return self.client.smembers(name)
        except Exception as e:
            logger.error(f"❌ Redis SMEMBERS 失败: {name}, {e}")
            return set()
    
    def sismember(self, name: str, value: Any) -> bool:
        """检查是否是集合成员"""
        try:
            return self.client.sismember(name, value)
        except Exception as e:
            logger.error(f"❌ Redis SISMEMBER 失败: {name}, {e}")
            return False
    
    # ==================== List 操作 ====================
    
    def lpush(self, name: str, *values: Any) -> int:
        """从左侧插入列表"""
        try:
            return self.client.lpush(name, *values)
        except Exception as e:
            logger.error(f"❌ Redis LPUSH 失败: {name}, {e}")
            return 0
    
    def rpush(self, name: str, *values: Any) -> int:
        """从右侧插入列表"""
        try:
            return self.client.rpush(name, *values)
        except Exception as e:
            logger.error(f"❌ Redis RPUSH 失败: {name}, {e}")
            return 0
    
    def lrange(self, name: str, start: int, end: int) -> List:
        """获取列表范围"""
        try:
            return self.client.lrange(name, start, end)
        except Exception as e:
            logger.error(f"❌ Redis LRANGE 失败: {name}, {e}")
            return []
    
    def ltrim(self, name: str, start: int, end: int) -> bool:
        """修剪列表"""
        try:
            return self.client.ltrim(name, start, end)
        except Exception as e:
            logger.error(f"❌ Redis LTRIM 失败: {name}, {e}")
            return False
    
    # ==================== 业务专用方法 ====================
    
    def set_market_data(self, ca: str, data: Dict, ttl: int = 30) -> bool:
        """
        缓存市场数据
        
        Args:
            ca: 合约地址
            data: 市场数据
            ttl: 过期时间（秒）
        """
        key = f"market:data:{ca}"
        return self.set(key, data, ex=ttl)
    
    def get_market_data(self, ca: str) -> Optional[Dict]:
        """获取缓存的市场数据"""
        key = f"market:data:{ca}"
        return self.get(key)
    
    def set_cooldown(self, config_id: int, alert_type: str, minutes: int) -> bool:
        """
        设置告警冷却时间
        
        Args:
            config_id: 配置ID
            alert_type: 告警类型
            minutes: 冷却分钟数
        """
        key = f"cooldown:{config_id}:{alert_type}"
        return self.set(key, "1", ex=minutes * 60)
    
    def is_in_cooldown(self, config_id: int, alert_type: str) -> bool:
        """检查是否在冷却期"""
        key = f"cooldown:{config_id}:{alert_type}"
        return self.exists(key) > 0
    
    def add_active_monitor(self, config_id: int) -> int:
        """添加活跃监控配置"""
        return self.sadd("monitor:active:configs", str(config_id))
    
    def remove_active_monitor(self, config_id: int) -> int:
        """移除活跃监控配置"""
        return self.srem("monitor:active:configs", str(config_id))
    
    def get_active_monitors(self) -> set:
        """获取所有活跃监控配置ID"""
        return self.smembers("monitor:active:configs")
    
    def close(self):
        """关闭 Redis 连接"""
        if self.client:
            self.client.close()
            logger.info("🔒 Redis连接已关闭")


# 全局 Redis 客户端实例
redis_client = RedisClient()


# 便捷函数
def get_redis() -> RedisClient:
    """获取 Redis 客户端实例"""
    return redis_client


def test_redis_connection() -> bool:
    """测试 Redis 连接"""
    return redis_client.ping()

