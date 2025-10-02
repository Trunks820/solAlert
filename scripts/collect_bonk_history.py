"""
BONK历史数据采集脚本
支持自定义采集页数，用于一次性采集大量历史数据
"""
import sys
import asyncio
import argparse
from pathlib import Path

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from solalert.collectors.bonk_collector import BonkCollector
from solalert.core.logger import setup_logger

# 设置日志
logger = setup_logger()


async def collect_history(max_pages: int = 10, sleep_interval: float = 1.0):
    """
    采集历史数据
    
    Args:
        max_pages: 最多采集页数（每页100条）
        sleep_interval: 每页之间的等待时间（秒）
    """
    logger.info("=" * 70)
    logger.info(f"🚀 开始采集BONK历史数据")
    logger.info(f"📊 采集页数: {max_pages} 页 (预计约 {max_pages * 100} 条数据)")
    logger.info(f"⏱️  页面间隔: {sleep_interval} 秒")
    logger.info("=" * 70)
    
    collector = BonkCollector()
    
    total_collected = 0
    total_saved = 0
    next_page_id = None
    
    try:
        for page in range(max_pages):
            try:
                # 构建请求参数
                params = collector.DEFAULT_PARAMS.copy()
                if next_page_id:
                    params['nextPageId'] = next_page_id
                
                logger.info(f"\n📄 正在采集第 {page + 1}/{max_pages} 页...")
                
                # 发起请求
                data = await collector._fetch_api(params)
                if not data:
                    logger.error(f"❌ 第{page + 1}页获取失败")
                    break
                
                rows = data.get('rows', [])
                if not rows:
                    logger.info(f"✅ 第{page + 1}页无数据，已到达最后一页")
                    break
                
                # 处理数据
                saved_count = await collector._process_tokens(rows)
                total_collected += len(rows)
                total_saved += saved_count
                
                logger.info(f"✅ 第{page + 1}页完成: 获取 {len(rows)} 条，入库 {saved_count} 条")
                logger.info(f"📊 累计进度: 已获取 {total_collected} 条，已入库 {total_saved} 条")
                
                # 获取下一页ID
                next_page_id = data.get('nextPageId')
                if not next_page_id:
                    logger.info(f"✅ 已到达最后一页")
                    break
                
                # 页面间等待，避免请求过快
                if page < max_pages - 1:  # 最后一页不需要等待
                    logger.info(f"⏳ 等待 {sleep_interval} 秒...")
                    await asyncio.sleep(sleep_interval)
                
            except Exception as e:
                logger.error(f"❌ 采集第{page + 1}页失败: {e}")
                break
        
        # 输出最终统计
        logger.info("\n" + "=" * 70)
        logger.info("🎉 历史数据采集完成！")
        logger.info(f"📊 总计: 获取 {total_collected} 条，成功入库 {total_saved} 条")
        logger.info(f"💾 重复数据: {total_collected - total_saved} 条")
        logger.info(f"✅ 入库率: {total_saved / total_collected * 100:.1f}%" if total_collected > 0 else "✅ 入库率: 0%")
        logger.info("=" * 70)
        
    except KeyboardInterrupt:
        logger.info("\n⏹️  采集被用户中断")
        logger.info(f"📊 已采集: {total_collected} 条，已入库: {total_saved} 条")
    except Exception as e:
        logger.error(f"❌ 采集过程出错: {e}", exc_info=True)
    finally:
        collector.session.close()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="BONK历史数据采集工具")
    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="采集页数（每页100条，默认10页=1000条）"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="页面间隔时间（秒，默认1秒）"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="采集所有历史数据（直到没有数据为止）"
    )
    
    args = parser.parse_args()
    
    # 如果选择采集所有数据，设置一个很大的页数
    max_pages = 9999 if args.all else args.pages
    
    asyncio.run(collect_history(max_pages, args.interval))


if __name__ == '__main__':
    main()

