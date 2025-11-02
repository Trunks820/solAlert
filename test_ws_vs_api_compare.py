"""
WebSocket vs API 数据对比测试
对比 WebSocket 推送的数据和主动调用 API 获取的数据，验证数据一致性
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import asyncio
import websockets
import json
import logging
from datetime import datetime
from solalert.core.database import DatabaseManager
from solalert.api.dbotx_api import DBotXAPI

# WebSocket配置
WS_URL = "wss://api-data-v1.dbotx.com/data/ws/"
API_KEY = "i1o3elfavv59ds02fggj9rsd0eg8w657"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def format_large_number(num):
    """格式化大数字为K/M/B"""
    if num is None:
        return "$0"
    if num >= 1_000_000_000:
        return f"${num/1_000_000_000:.2f}B"
    elif num >= 1_000_000:
        return f"${num/1_000_000:.2f}M"
    elif num >= 1_000:
        return f"${num/1_000:.2f}K"
    else:
        return f"${num:.2f}"


async def load_test_pairs_from_hot_tokens():
    """使用热门Token的CA，通过API搜索获取pair地址"""
    hot_cas = [
        'Sg4k4iFaEeqhv5866cQmsFTMhRx8sVCPAq2j8Xcpump',
        'GEuuznWpn6iuQAJxLKQDVGXPtrqXHNWTk3gZqqvJpump',
        '9XgfFWPxPU6hyDyGtfhC9D6eyRE3RUSgAYKHRznWpump',
        '6ikxp2KnQcdCik8Aadi2ScE7qgH4j5C7BvSyA29Wpump',
        '8fdBKZq7wo9fJbsZEZhq6omCgvKzLt97HY9XaGgqpump',
        'BBKPiLM9KjdJW7oQSKt99RVWcZdhF6sEHRKnwqeBGHST',
        'E7NgL19JbN8BhUDgWjkH8MtnbhJoaGaWJqosxZZepump',
        'Dfh5DzRgSvvCFDoYc2ciTkMrbDfRKybA4SoFbPmApump'
    ]
    
    logger.info(f"🔥 使用 {len(hot_cas)} 个热门Token进行测试")
    logger.info("")
    
    pairs = []
    api = DBotXAPI(API_KEY)
    
    try:
        for i, ca in enumerate(hot_cas, 1):
            logger.info(f"📡 [{i}/{len(hot_cas)}] 搜索 CA: {ca[:10]}...{ca[-6:]}")
            
            # 搜索pair地址
            pair_info = await api.search_pairs(ca)
            
            if pair_info and pair_info.get('pair_address'):
                pair_address = pair_info['pair_address']
                pairs.append({
                    'pair': pair_address,
                    'token': ca,
                    'ca': ca,
                    'symbol': f'Token{i}',  # 暂时用编号，后面会从API获取symbol
                    'batch_id': 'hot'
                })
                logger.info(f"   ✅ 找到 Pair: {pair_address[:10]}...{pair_address[-6:]}")
            else:
                logger.warning(f"   ⚠️ 未找到交易对")
            
            await asyncio.sleep(0.3)  # 避免请求过快
    
    finally:
        await api.close()
    
    logger.info("")
    logger.info(f"✅ 成功加载 {len(pairs)}/{len(hot_cas)} 个热门Token")
    
    return pairs


async def fetch_api_data(pair_address: str, ca: str):
    """通过API获取pair数据"""
    api = DBotXAPI(API_KEY)
    try:
        data = await api.get_pair_info('solana', pair_address)
        return data
    finally:
        await api.close()


async def compare_data(pair: str, ws_data: dict, pair_info: dict):
    """
    实时对比WS数据和API数据
    
    Args:
        pair: Pair地址
        ws_data: WebSocket推送的数据
        pair_info: Pair信息（包含ca, symbol等）
    """
    ca = pair_info['ca']
    symbol = pair_info['symbol']
    
    # 立即通过API获取数据
    logger.info(f"\n📡 收到推送，正在获取API数据对比: CA={ca[:10]}...{ca[-6:]}")
    api_data = await fetch_api_data(pair, ca)
    
    if not api_data:
        logger.warning("⚠️ API获取失败，跳过对比")
        return
    
    # 🔍 打印API返回的所有字段（调试用）
    logger.info("\n🔍 API返回的字段:")
    logger.info(f"   可用字段: {list(api_data.keys())[:20]}")  # 只显示前20个字段
    
    # 从API数据获取真实的Token名称
    token_symbol = api_data.get('tokenSymbol', symbol)
    token_name = api_data.get('tokenName', '')
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🔍 实时对比: {token_symbol} ({token_name})")
    logger.info(f"   CA: {ca}")
    logger.info(f"{'='*80}")
    
    # 🔍 打印WS返回的所有字段（调试用）
    logger.info("\n🔍 WS返回的字段:")
    logger.info(f"   可用字段: {list(ws_data.keys())}")
    
    # 提取关键指标对比
    logger.info("\n📊 价格对比:")
    api_price = api_data.get('tokenPrice', 0) or 0
    ws_price = ws_data.get('tp', 0) or 0
    price_diff_pct = abs(api_price - ws_price) / api_price * 100 if api_price > 0 else 0
    logger.info(f"   API价格:  ${api_price:.10f}")
    logger.info(f"   WS价格:   ${ws_price:.10f}")
    logger.info(f"   差异:     {price_diff_pct:.2f}%")
    
    logger.info("\n💎 市值对比:")
    api_mc = api_data.get('marketCap', 0) or 0
    ws_mc = ws_data.get('mp', 0) or 0
    mc_diff_pct = abs(api_mc - ws_mc) / api_mc * 100 if api_mc > 0 else 0
    logger.info(f"   API市值:  {format_large_number(api_mc)}")
    logger.info(f"   WS市值:   {format_large_number(ws_mc)}")
    logger.info(f"   差异:     {mc_diff_pct:.2f}%")
    
    logger.info("\n📈 价格变化对比:")
    logger.info(f"   1m:  API={api_data.get('priceChange1m', 0):+.6f}, WS={ws_data.get('pc1m', 0):+.6f}")
    logger.info(f"   5m:  API={api_data.get('priceChange5m', 0):+.6f}, WS={ws_data.get('pc5m', 0):+.6f}")
    logger.info(f"   1h:  API={api_data.get('priceChange1h', 0):+.6f}, WS={ws_data.get('pc1h', 0):+.6f}")
    logger.info(f"   6h:  API={api_data.get('priceChange6h', 0):+.6f}, WS={ws_data.get('pc6h', 0):+.6f}")
    logger.info(f"   24h: API={api_data.get('priceChange24h', 0):+.6f}, WS={ws_data.get('pc24h', 0):+.6f}")
    
    logger.info("\n💹 交易量对比 (1m):")
    api_bv1m = api_data.get('buyVolume1m', 0) or 0
    api_sv1m = api_data.get('sellVolume1m', 0) or 0
    api_total_1m = api_bv1m + api_sv1m
    
    ws_bv1m = ws_data.get('bv1m', 0) or 0
    ws_sv1m = ws_data.get('sv1m', 0) or 0
    ws_bsv = ws_data.get('bsv', 0) or 0
    ws_total_1m = ws_bsv if ws_bsv > 0 else (ws_bv1m + ws_sv1m)
    
    logger.info(f"   API买入:  {format_large_number(api_bv1m)}")
    logger.info(f"   WS买入:   {format_large_number(ws_bv1m)}")
    logger.info(f"   API卖出:  {format_large_number(api_sv1m)}")
    logger.info(f"   WS卖出:   {format_large_number(ws_sv1m)}")
    logger.info(f"   API总量:  {format_large_number(api_total_1m)}")
    logger.info(f"   WS总量:   {format_large_number(ws_total_1m)} (bsv={format_large_number(ws_bsv)})")
    
    logger.info("\n💹 交易量对比 (1h):")
    api_bv1h = api_data.get('buyVolume1h', 0) or 0
    api_sv1h = api_data.get('sellVolume1h', 0) or 0
    api_total_1h = api_bv1h + api_sv1h
    
    ws_bv1h = ws_data.get('bv1h', 0) or 0
    ws_sv1h = ws_data.get('sv1h', 0) or 0
    ws_total_1h = ws_bv1h + ws_sv1h
    
    logger.info(f"   API买入:  {format_large_number(api_bv1h)}")
    logger.info(f"   WS买入:   {format_large_number(ws_bv1h)}")
    logger.info(f"   API卖出:  {format_large_number(api_sv1h)}")
    logger.info(f"   WS卖出:   {format_large_number(ws_sv1h)}")
    logger.info(f"   API总量:  {format_large_number(api_total_1h)}")
    logger.info(f"   WS总量:   {format_large_number(ws_total_1h)}")
    
    logger.info("\n📊 持有者对比:")
    api_holders = api_data.get('holders', 0) or 0
    ws_holders = ws_data.get('h', 0) or 0
    logger.info(f"   API持有者:  {api_holders}")
    logger.info(f"   WS持有者:   {ws_holders}")
    
    logger.info("\n📊 TOP10持仓对比:")
    api_top10 = api_data.get('safetyInfo', {}).get('top10HolderRate', 0) or 0
    ws_top10 = ws_data.get('t10', 0) or 0
    logger.info(f"   API TOP10:  {api_top10*100:.2f}%")
    logger.info(f"   WS TOP10:   {ws_top10*100:.2f}%")
    
    logger.info("\n📊 流动性对比:")
    api_liq = api_data.get('currencyReserve', 0) or 0
    ws_liq = ws_data.get('tr', 0) or 0
    liq_diff_pct = abs(api_liq - ws_liq) / api_liq * 100 if api_liq > 0 else 0
    logger.info(f"   API流动性:  {format_large_number(api_liq)}")
    logger.info(f"   WS流动性:   {format_large_number(ws_liq)}")
    logger.info(f"   差异:       {liq_diff_pct:.2f}%")
    
    # 判断数据是否一致
    logger.info("\n💡 数据对比结论:")
    logger.info(f"   价格差异:     {price_diff_pct:.2f}%")
    logger.info(f"   市值差异:     {mc_diff_pct:.2f}%")
    logger.info(f"   流动性差异:   {liq_diff_pct:.2f}%")
    
    # 交易量1m对比
    volume_1m_diff_pct = abs(api_total_1m - ws_total_1m) / api_total_1m * 100 if api_total_1m > 0 else 0
    logger.info(f"   交易量1m差异: {volume_1m_diff_pct:.2f}%")
    
    # 交易量1h对比
    volume_1h_diff_pct = abs(api_total_1h - ws_total_1h) / api_total_1h * 100 if api_total_1h > 0 else 0
    logger.info(f"   交易量1h差异: {volume_1h_diff_pct:.2f}%")
    
    if price_diff_pct < 1 and mc_diff_pct < 5:
        logger.info("\n✅ 数据基本一致")
    elif price_diff_pct < 5 and mc_diff_pct < 10:
        logger.info("\n⚠️ 数据有小幅差异（可能是时间延迟）")
    else:
        logger.info("\n❌ 数据差异较大，需要检查")
    
    logger.info(f"{'='*80}\n")


async def test_ws_vs_api():
    """对比测试：WS持续运行，收到数据后立即调用API对比"""
    logger.info("=" * 80)
    logger.info("WebSocket 实时数据对比测试（热门Token）")
    logger.info("=" * 80)
    logger.info("")
    
    # 加载测试pairs（使用热门Token）
    test_pairs = await load_test_pairs_from_hot_tokens()
    if not test_pairs:
        logger.error("❌ 未能加载任何测试pair")
        return
    
    # 创建pair映射
    pair_map = {p['pair']: p for p in test_pairs}
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("开始WebSocket监听（收到数据后立即对比）")
    logger.info("=" * 80)
    logger.info("")
    
    # 订阅WebSocket
    try:
        async with websockets.connect(
            WS_URL,
            additional_headers={'x-api-key': API_KEY},
            ping_interval=30,
            ping_timeout=60
        ) as ws:
            logger.info("✅ WebSocket连接成功")
            logger.info("")
            
            # 构造订阅消息
            pairs_to_subscribe = [{'pair': p['pair'], 'token': p['token']} for p in test_pairs]
            
            subscribe_msg = {
                "method": "subscribe",
                "type": "pairsInfo",
                "args": {
                    "pairsInfoInterval": "1m",
                    "pairs": pairs_to_subscribe
                }
            }
            
            await ws.send(json.dumps(subscribe_msg))
            logger.info(f"✅ 已发送订阅请求 ({len(pairs_to_subscribe)} 个pair)")
            logger.info("")
            
            # 监听WebSocket数据
            start_time = datetime.now()
            timeout_seconds = 600  # 运行10分钟
            comparison_count = 0
            
            logger.info("开始监听WebSocket数据（收到后立即调用API对比）...")
            logger.info("")
            
            while True:
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > timeout_seconds:
                    logger.info(f"\n⏱️ 测试时间到（{timeout_seconds}秒）")
                    break
                
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(message)
                    
                    # 跳过确认消息
                    if data.get('method') == 'subscribeResponse':
                        logger.info(f"📨 订阅确认: {data.get('result', {}).get('message')}")
                        continue
                    
                    if data.get('status') == 'ack':
                        continue
                    
                    # 处理pairsInfo数据
                    if data.get('type') == 'pairsInfo':
                        results = data.get('result', [])
                        
                        logger.info(f"\n💡 收到 {len(results)} 个pair的数据推送")
                        
                        for item in results:
                            pair = item.get('p')
                            if pair in pair_map:
                                comparison_count += 1
                                
                                # 🔥 收到WS数据后，立即调用API对比
                                await compare_data(pair, item, pair_map[pair])
                
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"⚠️ WebSocket连接断开: {e}")
                    break
                except Exception as e:
                    logger.error(f"❌ 接收数据失败: {e}")
                    break
            
            logger.info("\n" + "=" * 80)
            logger.info("测试完成")
            logger.info("=" * 80)
            logger.info(f"运行时长: {int(elapsed)}秒 ({elapsed/60:.1f}分钟)")
            logger.info(f"对比次数: {comparison_count}")
            logger.info("=" * 80)
    
    except Exception as e:
        logger.error(f"❌ 测试失败: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_ws_vs_api())

