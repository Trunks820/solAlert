"""
查询批次-模板映射关系
显示每个批次包含哪些模板的CA
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from solalert.core.database import DatabaseManager


def check_batch_template_mapping():
    """检查批次和模板的映射关系"""
    print("=" * 100)
    print(" " * 30 + "批次-模板映射关系")
    print("=" * 100)
    print()
    
    db = DatabaseManager()
    
    # 查询批次-模板统计
    query = """
        SELECT 
            batch_id,
            template_id,
            template_name,
            time_interval,
            COUNT(*) as ca_count,
            MIN(market_cap) as min_market_cap,
            MAX(market_cap) as max_market_cap,
            AVG(market_cap) as avg_market_cap
        FROM sol_ws_batch_pool
        WHERE is_active = 1
        GROUP BY batch_id, template_id, template_name, time_interval
        ORDER BY batch_id, template_id
    """
    
    results = db.execute_query(query)
    
    if not results:
        print("❌ 未找到数据")
        return
    
    print(f"✅ 找到 {len(results)} 个批次-模板组合\n")
    
    # 按批次分组显示
    current_batch = None
    batch_total = 0
    
    for row in results:
        batch_id = row['batch_id']
        
        # 新批次，打印分隔线
        if current_batch != batch_id:
            if current_batch is not None:
                print(f"    小计: {batch_total} 个CA")
                print()
            
            current_batch = batch_id
            batch_total = 0
            
            print("=" * 100)
            print(f"📦 Batch {batch_id}")
            print("=" * 100)
        
        # 打印模板信息
        ca_count = row['ca_count']
        batch_total += ca_count
        
        print(f"  🔹 {row['template_name']} (ID: {row['template_id']})")
        print(f"     CA数量: {ca_count}")
        print(f"     时间间隔: {row['time_interval']}")
        print(f"     市值范围: ${row['min_market_cap']:,.0f} - ${row['max_market_cap']:,.0f}")
        print(f"     平均市值: ${row['avg_market_cap']:,.0f}")
        print()
    
    # 最后一个批次的小计
    if batch_total > 0:
        print(f"    小计: {batch_total} 个CA")
        print()
    
    print("=" * 100)
    
    # 总体统计
    print("\n" + "=" * 100)
    print("总体统计")
    print("=" * 100)
    
    total_query = """
        SELECT 
            COUNT(DISTINCT batch_id) as batch_count,
            COUNT(DISTINCT template_id) as template_count,
            COUNT(*) as total_ca,
            SUM(CASE WHEN time_interval='1m' THEN 1 ELSE 0 END) as ca_1m,
            SUM(CASE WHEN time_interval='5m' THEN 1 ELSE 0 END) as ca_5m,
            SUM(CASE WHEN time_interval='1h' THEN 1 ELSE 0 END) as ca_1h
        FROM sol_ws_batch_pool
        WHERE is_active = 1
    """
    
    total_result = db.execute_query(total_query)
    if total_result:
        stats = total_result[0]
        print(f"批次总数: {stats['batch_count']}")
        print(f"模板总数: {stats['template_count']}")
        print(f"CA总数: {stats['total_ca']}")
        print(f"\n时间间隔分布:")
        print(f"  - 1m: {stats['ca_1m']} 个CA")
        print(f"  - 5m: {stats['ca_5m']} 个CA")
        print(f"  - 1h: {stats['ca_1h']} 个CA")
    
    # 检查time_interval冲突
    print("\n" + "=" * 100)
    print("⚠️ 批次内time_interval冲突检查")
    print("=" * 100)
    
    conflict_query = """
        SELECT 
            batch_id,
            COUNT(DISTINCT time_interval) as interval_count,
            GROUP_CONCAT(DISTINCT time_interval) as intervals
        FROM sol_ws_batch_pool
        WHERE is_active = 1
        GROUP BY batch_id
        HAVING COUNT(DISTINCT time_interval) > 1
        ORDER BY batch_id
    """
    
    conflicts = db.execute_query(conflict_query)
    
    if conflicts:
        print(f"\n❌ 发现 {len(conflicts)} 个批次存在time_interval冲突：")
        for conflict in conflicts:
            print(f"  Batch {conflict['batch_id']}: {conflict['intervals']} ({conflict['interval_count']}种间隔)")
        
        print("\n💡 解决方案：")
        print("  1. 统一订阅1m间隔（WebSocket返回所有时间窗口的数据）")
        print("  2. 处理消息时，根据每个CA的time_interval配置，选择对应字段")
        print("     - time_interval='1m' → 使用 pc1m, bv1m, sv1m")
        print("     - time_interval='5m' → 使用 pc5m, bv5m, sv5m")
        print("     - time_interval='1h' → 使用 pc1h, bv1h, sv1h")
    else:
        print("\n✅ 所有批次的time_interval都一致，无冲突")
    
    print("\n" + "=" * 100)
    print()


if __name__ == "__main__":
    check_batch_template_mapping()

