"""
GMGN API 客户端
用于获取 Token 的实时价格、交易量、持有人数等数据
"""
import requests
import json
import logging
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
    
    def _get_headers(self, chain: str, first_address: str = "") -> Dict[str, str]:
        """
        根据链类型生成对应的请求头
        
        Args:
            chain: 链名称 ('sol' 或 'bsc')
            first_address: 第一个 token 地址（用于设置 referer）
            
        Returns:
            请求头字典
        """
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
            'baggage': "sentry-environment=production,sentry-release=20251017-5539-a45bd6a,sentry-public_key=93c25bab7246077dc3eb85b59d6e7d40,sentry-trace_id=d95288eaa1cf4f4dafce7621ba8b4264,sentry-sample_rate=0.01,sentry-sampled=false",
            'sentry-trace': "d95288eaa1cf4f4dafce7621ba8b4264-98f77d807e0baa09-0",
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
        
        # 根据链设置不同的 referer 和 Cookie
        if chain.lower() == 'sol':
            # SOL 链的配置
            if first_address:
                headers['referer'] = f"https://gmgn.ai/sol/token/{first_address}"
            else:
                headers['referer'] = "https://gmgn.ai/sol/token/"
            
            # SOL 链的 Cookie（注意 GMGN_CHAIN=sol）
            headers['Cookie'] = "_ga=GA1.1.1917780211.1737870293; GMGN_LOCALE=zh-CN; GMGN_THEME=dark; GMGN_CHAIN=sol; cf_clearance=WyR5pgnBwWTZYCs7x0PlUDolJdFSD1_vESoBtJpM86Q-1759673110-1.2.1.1-CtiQQRQkGjQKPDo.ToiJ4YRQTQxG42Su9DFFGm_ZICG6o7za2kPFT33t45wCvYjBRBtHzAEpVRBnNj4f3G..FnTFxUZq4c1sJadh4sQnP.sdTqzCsqoNB9hJLGoVgouwr4SrON61tBhOa4ZZXUqrsQoT55VQiYSEVPizK0BFpidCYD2dwOi3A5dIVVyEfRTScd1yGBjT6Ls4Jp3ylpPbuyIPlfGAkeNR_bz7jS1ZJ9I; sid=gmgn%7Cb03bdd6b7520e3bd4fca97456bec170d; _ga_UGLVBMV4Z0=GS1.2.1760772226676993.245e6720757dcf65dbf07509eaa78e45.1HeDju0RMx9thEVSmQnBRA%3D%3D.X5%2FnIa8F8is8SxbIC881HQ%3D%3D.7yaEtYt33EhexBLBa3Preg%3D%3D.kw17uCNO31i4Bot0D7YriA%3D%3D; __cf_bm=FZzfBjoRjvWn_QLy9ALsJlPxGQ74mdaOT3Hr8Lk8EDQ-1760772855-1.0.1.1-ESoMe2bZV9y_ljBotX4X11s7IeQkUtS1eIt8IvWDxnWFmvLeNYbD_y.FXxbpM3InbHMRA8UWPT.fdHsIlo_vdaYGbbNUat9TA3L8cuD5Wm8; _ga_0XM0LYXGC8=GS2.1.s1760772234$o226$g1$t1760773388$j5$l0$h0"
        
        elif chain.lower() == 'bsc':
            # BSC 链的配置
            if first_address:
                headers['referer'] = f"https://gmgn.ai/bsc/token/{first_address}"
            else:
                headers['referer'] = "https://gmgn.ai/bsc/token/"
            
            # BSC 链的 Cookie（注意 GMGN_CHAIN 可能不同，但通常还是 sol，因为用户可能从 sol 切换过来）
            headers['Cookie'] = "_ga=GA1.1.1917780211.1737870293; _ga_0XM0LYXGC8=deleted; GMGN_LOCALE=zh-CN; GMGN_THEME=dark; GMGN_CHAIN=sol; cf_clearance=WyR5pgnBwWTZYCs7x0PlUDolJdFSD1_vESoBtJpM86Q-1759673110-1.2.1.1-CtiQQRQkGjQKPDo.ToiJ4YRQTQxG42Su9DFFGm_ZICG6o7za2kPFT33t45wCvYjBRBtHzAEpVRBnNj4f3G..FnTFxUZq4c1sJadh4sQnP.sdTqzCsqoNB9hJLGoVgouwr4SrON61tBhOa4ZZXUqrsQoT55VQiYSEVPizK0BFpidCYD2dwOi3A5dIVVyEfRTScd1yGBjT6Ls4Jp3ylpPbuyIPlfGAkeNR_bz7jS1ZJ9I; __cf_bm=z1GT01NqlekRZiyvWNhyvy3fKYqZ91i9pP3h8IH.x60-1760771944-1.0.1.1-d3iN09_wcI599HycMRn8ipAs7I06p7t6HTPEZ_MYZPnE2PVUXIjLBmgsOugTyFJCcecFXtuumRE32cq6F2Siqm3OSHC1qZkniWI_M4ShdEo; sid=gmgn%7Cb03bdd6b7520e3bd4fca97456bec170d; _ga_UGLVBMV4Z0=GS1.2.1760772226676993.245e6720757dcf65dbf07509eaa78e45.1HeDju0RMx9thEVSmQnBRA%3D%3D.X5%2FnIa8F8is8SxbIC881HQ%3D%3D.7yaEtYt33EhexBLBa3Preg%3D%3D.kw17uCNO31i4Bot0D7YriA%3D%3D; _ga_0XM0LYXGC8=GS2.1.s1760772234$o226$g0$t1760772234$j60$l0$h0"
        
        else:
            # 默认配置
            headers['referer'] = "https://gmgn.ai/"
            headers['Cookie'] = "_ga=GA1.1.1917780211.1737870293; GMGN_LOCALE=zh-CN; GMGN_THEME=dark; GMGN_CHAIN=sol"
        
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
            data = response.json()
            
            if data.get('code') == 0 and 'data' in data:
                token_count = len(data['data']) if data['data'] else 0
                logger.info(f"✅ GMGN API 查询成功 (chain={chain}, 返回{token_count}个Token)")
                return data['data']
            else:
                logger.error(f"❌ GMGN API 返回错误 (chain={chain}): code={data.get('code')}, message={data.get('message', 'Unknown error')}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error(f"GMGN API 请求超时 (chain={chain}, addresses={len(addresses)})")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"GMGN API 连接错误: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"GMGN API HTTP 错误: {e}")
            return None
        except ValueError as e:
            logger.error(f"GMGN API 返回数据解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"GMGN API 请求异常: {e}")
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

