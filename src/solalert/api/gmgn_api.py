"""
GMGN API 客户端
用于获取 Token 的实时价格、交易量、持有人数等数据
"""
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.config import GMGN_COOKIE_CONFIG, HTTP_PROXY_CONFIG

logger = logging.getLogger('solalert.api.gmgn_api')


class GmgnAPI:
    """GMGN API 客户端"""
    
    def __init__(self):
        self.base_url = "https://gmgn.ai/api/v1/mutil_window_token_info"
        self.launchpad_url = "https://gmgn.ai/api/v1/mutil_window_token_security_launchpad"
        
        # 查询参数 - BSC 和 SOL 使用不同的版本
        self.params_bsc = {
            'device_id': '5461cc48-2e83-4740-8e90-8409db8cf33d',
            'fp_did': '68fd1291b5f4f826d98e6378c0e3a663',
            'client_id': 'gmgn_web_20251022-5795-0f4e34a',
            'from_app': 'gmgn',
            'app_ver': '20251022-5795-0f4e34a',
            'tz_name': 'Asia/Shanghai',
            'tz_offset': '28800',
            'app_lang': 'zh-CN',
            'os': 'web'
        }
        
        self.params_sol = {
            'device_id': 'b3bec9fb-aaba-4839-a375-214428819024',
            'fp_did': '3e6d1c9f9c4df6f1c8be8da1865b8ea1',
            'client_id': 'gmgn_web_20251022-5727-c8b740e',
            'from_app': 'gmgn',
            'app_ver': '20251022-5727-c8b740e',
            'tz_name': 'Asia/Shanghai',
            'tz_offset': '28800',
            'app_lang': 'zh-CN',
            'os': 'web'
        }
        self.cookies = {
            'sol': (GMGN_COOKIE_CONFIG.get('sol') or '').strip(),
            'bsc': (GMGN_COOKIE_CONFIG.get('bsc') or '').strip(),
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
        
        # 配置代理（如果启用）
        logger.info(f"🔍 HTTP_PROXY_CONFIG enabled: {HTTP_PROXY_CONFIG.get('enabled')}")
        if HTTP_PROXY_CONFIG.get('enabled'):
            http_proxy = (HTTP_PROXY_CONFIG.get('http_proxy') or '').strip()
            https_proxy = (HTTP_PROXY_CONFIG.get('https_proxy') or '').strip() or http_proxy
            logger.info(f"🔍 http_proxy: {http_proxy}, https_proxy: {https_proxy}")
            proxies = {}
            if http_proxy:
                proxies['http'] = http_proxy
            if https_proxy:
                proxies['https'] = https_proxy
            if proxies:
                session.proxies = proxies
                logger.info(f"✅ GMGN API 使用代理: {proxies}")
        else:
            logger.warning("⚠️ GMGN API 代理未启用！")
        
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
    
    def _prepare_cookie(self, chain: str) -> Optional[str]:
        """
        根据链类型返回配置中的真实 Cookie，并确保 GMGN_CHAIN 值匹配。
        """
        chain_key = (chain or '').lower()
        cookie = self.cookies.get(chain_key)
        if not cookie:
            return None
        if 'GMGN_CHAIN=' in cookie:
            cookie = re.sub(r'(GMGN_CHAIN=)([^;]+)', rf'\1{chain_key}', cookie)
        else:
            suffix = '' if cookie.endswith(';') else ';'
            cookie = f"{cookie}{suffix} GMGN_CHAIN={chain_key}"
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
        # 根据链类型使用不同的 baggage 和 sentry-trace
        if chain.lower() == 'bsc':
            baggage = "sentry-environment=production,sentry-release=20251022-5727-c8b740e,sentry-public_key=93c25bab7246077dc3eb85b59d6e7d40,sentry-trace_id=ca3fb5a2af01470c9e3a76c9905971c8,sentry-sample_rate=0.01,sentry-sampled=false"
            sentry_trace = "d1a9e31a722d41198c18f57e58a5aede-9deb391aa9de5bd7-0"
        else:  # sol
            baggage = "sentry-environment=production,sentry-release=20250807-2092-28237ac,sentry-public_key=93c25bab7246077dc3eb85b59d6e7d40,sentry-trace_id=76e65ba658f7474db1728af8e6be117c,sentry-sample_rate=0.01,sentry-sampled=false"
            sentry_trace = "76e65ba658f7474db1728af8e6be117c-bab9640fa6a86171-0"
        
        # 基础请求头（完全按照用户提供的样例）
        headers = {
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip, deflate, br, zstd",
            'Content-Type': "application/json",
            'sec-ch-ua-full-version-list': '"Not)A;Brand";v="8.0.0.0", "Chromium";v="138.0.7204.169", "Google Chrome";v="138.0.7204.169"',
            'sec-ch-ua-platform': '"Windows"',
            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            'sec-ch-ua-bitness': '"64"',
            'sec-ch-ua-model': '""',
            'sec-ch-ua-mobile': "?0",
            'baggage': baggage,
            'sentry-trace': sentry_trace,
            'sec-ch-ua-arch': '"x86"',
            'sec-ch-ua-full-version': '"138.0.7204.169"',
            'sec-ch-ua-platform-version': '"15.0.0"',
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
        
        cookie = self._prepare_cookie(chain)
        if cookie:
            headers['Cookie'] = cookie
        else:
            logger.error(f"❌ 缺少 GMGN Cookie 配置 (chain={chain})，请设置环境变量 GMGN_COOKIE_{chain.upper()}")
        
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
        
        # 动态更新代理配置（确保使用最新的环境变量）
        from ..core.config import HTTP_PROXY_CONFIG
        if HTTP_PROXY_CONFIG.get('enabled'):
            http_proxy = (HTTP_PROXY_CONFIG.get('http_proxy') or '').strip()
            https_proxy = (HTTP_PROXY_CONFIG.get('https_proxy') or '').strip() or http_proxy
            if http_proxy or https_proxy:
                self.session.proxies = {}
                if http_proxy:
                    self.session.proxies['http'] = http_proxy
                if https_proxy:
                    self.session.proxies['https'] = https_proxy
        
        try:
            payload = {
                "chain": chain,
                "addresses": addresses
            }
            
            # 根据链类型选择对应的 params 和 headers
            params = self.params_bsc if chain.lower() == 'bsc' else self.params_sol
            headers = self._get_headers(chain, addresses[0] if addresses else "")
            
            # 使用 data=json.dumps() 而不是 json=payload，完全模拟浏览器行为
            response = self.session.post(
                self.base_url,
                params=params,
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )
            
            response.raise_for_status()
            
            # 检查响应内容
            if not response.content:
                logger.error(f"❌ GMGN API 返回空响应 (chain={chain}, addresses={addresses})")
                return None
            
            # 让 requests 自动处理解压（需要安装 brotli 库）
            # requests 会根据 Content-Encoding 自动解压 gzip/deflate/br
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"❌ GMGN API JSON 解析失败 (chain={chain})")
                logger.error(f"   响应状态码: {response.status_code}")
                logger.error(f"   Content-Type: {response.headers.get('Content-Type')}")
                logger.error(f"   Content-Encoding: {response.headers.get('Content-Encoding', 'none')}")
                
                # 尝试手动解压（备用方案）
                content_encoding = response.headers.get('Content-Encoding', '').lower()
                if content_encoding == 'br':
                    try:
                        import brotli
                        decompressed = brotli.decompress(response.content)
                        data = json.loads(decompressed.decode('utf-8'))
                        logger.info("✅ 手动 Brotli 解压成功")
                    except ImportError:
                        logger.error("❌ 缺少 brotli 库，请安装: pip install brotli")
                        logger.error("   提示: 在 Docker 容器中运行 'pip install brotli' 或重新构建镜像")
                        return None
                    except Exception as br_error:
                        logger.error(f"❌ 手动 Brotli 解压失败: {br_error}")
                        logger.error(f"   响应前50字节hex: {response.content[:50].hex()}")
                        return None
                else:
                    logger.error(f"   响应前100字符: {response.text[:100]}")
                    return None
            
            if data.get('code') == 0 and 'data' in data:
                return data['data']
            else:
                logger.debug(f"GMGN API 错误: code={data.get('code')}")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"GMGN API 超时: {chain}")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GMGN API 连接错误: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning(f"GMGN API HTTP错误 {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"GMGN API 异常: {e}")
            return None
    
    def get_token_launchpad_info(
        self,
        chain: str,
        address: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取 Token 的 launchpad 信息（用于判断是否来自特定平台）
        
        Args:
            chain: 链名称 ('sol' 或 'bsc')
            address: Token 地址
            
        Returns:
            launchpad 信息字典，如果失败返回 None
            {
                "launchpad": "fourmeme",  # 平台名称，null 表示不是 launchpad 代币
                "launchpad_status": 1,
                "launchpad_progress": "1",
                "launchpad_platform": "fourmeme"
            }
        """
        if not address:
            return None
        
        # BSC 地址需要转换为小写
        if chain.lower() == 'bsc':
            address = address.lower()
        
        try:
            # 构建完整 URL
            url = f"{self.launchpad_url}/{chain}/{address}"
            
            # 根据链类型选择对应的 params 和 headers
            params = self.params_bsc if chain.lower() == 'bsc' else self.params_sol
            headers = self._get_headers(chain, address)
            
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"🔍 GMGN Launchpad 请求: {address}... 状态码={response.status_code}")
            
            response.raise_for_status()
            
            # 检查响应内容
            if not response.content:
                logger.error(f"❌ GMGN Launchpad API 返回空响应 (chain={chain}, address={address})")
                return None
            
            # 解析 JSON
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"❌ GMGN Launchpad API JSON 解析失败 (chain={chain})")
                # 尝试手动解压（如果是 brotli 压缩）
                content_encoding = response.headers.get('Content-Encoding', '').lower()
                if content_encoding == 'br':
                    try:
                        import brotli
                        decompressed = brotli.decompress(response.content)
                        data = json.loads(decompressed.decode('utf-8'))
                        logger.info("✅ 手动 Brotli 解压成功")
                    except ImportError:
                        logger.error("❌ 缺少 brotli 库")
                        return None
                    except Exception as br_error:
                        logger.error(f"❌ 手动 Brotli 解压失败: {br_error}")
                        return None
                else:
                    logger.error(f"   响应前100字符: {response.text[:100]}")
                    return None
            
            # 打印完整响应
            logger.info(f"   完整响应: {json.dumps(data, ensure_ascii=False)}")
            
            if data.get('code') == 0 and 'data' in data:
                launchpad_data = data['data'].get('launchpad')
                logger.info(f"   Launchpad 数据: {launchpad_data}")
                return launchpad_data
            else:
                logger.warning(f"   API 返回 code={data.get('code')}, has_data={('data' in data)}")
                return None
                
        except requests.exceptions.Timeout:
            logger.debug(f"GMGN Launchpad API 超时: {address[:10]}...")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"GMGN Launchpad API 连接错误: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.debug(f"GMGN Launchpad API HTTP错误 {e.response.status_code}")
            return None
        except Exception as e:
            logger.debug(f"GMGN Launchpad API 异常: {e}")
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
            
            # 获取市值和供应量信息（确保转换为 float）
            def safe_float(val):
                """安全转换为 float"""
                if val is None or val == '' or val == 0:
                    return 0
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return 0
            
            market_cap = safe_float(token_data.get('market_cap', 0))
            circulating_supply = safe_float(token_data.get('circulating_supply', 0))
            total_supply = safe_float(token_data.get('total_supply', 0))
            max_supply = safe_float(token_data.get('max_supply', 0))
            
            # 如果 API 没有直接返回 market_cap，按优先级计算：
            # 1. 价格 × 流通供应量 (最标准)
            # 2. 价格 × 总供应量
            # 3. 价格 × 最大供应量
            price = float(price_info.get('price', 0))
            if not market_cap and price:
                if circulating_supply:
                    market_cap = price * circulating_supply
                elif total_supply:
                    market_cap = price * total_supply
                elif max_supply:
                    market_cap = price * max_supply
            
            return {
                # 基础信息
                'address': token_data.get('address'),
                'symbol': token_data.get('symbol'),
                'name': token_data.get('name'),
                'logo': token_data.get('logo', ''),  # Token Logo
                'holder_count': token_data.get('holder_count', 0),
                'holders': token_data.get('holder_count', 0),  # 别名
                'liquidity': float(token_data.get('liquidity', 0)),
                
                # 市值和供应量
                'market_cap': float(market_cap) if market_cap else 0,
                'circulating_supply': float(circulating_supply) if circulating_supply else 0,
                'total_supply': float(total_supply) if total_supply else 0,
                'max_supply': float(max_supply) if max_supply else 0,
                
                # 价格信息
                'price': price,
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

