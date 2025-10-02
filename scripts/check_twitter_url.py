"""
检查BONK数据中的Twitter URL长度
找出导致错误的具体数据
"""
import sys
from pathlib import Path

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from solalert.collectors.bonk_collector import BonkCollector
from solalert.core.logger import setup_logger
import asyncio

# 设置日志
logger = setup_logger()


async def check_twitter_urls():
    """检查API返回的Twitter URL"""
    collector = BonkCollector()
    
    try:
        # 获取最新一页数据
        data = await collector._fetch_api(collector.DEFAULT_PARAMS)
        
        if not data or not data.get('rows'):
            logger.error("无法获取数据")
            return
        
        rows = data.get('rows', [])
        logger.info(f"获取到 {len(rows)} 条数据")
        logger.info("="*70)
        
        # 检查每条数据的Twitter URL
        long_urls = []
        
        for i, token in enumerate(rows, 1):
            twitter = token.get('twitter', '')
            if twitter:
                url_length = len(twitter)
                logger.info(f"{i}. {token.get('name')} ({token.get('symbol')})")
                logger.info(f"   URL长度: {url_length}")
                logger.info(f"   URL: {twitter}")
                
                if url_length > 255:
                    long_urls.append({
                        'name': token.get('name'),
                        'symbol': token.get('symbol'),
                        'length': url_length,
                        'url': twitter
                    })
                    logger.warning(f"   ⚠️ 超过255字符！")
                
                logger.info("-"*70)
        
        # 总结
        logger.info("="*70)
        logger.info(f"检查完成！")
        logger.info(f"总数据: {len(rows)}")
        logger.info(f"有Twitter的: {sum(1 for r in rows if r.get('twitter'))}")
        logger.info(f"超长URL: {len(long_urls)}")
        
        if long_urls:
            logger.warning(f"\n发现 {len(long_urls)} 个超长URL:")
            for item in long_urls:
                logger.warning(f"  - {item['name']}: {item['length']} 字符")
                logger.warning(f"    {item['url'][:100]}...")
        
    finally:
        collector.session.close()


if __name__ == '__main__':
    asyncio.run(check_twitter_urls())

