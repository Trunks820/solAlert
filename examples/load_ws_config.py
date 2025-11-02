"""
SOL WebSocket监控配置加载示例
演示如何从Redis加载模板配置并初始化缓存
"""
import sys
import os
import json
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from solalert.core.redis_client import RedisClient
from solalert.core.database import DatabaseManager
from solalert.core.config import REDIS_CONFIG, DB_CONFIG


class SolWsConfigLoader:
    """SOL WebSocket配置加载器"""
    
    def __init__(self):
        self.redis_wrapper = RedisClient(config=REDIS_CONFIG)
        self.redis = self.redis_wrapper.client
        self.db = DatabaseManager()  # 单例模式，直接实例化
        self.templates = []
        self.batch_configs = []
    
    def load_templates_from_redis(self):
        """从Redis加载模板配置"""
        print("=" * 80)
        print("步骤1: 从Redis加载模板配置")
        print("=" * 80)
        
        key = "quick_monitor:template:sol"
        value = self.redis.get(key)
        
        if not value:
            print("❌ 未找到模板配置")
            return False
        
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        
        # 处理Java Long类型
        value_cleaned = value.replace('L,', ',').replace('L}', '}').replace('L]', ']')
        self.templates = json.loads(value_cleaned)
        
        print(f"✅ 成功加载 {len(self.templates)} 个模板配置\n")
        
        for template in self.templates:
            print(f"📋 {template['configName']} (ID: {template['id']})")
            print(f"   市值要求: ≥ ${template['minMarketCap']:,.0f}")
            print(f"   时间间隔: {template['timeInterval']}")
            print(f"   触发逻辑: {template['triggerLogic']}")
            
            # 解析eventsConfig
            events = json.loads(template['eventsConfig'])
            print(f"   监控指标:")
            if events.get('priceChange', {}).get('enabled'):
                rise = events['priceChange'].get('risePercent')
                if rise:
                    print(f"     - 价格上涨: ≥{rise}%")
            if events.get('volume', {}).get('enabled'):
                threshold = events['volume'].get('threshold')
                if threshold:
                    print(f"     - 交易量: ≥${threshold:,.0f}")
            print()
        
        return True
    
    def load_batch_pool_from_db(self):
        """从数据库加载批次池数据"""
        print("=" * 80)
        print("步骤2: 从数据库加载批次池数据")
        print("=" * 80)
        
        query = """
            SELECT * FROM sol_ws_batch_pool
            WHERE is_active = 1
            ORDER BY batch_id, sort_order
        """
        
        self.batch_configs = self.db.execute_query(query)
        
        if not self.batch_configs:
            print("❌ 未找到批次池数据")
            return False
        
        print(f"✅ 成功加载 {len(self.batch_configs)} 个CA配置\n")
        
        # 统计各批次数量
        batch_stats = {}
        for config in self.batch_configs:
            batch_id = config['batch_id']
            batch_stats[batch_id] = batch_stats.get(batch_id, 0) + 1
        
        print(f"批次分布:")
        for batch_id in sorted(batch_stats.keys()):
            count = batch_stats[batch_id]
            print(f"  Batch {batch_id:2d}: {count:2d} 个CA")
        
        print()
        return True
    
    def write_to_redis_cache(self):
        """将配置写入Redis缓存"""
        print("=" * 80)
        print("步骤3: 写入Redis缓存")
        print("=" * 80)
        
        pipeline = self.redis.pipeline()
        
        for config in self.batch_configs:
            pair = config['pair_address']
            ca = config['ca']
            batch_id = config['batch_id']
            
            # 1. 配置缓存（Hash）
            config_key = f"quick_monitor:ws:config:{pair}"
            pipeline.hset(config_key, mapping={
                'ca': ca,
                'token_symbol': config.get('token_symbol', ''),
                'token_name': config.get('token_name', ''),
                'pair_address': pair,
                'batch_id': batch_id,
                'template_id': config['template_id'],
                'template_name': config.get('template_name', ''),
                'time_interval': config['time_interval'],
                'events_config': config['events_config'],
                'trigger_logic': config['trigger_logic'],
                'priority': config['priority'],
                'market_cap': float(config.get('market_cap', 0)),
                'twitter_url': config.get('twitter_url', ''),
            })
            
            # 2. CA到Pair映射
            pipeline.set(f"quick_monitor:ws:ca_pair:{ca}", pair)
            
            # 3. 批次索引
            pipeline.sadd(f"quick_monitor:ws:batch:{batch_id}", pair)
        
        # 4. 设置版本号
        current_time = datetime.now().timestamp()
        pipeline.set('quick_monitor:ws:version', current_time)
        
        # 执行Pipeline
        pipeline.execute()
        
        print(f"✅ 已写入 {len(self.batch_configs)} 个配置到Redis")
        print(f"   - 配置缓存: quick_monitor:ws:config:* ({len(self.batch_configs)} 个)")
        print(f"   - CA映射: quick_monitor:ws:ca_pair:* ({len(self.batch_configs)} 个)")
        
        # 统计批次数
        batch_count = len(set(c['batch_id'] for c in self.batch_configs))
        print(f"   - 批次索引: quick_monitor:ws:batch:* ({batch_count} 个)")
        print(f"   - 版本号: quick_monitor:ws:version")
        print()
    
    def verify_cache(self):
        """验证缓存是否正确写入"""
        print("=" * 80)
        print("步骤4: 验证Redis缓存")
        print("=" * 80)
        
        # 随机抽取一个配置验证
        if self.batch_configs:
            sample = self.batch_configs[0]
            pair = sample['pair_address']
            ca = sample['ca']
            batch_id = sample['batch_id']
            
            print(f"验证样本:")
            print(f"  CA: {ca}")
            print(f"  Pair: {pair}")
            print(f"  Batch: {batch_id}\n")
            
            # 验证配置缓存
            config_key = f"quick_monitor:ws:config:{pair}"
            cached_config = self.redis.hgetall(config_key)
            if cached_config:
                print(f"✅ 配置缓存存在")
                print(f"   Template ID: {cached_config.get(b'template_id', b'').decode()}")
                print(f"   Time Interval: {cached_config.get(b'time_interval', b'').decode()}")
            else:
                print(f"❌ 配置缓存不存在")
            
            # 验证CA映射
            ca_pair_key = f"quick_monitor:ws:ca_pair:{ca}"
            cached_pair = self.redis.get(ca_pair_key)
            if cached_pair:
                if isinstance(cached_pair, bytes):
                    cached_pair = cached_pair.decode()
                print(f"✅ CA映射存在: {ca} → {cached_pair}")
            else:
                print(f"❌ CA映射不存在")
            
            # 验证批次索引
            batch_key = f"quick_monitor:ws:batch:{batch_id}"
            batch_members = self.redis.smembers(batch_key)
            if batch_members:
                print(f"✅ 批次索引存在: Batch {batch_id} 有 {len(batch_members)} 个成员")
            else:
                print(f"❌ 批次索引不存在")
            
            # 验证版本号
            version = self.redis.get('quick_monitor:ws:version')
            if version:
                if isinstance(version, bytes):
                    version = float(version.decode())
                else:
                    version = float(version)
                version_time = datetime.fromtimestamp(version)
                print(f"✅ 版本号存在: {version_time.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"❌ 版本号不存在")
        
        print()
    
    def get_cache_stats(self):
        """获取缓存统计信息"""
        print("=" * 80)
        print("缓存统计信息")
        print("=" * 80)
        
        # 统计各类key的数量
        config_keys = self.redis.keys('quick_monitor:ws:config:*')
        ca_pair_keys = self.redis.keys('quick_monitor:ws:ca_pair:*')
        batch_keys = self.redis.keys('quick_monitor:ws:batch:*')
        
        print(f"配置缓存数量: {len(config_keys)}")
        print(f"CA映射数量: {len(ca_pair_keys)}")
        print(f"批次索引数量: {len(batch_keys)}")
        
        # 估算内存占用
        sample_size = 0
        if config_keys:
            # 使用DEBUG OBJECT命令获取一个key的序列化长度
            try:
                memory = self.redis.memory_usage(config_keys[0])
                if memory:
                    total_memory = memory * len(config_keys) / 1024 / 1024
                    print(f"预估内存占用: {total_memory:.2f} MB")
            except:
                pass
        
        print()
    
    def run(self):
        """执行完整的配置加载流程"""
        print("\n")
        print("=" * 80)
        print(" " * 20 + "SOL WebSocket 配置加载器")
        print("=" * 80)
        print()
        
        try:
            # 1. 加载模板配置
            if not self.load_templates_from_redis():
                return
            
            # 2. 加载批次池数据
            if not self.load_batch_pool_from_db():
                return
            
            # 3. 写入Redis缓存
            self.write_to_redis_cache()
            
            # 4. 验证缓存
            self.verify_cache()
            
            # 5. 统计信息
            self.get_cache_stats()
            
            print("=" * 80)
            print("✅ 配置加载完成！WebSocket监控已准备就绪")
            print("=" * 80)
            print()
            
        except Exception as e:
            print(f"\n❌ 错误: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            self.redis_wrapper.close()
            # DatabaseManager是单例，不需要手动关闭


if __name__ == "__main__":
    loader = SolWsConfigLoader()
    loader.run()

