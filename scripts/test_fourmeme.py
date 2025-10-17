"""
Four.meme采集器测试脚本
测试BSC链Token数据采集功能
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from solalert.collectors.fourmeme_collector import FourMemeCollector
from solalert.core.logger import setup_logger
from solalert.core.database import get_db

logger = setup_logger()


async def test_api():
    """测试1: API调用"""
    logger.info("=" * 80)
    logger.info("测试1: Four.meme API调用")
    logger.info("=" * 80)
    
    collector = FourMemeCollector()
    
    # 测试外盘API
    logger.info("📊 测试外盘API (listedPancake=true)...")
    params = collector.BASE_PARAMS.copy()
    params['listedPancake'] = 'true'
    params['pageIndex'] = '1'
    
    outer_data = await collector._fetch_api(params)
    if outer_data and outer_data.get('data'):
        tokens = outer_data['data']
        logger.info(f"✅ 外盘API调用成功，获取 {len(tokens)} 条数据")
        if tokens:
            first_token = tokens[0]
            logger.info(f"   示例Token:")
            logger.info(f"   - 名称: {first_token.get('name')}")
            logger.info(f"   - CA: {first_token.get('address')[:20]}...")
            logger.info(f"   - 市值: ${first_token.get('tokenPrice', {}).get('marketCap', 0)}")
            logger.info(f"   - Twitter: {first_token.get('twitterUrl', '无')[:50]}")
    else:
        logger.error("❌ 外盘API调用失败")
    
    logger.info("")
    
    # 测试内盘API
    logger.info("📊 测试内盘API (listedPancake=false)...")
    params['listedPancake'] = 'false'
    
    inner_data = await collector._fetch_api(params)
    if inner_data and inner_data.get('data'):
        tokens = inner_data['data']
        logger.info(f"✅ 内盘API调用成功，获取 {len(tokens)} 条数据")
        if tokens:
            first_token = tokens[0]
            logger.info(f"   示例Token:")
            logger.info(f"   - 名称: {first_token.get('name')}")
            logger.info(f"   - CA: {first_token.get('address')[:20]}...")
            logger.info(f"   - 进度: {float(first_token.get('tokenPrice', {}).get('progress', 0)) * 100:.2f}%")
    else:
        logger.error("❌ 内盘API调用失败")
    
    collector.session.close()


async def test_database():
    """测试2: 数据库连接和查询"""
    logger.info("")
    logger.info("=" * 80)
    logger.info("测试2: 数据库连接")
    logger.info("=" * 80)
    
    db = get_db()
    
    # 测试查询fourmeme数据
    sql = """
    SELECT ca, token_name, token_symbol, twitter_url, launch_time, highest_market_cap
    FROM token_launch_history
    WHERE source = 'fourmeme'
    ORDER BY created_at DESC
    LIMIT 5
    """
    
    results = db.execute_query(sql)
    
    if results:
        logger.info(f"✅ 数据库连接成功")
        logger.info(f"📊 最近5条Four.meme数据:")
        for i, row in enumerate(results, 1):
            ca, name, symbol, twitter, launch_time, market_cap = row
            # 确保market_cap是数字类型
            try:
                market_cap_float = float(market_cap) if market_cap else 0
            except (ValueError, TypeError):
                market_cap_float = 0
            twitter_info = f" | Twitter: ✅" if twitter else ""
            logger.info(f"   {i}. {name} ({symbol}) | CA: {ca[:15]}... | 市值: ${market_cap_float:,.2f}{twitter_info}")
    else:
        logger.info("✅ 数据库连接成功")
        logger.info("📊 暂无Four.meme数据")


async def test_collect_once():
    """测试3: 执行一次完整采集"""
    logger.info("")
    logger.info("=" * 80)
    logger.info("测试3: 执行一次完整采集")
    logger.info("=" * 80)
    logger.info("💡 这将持续采集外盘和内盘的所有新Token（直到遇到重复或无数据）")
    logger.info("⚠️  如果是首次运行可能需要较长时间，请耐心等待...")
    logger.info("")
    
    collector = FourMemeCollector()
    
    try:
        # 执行初始化采集
        await collector._collect_initial_data()
        logger.info("")
        logger.info("✅ 采集完成！")
    except Exception as e:
        logger.error(f"❌ 采集失败: {e}", exc_info=True)
    finally:
        collector.session.close()


async def test_statistics():
    """测试4: 统计信息"""
    logger.info("")
    logger.info("=" * 80)
    logger.info("测试4: Four.meme数据统计")
    logger.info("=" * 80)
    
    db = get_db()
    
    # 统计总数
    sql_count = """
    SELECT COUNT(*) as total,
           SUM(CASE WHEN twitter_url IS NOT NULL AND twitter_url != '' THEN 1 ELSE 0 END) as with_twitter,
           AVG(highest_market_cap) as avg_market_cap,
           MAX(highest_market_cap) as max_market_cap
    FROM token_launch_history
    WHERE source = 'fourmeme'
    """
    
    result = db.execute_query(sql_count, fetch_one=True)
    
    if result:
        total, with_twitter, avg_cap, max_cap = result
        # 确保类型转换
        total = int(total) if total else 0
        with_twitter = int(with_twitter) if with_twitter else 0
        avg_cap = float(avg_cap) if avg_cap else 0
        max_cap = float(max_cap) if max_cap else 0
        
        logger.info(f"📊 数据统计:")
        logger.info(f"   总数据量: {total}")
        logger.info(f"   有Twitter: {with_twitter} ({(with_twitter/total*100 if total else 0):.1f}%)")
        logger.info(f"   平均市值: ${avg_cap:,.2f}")
        logger.info(f"   最高市值: ${max_cap:,.2f}")
    
    # 按日期统计
    sql_daily = """
    SELECT DATE(created_at) as date, COUNT(*) as count
    FROM token_launch_history
    WHERE source = 'fourmeme'
    GROUP BY DATE(created_at)
    ORDER BY date DESC
    LIMIT 7
    """
    
    daily_results = db.execute_query(sql_daily)
    
    if daily_results:
        logger.info("")
        logger.info("📅 最近7天采集情况:")
        for date, count in daily_results:
            logger.info(f"   {date}: {count} 条")


async def main():
    """主测试函数"""
    logger.info("🧪 开始Four.meme采集器测试")
    logger.info("")
    
    # 测试1: API调用
    await test_api()
    
    # 测试2: 数据库
    await test_database()
    
    # 询问是否执行采集
    logger.info("")
    logger.info("=" * 80)
    user_input = input("是否执行一次完整采集? (输入 yes 继续，其他跳过): ")
    
    if user_input.lower() in ['yes', 'y', '是']:
        await test_collect_once()
        # 采集后再次查看统计
        await test_statistics()
    else:
        logger.info("⏭️  跳过采集测试")
        # 显示现有统计
        await test_statistics()
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("🎉 测试完成！")
    logger.info("=" * 80)
    logger.info("")
    logger.info("💡 启动命令:")
    logger.info("   python main.py --module fourmeme_collector")
    logger.info("   python main.py --module fourmeme_collector --interval 120  # 2分钟间隔")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⏹️  测试已取消")
    except Exception as e:
        logger.error(f"❌ 测试失败: {e}", exc_info=True)

