"""
GMGN API 客户端
用于获取 Token 的实时价格、交易量、持有人数等数据
"""
import requests
import json
import logging
import time
import random
from typing import List, Dict, Optional, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class GmgnAPI:
    """GMGN API 客户端"""
    
    def __init__(self):
        self.base_url = "https://gmgn.ai/api/v1/mutil_window_token_info"
        
        # 查询参数
        self.params = {
            'device_id': 'f83050a4-8e63-49b0-a823-f98958116c7c',
            'fp_did': 'd4189f054aa9246951683e1c86679ac9',
            'client_id': 'gmgn_web_20251017-5539-a45bd6a',
            'from_app': 'gmgn',
            'app_ver': '20251017-5539-a45bd6a',
            'tz_name': 'Asia/Shanghai',
            'tz_offset': '28800',
            'app_lang': 'zh-CN',
            'os': 'web'
        }
        
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """创建带重试机制的 session"""
        session = requests.Session()
        
        # 禁用 SSL 证书验证（解决本地环境证书问题）
        session.verify = False
        
        # 禁用 SSL 警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        return session
    
    def _generate_dynamic_cookie(self, chain: str) -> str:
        """
        生成动态 Cookie（使用当前时间戳）
        
        Args:
            chain: 链名称
            
        Returns:
            Cookie 字符串
        """
        current_timestamp = int(time.time())
        
        # 生成随机的 cf_clearance（模拟真实的格式）
        cf_clearance_base = "WyR5pgnBwWTZYCs7x0PlUDolJdFSD1_vESoBtJpM86Q"
        cf_clearance = f"{cf_clearance_base}-{current_timestamp}-1.2.1.1-CtiQQRQkGjQKPDo.ToiJ4YRQTQxG42Su9DFFGm_ZICG6o7za2kPFT33t45wCvYjBRBtHzAEpVRBnNj4f3G..FnTFxUZq4c1sJadh4sQnP.sdTqzCsqoNB9hJLGoVgouwr4SrON61tBhOa4ZZXUqrsQoT55VQiYSEVPizK0BFpidCYD2dwOi3A5dIVVyEfRTScd1yGBjT6Ls4Jp3ylpPbuyIPlfGAkeNR_bz7jS1ZJ9I"
        
        # 生成随机的 __cf_bm（格式：随机字符串-时间戳-版本信息）
        random_str = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-', k=43))
        cf_bm = f"{random_str}-{current_timestamp}-1.0.1.1-{random_str[:80]}"
        
        # 生成 _ga_UGLVBMV4Z0 和 _ga_0XM0LYXGC8
        ga_timestamp_ms = current_timestamp * 1000
        ga_uglvbmv4z0 = f"GS1.2.{ga_timestamp_ms}.{random.randint(100000000, 999999999)}.1HeDju0RMx9thEVSmQnBRA%3D%3D.X5%2FnIa8F8is8SxbIC881HQ%3D%3D.7yaEtYt33EhexBLBa3Preg%3D%3D.kw17uCNO31i4Bot0D7YriA%3D%3D"
        
        # sid 保持固定（会话ID通常较稳定）
        sid = "gmgn%7Cb03bdd6b7520e3bd4fca97456bec170d"
        
        if chain.lower() == 'sol':
            ga_0xm0lyxgc8 = f"GS2.1.s{current_timestamp}$o{random.randint(100, 999)}$g1$t{current_timestamp + random.randint(100, 1000)}$j{random.randint(1, 99)}$l0$h0"
        else:
            ga_0xm0lyxgc8 = f"GS2.1.s{current_timestamp}$o{random.randint(100, 999)}$g0$t{current_timestamp}$j{random.randint(1, 99)}$l0$h0"
        
        # 组合 Cookie
        cookie = f"_ga=GA1.1.1917780211.1737870293; GMGN_LOCALE=zh-CN; GMGN_THEME=dark; GMGN_CHAIN=sol; cf_clearance={cf_clearance}; sid={sid}; _ga_UGLVBMV4Z0={ga_uglvbmv4z0}; __cf_bm={cf_bm}; _ga_0XM0LYXGC8={ga_0xm0lyxgc8}"
        
        return cookie
    
    def _get_headers(self, chain: str, first_address: str = "") -> Dict[str, str]:
        """
        根据链类型生成对应的请求头
        
        Args:
            chain: 链名称 ('sol' 或 'bsc')
            first_address: 第一个 token 地址（用于设置 referer）
            
        Returns:
            请求头字典
        """
        # 生成随机的 trace ID 和 baggage
        trace_id = ''.join(random.choices('0123456789abcdef', k=32))
        span_id = ''.join(random.choices('0123456789abcdef', k=16))
        
        # 基础请求头（两个链通用）
        headers = {
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip, deflate, br, zstd",
            'Content-Type': "application/json",
            'sec-ch-ua-full-version-list': '"Google Chrome";v="141.0.7390.67", "Not?A_Brand";v="8.0.0.0", "Chromium";v="141.0.7390.67"',
            'sec-ch-ua-platform': '"Windows"',
            'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            'sec-ch-ua-bitness': '"64"',
            'sec-ch-ua-model': '""',
            'sec-ch-ua-mobile': "?0",
            'baggage': f"sentry-environment=production,sentry-release=20251017-5539-a45bd6a,sentry-public_key=93c25bab7246077dc3eb85b59d6e7d40,sentry-trace_id={trace_id},sentry-sample_rate=0.01,sentry-sampled=false",
            'sentry-trace': f"{trace_id}-{span_id}-0",
            'sec-ch-ua-arch': '"x86"',
            'sec-ch-ua-full-version': '"141.0.7390.67"',
            'sec-ch-ua-platform-version': '"19.0.0"',
            'origin': "https://gmgn.ai",
            'sec-fetch-site': "same-origin",
            'sec-fetch-mode': "cors",
            'sec-fetch-dest': "empty",
            'accept-language': "zh-CN,zh;q=0.9,en;q=0.8",
            'priority': "u=1, i",
        }
        
        # 根据链设置不同的 referer 和动态 Cookie
        if chain.lower() == 'sol':
            if first_address:
                headers['referer'] = f"https://gmgn.ai/sol/token/{first_address}"
            else:
                headers['referer'] = "https://gmgn.ai/sol/token/"
        elif chain.lower() == 'bsc':
            if first_address:
                headers['referer'] = f"https://gmgn.ai/bsc/token/{first_address}"
            else:
                headers['referer'] = "https://gmgn.ai/bsc/token/"
        else:
            headers['referer'] = "https://gmgn.ai/"
        
        # 使用动态生成的 Cookie
        headers['Cookie'] = self._generate_dynamic_cookie(chain)
        
        return headers
    
    def get_token_info_batch(
        self,
        chain: str,
        addresses: List[str]
    ) -> Optional[List[Dict[str, Any]]]:
        """
        批量获取 Token 信息
        
        Args:
            chain: 链名称 ('sol' 或 'bsc')
            addresses: Token 地址列表（建议每批不超过 50 个）
            
        Returns:
            Token 信息列表，如果失败返回 None
        """
        if not addresses:
            return []
        
        # 每批最多 50 个
        if len(addresses) > 50:
            logger.warning(f"地址数量超过 50 个({len(addresses)})，将只处理前 50 个")
            addresses = addresses[:50]
        
        # BSC 地址需要转换为小写（EVM 链地址不区分大小写，但 API 要求小写）
        if chain.lower() == 'bsc':
            addresses = [addr.lower() for addr in addresses]
        
        # 记录请求信息
        logger.debug(f"📤 GMGN API 请求: chain={chain}, 地址数={len(addresses)}")
        
        try:
            payload = {
                "chain": chain,
                "addresses": addresses
            }
            
            # 根据链类型生成对应的请求头
            headers = self._get_headers(chain, addresses[0] if addresses else "")
            
            # 使用 data=json.dumps() 而不是 json=payload，完全模拟浏览器行为
            response = self.session.post(
                self.base_url,
                params=self.params,
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )
            
            response.raise_for_status()
            
            # 检查响应内容
            if not response.content:
                logger.error(f"❌ GMGN API 返回空响应 (chain={chain}, addresses={addresses})")
                return None
            
            # 打印前4字节用于调试
            first_bytes = response.content[:4]
            logger.debug(f"响应前4字节: {first_bytes.hex()} ({first_bytes})")
            
            # 检查是否是 gzip 压缩（检查 magic number）
            # gzip: 1f8b, deflate/zlib: 78xx
            if response.content[:2] == b'\x1f\x8b':
                logger.debug("检测到 gzip 压缩响应，尝试手动解压...")
                import gzip
                try:
                    decompressed = gzip.decompress(response.content)
                    data = json.loads(decompressed.decode('utf-8'))
                    logger.debug("✅ 手动解压 gzip 成功")
                except Exception as decompress_error:
                    logger.error(f"❌ 手动解压 gzip 失败: {decompress_error}")
                    logger.error(f"   原始内容长度: {len(response.content)} bytes")
                    return None
            elif response.content[0:1] == b'\x78':  # deflate/zlib 压缩
                logger.debug("检测到 deflate/zlib 压缩响应，尝试手动解压...")
                import zlib
                try:
                    decompressed = zlib.decompress(response.content)
                    data = json.loads(decompressed.decode('utf-8'))
                    logger.debug("✅ 手动解压 zlib 成功")
                except Exception as decompress_error:
                    logger.error(f"❌ 手动解压 zlib 失败: {decompress_error}")
                    logger.error(f"   尝试 zlib 负窗口...")
                    try:
                        decompressed = zlib.decompress(response.content, -zlib.MAX_WBITS)
                        data = json.loads(decompressed.decode('utf-8'))
                        logger.debug("✅ 手动解压 zlib (负窗口) 成功")
                    except Exception as e2:
                        logger.error(f"❌ 所有解压方式都失败: {e2}")
                        return None
            else:
                # 正常解析 JSON
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    logger.error(f"❌ GMGN API JSON 解析失败 (chain={chain})")
                    logger.error(f"   响应状态码: {response.status_code}")
                    logger.error(f"   Content-Type: {response.headers.get('Content-Type')}")
                    logger.error(f"   Content-Encoding: {response.headers.get('Content-Encoding', 'none')}")
                    logger.error(f"   前4字节hex: {first_bytes.hex()}")
                    logger.error(f"   响应前100字符: {response.text[:100]}")
                    return None
            
            if data.get('code') == 0 and 'data' in data:
                token_count = len(data['data']) if data['data'] else 0
                logger.info(f"✅ GMGN API 查询成功 (chain={chain}, 返回{token_count}个Token)")
                return data['data']
            else:
                logger.error(f"❌ GMGN API 返回错误 (chain={chain}): code={data.get('code')}, message={data.get('message', 'Unknown error')}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error(f"❌ GMGN API 请求超时 (chain={chain}, addresses={len(addresses)})")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ GMGN API 连接错误: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ GMGN API HTTP 错误 (status={e.response.status_code}): {e}")
            logger.error(f"   响应内容: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"❌ GMGN API 请求异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def parse_token_data(self, token_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        解析单个 Token 数据
        
        Args:
            token_data: API 返回的单个 Token 数据
            
        Returns:
            解析后的数据字典
        """
        try:
            price_info = token_data.get('price', {})
            
            return {
                # 基础信息
                'address': token_data.get('address'),
                'symbol': token_data.get('symbol'),
                'name': token_data.get('name'),
                'holder_count': token_data.get('holder_count', 0),
                'liquidity': float(token_data.get('liquidity', 0)),
                
                # 价格信息
                'price': float(price_info.get('price', 0)),
                'price_1m': float(price_info.get('price_1m', 0)),
                'price_5m': float(price_info.get('price_5m', 0)),
                'price_1h': float(price_info.get('price_1h', 0)),
                'price_6h': float(price_info.get('price_6h', 0)),
                'price_24h': float(price_info.get('price_24h', 0)),
                
                # 交易量信息
                'volume_1m': float(price_info.get('volume_1m', 0)),
                'volume_5m': float(price_info.get('volume_5m', 0)),
                'volume_1h': float(price_info.get('volume_1h', 0)),
                'volume_6h': float(price_info.get('volume_6h', 0)),
                'volume_24h': float(price_info.get('volume_24h', 0)),
                
                # 买卖信息
                'buys_1m': price_info.get('buys_1m', 0),
                'buys_5m': price_info.get('buys_5m', 0),
                'sells_1m': price_info.get('sells_1m', 0),
                'sells_5m': price_info.get('sells_5m', 0),
                'swaps_5m': price_info.get('swaps_5m', 0),
                
                # 热度
                'hot_level': price_info.get('hot_level', 0),
            }
            
        except Exception as e:
            logger.error(f"解析 Token 数据失败: {e} | address: {token_data.get('address')}")
            return None


# 全局单例
_gmgn_api = None

def get_gmgn_api() -> GmgnAPI:
    """获取 GMGN API 客户端单例"""
    global _gmgn_api
    if _gmgn_api is None:
        _gmgn_api = GmgnAPI()
    return _gmgn_api

