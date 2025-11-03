"""
DBotX API 客户端（异步版本）
用于获取 Token 的实时价格、交易量、持有人数等数据
相比 GMGN API 更稳定（使用 API Key 而非 Cookie）

✅ 使用 httpx.AsyncClient 替代 requests，避免阻塞事件循环
"""
import logging
from typing import Dict, Optional
import httpx

logger = logging.getLogger(__name__)


class DBotXAPI:
    """DBotX API 客户端（异步）"""
    
    def __init__(self, api_key: str = "i1o3elfavv59ds02fggj9rsd0eg8w657"):
        self.base_url = "https://api-data-v1.dbotx.com"
        self.api_key = api_key
        
        # 创建异步 HTTP 客户端
        # 添加 User-Agent 模拟浏览器请求（某些API会检测）
        default_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        self.client = httpx.AsyncClient(
            verify=False,  # 禁用 SSL 验证
            timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),  # 总超时30秒，连接10秒，读取20秒
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers=default_headers
        )
        
        # 禁用 SSL 警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()
    
    async def search_pairs(self, token_address: str) -> Optional[Dict]:
        """
        搜索代币的所有交易对，返回交易量最大的交易对信息（异步）
        
        Args:
            token_address: 代币地址
        
        Returns:
            交易量最大的交易对信息字典（包含 _id, poolType, currencyReserve, isLaunchMigration 等），失败返回 None
        """
        try:
            url = f"{self.base_url}/kline/search"
            
            params = {
                'keyword': token_address.lower()
            }
            
            headers = {
                'x-api-key': self.api_key
            }
            
            # 异步请求，最多重试 3 次
            for attempt in range(3):
                try:
                    response = await self.client.get(url, params=params, headers=headers)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if data.get('err') is False and data.get('res'):
                            pairs = data['res']
                            if len(pairs) > 0:
                                # 第一个就是交易量最大的
                                largest_pair = pairs[0]
                                pair_id = largest_pair.get('_id') or largest_pair.get('id')
                                volume_24h = largest_pair.get('buyAndSellVolume24h', 0)
                                pool_type = largest_pair.get('poolType', '')
                                currency_reserve = largest_pair.get('currencyReserve', 0)
                                
                                # 提取链名称（API返回的是真实链名，如 'solana', 'bsc'）
                                chain_name = largest_pair.get('chain', '')
                                
                                logger.info(
                                    f"✅ 找到最大交易对: {pair_id[:10]}... "
                                    f"(链: {chain_name}, 24h: ${volume_24h:,.0f}, 池: {pool_type}, 储备: {currency_reserve:.2f})"
                                )
                                
                                # 返回完整信息（用于判断内外盘）
                                return {
                                    'pair_address': pair_id,
                                    'chain': chain_name,  # 添加链名称字段
                                    'pool_type': pool_type,
                                    'currency_reserve': currency_reserve or 0,
                                    'is_launch_migration': largest_pair.get('isLaunchMigration', False),
                                    'volume_24h': volume_24h
                                }
                            else:
                                logger.warning(f"⚠️  Token {token_address[:10]}... 搜索结果为空，未找到交易对")
                                return None
                        else:
                            logger.warning(f"⚠️  Search API 返回错误: {data}")
                            return None
                    
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        # 可重试的错误
                        if attempt < 2:
                            await asyncio.sleep(1 * (attempt + 1))  # 递增延迟
                            continue
                        logger.warning(f"⚠️  Search API HTTP错误 {response.status_code}（已重试{attempt + 1}次）")
                        return None
                    else:
                        logger.warning(f"⚠️  Search API HTTP错误 {response.status_code}")
                        return None
                
                except httpx.TimeoutException:
                    if attempt < 2:
                        retry_delay = 2 ** attempt  # 指数退避：1s, 2s, 4s
                        logger.debug(f"⏱️ Search API 超时，{retry_delay}秒后重试 {attempt + 1}/3")
                        await asyncio.sleep(retry_delay)
                        continue
                    logger.warning(f"⚠️  Search API 超时（已重试3次）: {token_address[:10]}...")
                    return None
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(1)
                        continue
                    logger.warning(f"⚠️  Search API 请求失败: {e}")
                    return None
            
            return None
        
        except Exception as e:
            logger.warning(f"⚠️  Search API 外部异常: {e}")
            return None
    
    async def get_pair_info(self, chain: str, pair_address: str) -> Optional[Dict]:
        """
        获取交易对信息（异步）
        
        Args:
            chain: 链名称（如 'bsc', 'sol'）
            pair_address: 交易对地址
        
        Returns:
            交易对信息字典，失败返回 None
        """
        try:
            url = f"{self.base_url}/kline/pair_info"
            
            params = {
                'chain': chain.lower(),
                'pair': pair_address  # 保持原始大小写（Solana地址区分大小写）
            }
            
            headers = {
                'x-api-key': self.api_key
            }
            
            # 异步请求，最多重试 3 次
            for attempt in range(3):
                try:
                    response = await self.client.get(url, params=params, headers=headers)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if data.get('err') is False:
                            res = data.get('res')
                            
                            # 区分：None（失败）和 {}（空数据）
                            if res is None:
                                logger.warning(f"⚠️  DBotX API 返回错误: pair={pair_address[:10]}... data={data}")
                                return None
                            elif not res:  # 空字典 {}
                                logger.debug(f"⚠️  DBotX API 返回空数据: pair={pair_address[:10]}... 将触发降级")
                                return res  # 返回空字典，让调用方判断并降级
                            else:
                                logger.info(f"✅ DBotX API 成功获取 pair_info: {pair_address[:10]}...")
                                return res
                        else:
                            logger.warning(f"⚠️  DBotX API 返回错误: pair={pair_address[:10]}... data={data}")
                            return None
                    
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        if attempt < 2:
                            await asyncio.sleep(1 * (attempt + 1))
                            continue
                        logger.warning(f"⚠️  DBotX API HTTP错误 {response.status_code}（已重试{attempt + 1}次）")
                        return None
                    else:
                        logger.warning(f"⚠️  DBotX API HTTP错误 {response.status_code}")
                        return None
                
                except httpx.TimeoutException:
                    if attempt < 2:
                        retry_delay = 2 ** attempt  # 指数退避：1s, 2s, 4s
                        logger.debug(f"⏱️ DBotX API 超时，{retry_delay}秒后重试 {attempt + 1}/3")
                        await asyncio.sleep(retry_delay)
                        continue
                    logger.warning(f"⚠️  DBotX API 超时（已重试3次）: pair={pair_address[:10]}...")
                    return None
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(1)
                        continue
                    logger.warning(f"⚠️  DBotX API 请求失败: {e}")
                    return None
            
            return None
        
        except Exception as e:
            logger.warning(f"⚠️  DBotX API 外部异常: {e}")
            return None
    
    async def get_token_launchpad_info(self, chain: str, token_address: str) -> Optional[Dict]:
        """
        获取代币 Launchpad 信息（异步）
        
        Args:
            chain: 链名称
            token_address: 代币地址
        
        Returns:
            Launchpad 信息：{'launchpad': 'fourmeme', 'pair_address': '0x...', 'launchpad_status': 0/1, ...}
        """
        try:
            # 1. 先搜索获取交易量最大的交易对信息
            pair_info = await self.search_pairs(token_address)
            
            if not pair_info:
                return None
            
            # 提取关键信息
            pair_address = pair_info.get('pair_address')
            pool_type = pair_info.get('pool_type', '').lower()
            currency_reserve = pair_info.get('currency_reserve', 0)
            is_launch_migration = pair_info.get('is_launch_migration', False)
            
            # 2. 用交易对地址查询详细信息（获取更多数据）
            detailed_info = await self.get_pair_info(chain, pair_address)
            
            if not detailed_info:
                return None
            
            # 提取 preDex 信息
            pre_dex = detailed_info.get('preDex', '').lower()
            
            # 判断是否是 fourmeme
            if pool_type == 'fourmeme' or pre_dex == 'fourmeme':
                # 判断内外盘：
                # 1. pool_type == 'fourmeme' && currencyReserve < 18 → 内盘 (status=0)
                # 2. 其他情况 → 外盘 (status=1)
                launchpad_status = 0 if (pool_type == 'fourmeme' and currency_reserve < 18) else 1
                
                logger.debug(
                    f"Fourmeme 判断: pool={pool_type}, reserve={currency_reserve:.2f} → "
                    f"{'内盘' if launchpad_status == 0 else '外盘'}"
                )
                
                return {
                    'launchpad': 'fourmeme',
                    'poolType': pool_type,
                    'preDex': pre_dex,
                    'pair_address': pair_address,
                    'launchpad_status': launchpad_status,  # 0=内盘, 1=外盘
                    'currency_reserve': currency_reserve,
                    'is_launch_migration': is_launch_migration
                }
            
            # 其他平台
            if pool_type or pre_dex:
                return {
                    'launchpad': pool_type or pre_dex,
                    'poolType': pool_type,
                    'preDex': pre_dex,
                    'pair_address': pair_address,
                    'launchpad_status': 1  # 非 fourmeme 默认为外盘
                }
            
            return None
        
        except Exception as e:
            logger.debug(f"解析 Launchpad 信息失败: {e}")
            return None
    
    def parse_token_data(self, raw_data: Dict, time_interval: str = '1m') -> Optional[Dict]:
        """
        解析 DBotX API 返回的代币数据（同步，仅解析不涉及 I/O）
        
        Args:
            raw_data: API 返回的原始数据
            time_interval: 时间间隔 ('1m', '5m', '1h'), 默认 '1m'
        
        Returns:
            标准化的代币数据字典
        """
        try:
            # 解析各时间段数据
            price_change_1m = raw_data.get('priceChange1m', 0) * 100  # 转换为百分比
            price_change_5m = raw_data.get('priceChange5m', 0) * 100
            price_change_1h = raw_data.get('priceChange1h', 0) * 100
            
            volume_1m = raw_data.get('buyAndSellVolume1m', 0)
            volume_5m = raw_data.get('buyAndSellVolume5m', 0)
            volume_1h = raw_data.get('buyAndSellVolume1h', 0)
            
            # 根据 time_interval 选择使用的数据
            interval_map = {
                '1m': (price_change_1m, volume_1m),
                '5m': (price_change_5m, volume_5m),
                '1h': (price_change_1h, volume_1h),
            }
            price_change, volume = interval_map.get(time_interval.lower(), (price_change_1m, volume_1m))
            
            return {
                'address': raw_data.get('token', ''),
                'symbol': raw_data.get('symbol', 'Unknown'),
                'name': raw_data.get('name', 'Unknown'),
                'price': raw_data.get('tokenPriceUsd', 0),
                
                # 使用选定时间间隔的数据
                'price_change': price_change,
                'volume': volume,
                
                # 保留各时间段数据
                'price_1m': price_change_1m,
                'price_5m': price_change_5m,
                'price_1h': raw_data.get('priceChange1h', 0) * 100,
                'price_24h': raw_data.get('priceChange24h', 0) * 100,
                
                'volume_1m': volume_1m,
                'volume_5m': volume_5m,
                'volume_1h': raw_data.get('buyAndSellVolume1h', 0),
                'volume_24h': raw_data.get('buyAndSellVolume24h', 0),
                
                # 交易次数
                'buy_count_1m': raw_data.get('buyTimes1m', 0),
                'sell_count_1m': raw_data.get('sellTimes1m', 0),
                'buy_count_24h': raw_data.get('buyTimes24h', 0),
                'sell_count_24h': raw_data.get('sellTimes24h', 0),
                
                # 市值和流动性
                'market_cap': raw_data.get('marketCap', 0),
                'liquidity': raw_data.get('marketCap', 0),
                
                # 持有者信息
                'holder_count': raw_data.get('holders', 0),
                'top10_holder_rate': raw_data.get('safetyInfo', {}).get('top10HolderRate', 0),
                
                # 安全信息
                'is_honeypot': raw_data.get('safetyInfo', {}).get('isHoneypot', False),
                'is_open_source': raw_data.get('safetyInfo', {}).get('isOpenSource', False),
                'can_mint': raw_data.get('safetyInfo', {}).get('canMint', False),
                'is_proxy': raw_data.get('safetyInfo', {}).get('isProxy', False),
                
                # 图标
                'logo': raw_data.get('image', ''),
            }
        
        except Exception as e:
            logger.error(f"解析代币数据失败: {e}")
            return None
    
    async def __aenter__(self):
        """支持 async with 语法"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """支持 async with 语法"""
        await self.close()


# 需要导入 asyncio（用于重试延迟）
import asyncio
