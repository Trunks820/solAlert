"""
Four.meme 数据采集器
从 Four.meme API 采集 BSC 链上的 Meme Token (内盘+外盘)
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import BaseCollector
from ..repositories.token_repo import TokenRepository

logger = logging.getLogger(__name__)


class FourMemeCollector(BaseCollector):
    """Four.meme采集器 - 采集BSC链上的Meme Token"""

    # Four.meme API配置
    API_BASE_URL = "https://four.meme/meme-api/v1/private/token/query"

    # 请求参数模板
    BASE_PARAMS = {
        'orderBy': 'BnTimeDesc',      # 按时间降序
        'queryMode': 'Binance',
        'tokenName': '',
        'pageIndex': '1',
        'pageSize': '30',
        'symbol': '',
        'labels': ''
    }

    # HTTP Headers
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'sec-ch-ua-platform': '"Windows"',
        'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        'sec-ch-ua-mobile': '?0',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'cors',
        'sec-fetch-dest': 'empty',
        'referer': 'https://four.meme/zh-TW/binance-exclusive-token-list',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'priority': 'u=1, i'
    }
    
    def __init__(self, poll_interval: int = 60):
        """
        初始化Four.meme采集器

        Args:
            poll_interval: 轮询间隔（秒），默认60秒
        """
        super().__init__("FourMemeCollector")
        self.poll_interval = poll_interval
        self.token_repo = TokenRepository()
        self.session = self._create_session()
        
        # 记录最后处理的Token ID，避免重复（内盘和外盘分开记录）
        self.last_seen_inner_id = None  # 内盘
        self.last_seen_outer_id = None  # 外盘

    def _create_session(self) -> requests.Session:
        """
        创建带重试机制的HTTP会话

        Returns:
            配置好的Session对象
        """
        session = requests.Session()

        # 配置重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        return session

    async def start(self):
        """启动采集器"""
        self.is_running = True
        self.log_info("🚀 Four.meme采集器启动")

        try:
            # 首次启动时，先采集一次历史数据
            await self._collect_initial_data()

            # 然后进入轮询模式
            while self.is_running:
                try:
                    await self._poll_new_tokens()
                    await asyncio.sleep(self.poll_interval)
                except Exception as e:
                    self.log_error(f"轮询出错: {e}", e)
                    await asyncio.sleep(10)

        except Exception as e:
            self.log_error(f"采集器异常退出: {e}", e)
        finally:
            self.is_running = False
            self.session.close()

    async def stop(self):
        """停止采集器"""
        self.log_info("正在停止Four.meme采集器...")
        self.is_running = False
        self.session.close()

    async def _collect_initial_data(self):
        """
        初始化时采集历史数据
        分别采集内盘和外盘的所有新数据（直到遇到重复或无数据）
        """
        self.log_info("开始采集Four.meme历史数据...")
        
        total_collected = 0
        total_saved = 0

        # 1. 采集外盘（已上Pancakeswap）
        self.log_info("📊 采集外盘数据（已上Pancakeswap）...")
        outer_collected, outer_saved = await self._collect_all_pages('true')
        total_collected += outer_collected
        total_saved += outer_saved

        # 2. 采集内盘（未上Pancakeswap）
        self.log_info("📊 采集内盘数据（内盘交易）...")
        inner_collected, inner_saved = await self._collect_all_pages('false')
        total_collected += inner_collected
        total_saved += inner_saved

        self.log_success(f"历史数据采集完成: 共获取 {total_collected} 条，成功入库 {total_saved} 条")

    async def _collect_all_pages(self, listed_pancake: str) -> tuple:
        """
        采集指定类型的所有Token（持续采集直到遇到重复或无数据）

        Args:
            listed_pancake: 'true'=外盘, 'false'=内盘

        Returns:
            (采集总数, 保存总数)
        """
        total_collected = 0
        total_saved = 0
        page = 1
        consecutive_zero_saves = 0  # 连续0条新入库的页数

        while True:
            try:
                # 构建请求参数
                params = self.BASE_PARAMS.copy()
                params['listedPancake'] = listed_pancake
                params['pageIndex'] = str(page)

                # 发起请求
                data = await self._fetch_api(params)
                if not data:
                    self.log_info(f"   API返回失败，停止采集")
                    break

                tokens = data.get('data', [])
                if not tokens:
                    self.log_info(f"   第{page}页无数据，采集完成")
                    break

                # 处理数据
                saved_count = await self._process_tokens(tokens, listed_pancake)
                total_collected += len(tokens)
                total_saved += saved_count

                self.log_info(f"   第{page}页: 获取{len(tokens)}条，入库{saved_count}条")

                # 记录最后一个ID
                if tokens:
                    last_id = tokens[0]['id']
                    if listed_pancake == 'true':
                        self.last_seen_outer_id = last_id
                    else:
                        self.last_seen_inner_id = last_id

                # 如果这一页没有任何新数据入库，计数+1
                if saved_count == 0:
                    consecutive_zero_saves += 1
                    # 连续3页都没有新数据，说明已经采集完了
                    if consecutive_zero_saves >= 3:
                        self.log_info(f"   连续{consecutive_zero_saves}页无新数据，采集完成")
                        break
                else:
                    # 有新数据，重置计数
                    consecutive_zero_saves = 0

                # 下一页
                page += 1

                # 避免请求过快
                await asyncio.sleep(1)

            except Exception as e:
                self.log_error(f"采集第{page}页失败: {e}", e)
                break

        return total_collected, total_saved

    async def _collect_page(self, listed_pancake: str, max_pages: int = 3) -> tuple:
        """
        采集指定类型的Token

        Args:
            listed_pancake: 'true'=外盘, 'false'=内盘
            max_pages: 最多采集页数

        Returns:
            (采集总数, 保存总数)
        """
        total_collected = 0
        total_saved = 0

        for page in range(1, max_pages + 1):
            try:
                # 构建请求参数
                params = self.BASE_PARAMS.copy()
                params['listedPancake'] = listed_pancake
                params['pageIndex'] = str(page)

                # 发起请求
                data = await self._fetch_api(params)
                if not data:
                    break

                tokens = data.get('data', [])
                if not tokens:
                    self.log_info(f"   第{page}页无数据，停止采集")
                    break

                # 处理数据
                saved_count = await self._process_tokens(tokens, listed_pancake)
                total_collected += len(tokens)
                total_saved += saved_count

                self.log_info(f"   第{page}页: 获取{len(tokens)}条，入库{saved_count}条")

                # 记录最后一个ID
                if tokens:
                    last_id = tokens[0]['id']
                    if listed_pancake == 'true':
                        self.last_seen_outer_id = last_id
                    else:
                        self.last_seen_inner_id = last_id

                # 避免请求过快
                await asyncio.sleep(1)

            except Exception as e:
                self.log_error(f"采集第{page}页失败: {e}", e)
                break

        return total_collected, total_saved

    async def _poll_new_tokens(self):
        """
        轮询获取最新的Token
        分别检查内盘和外盘
        """
        try:
            # 1. 检查外盘新Token
            await self._poll_by_type('true', self.last_seen_outer_id)
            
            # 2. 检查内盘新Token
            await self._poll_by_type('false', self.last_seen_inner_id)

        except Exception as e:
            self.log_error(f"轮询失败: {e}", e)

    async def _poll_by_type(self, listed_pancake: str, last_seen_id: Optional[int]):
        """
        轮询指定类型的新Token

        Args:
            listed_pancake: 'true'=外盘, 'false'=内盘
            last_seen_id: 最后看到的Token ID
        """
        try:
            # 只获取第一页
            params = self.BASE_PARAMS.copy()
            params['listedPancake'] = listed_pancake
            params['pageIndex'] = '1'

            data = await self._fetch_api(params)
            if not data:
                return

            tokens = data.get('data', [])
            if not tokens:
                return

            # 如果是第一次轮询，记录第一个ID
            if last_seen_id is None:
                first_id = tokens[0]['id']
                if listed_pancake == 'true':
                    self.last_seen_outer_id = first_id
                    self.log_info(f"开始监控外盘，当前最新Token: {tokens[0]['name']}")
                else:
                    self.last_seen_inner_id = first_id
                    self.log_info(f"开始监控内盘，当前最新Token: {tokens[0]['name']}")
                return

            # 找出新Token（在last_seen_id之前的所有记录）
            new_tokens = []
            for token in tokens:
                if token['id'] == last_seen_id:
                    break
                new_tokens.append(token)

            if new_tokens:
                # 更新last_seen_id
                new_id = new_tokens[0]['id']
                if listed_pancake == 'true':
                    self.last_seen_outer_id = new_id
                else:
                    self.last_seen_inner_id = new_id

                # 处理新Token
                saved_count = await self._process_tokens(new_tokens, listed_pancake)
                market_type = "外盘" if listed_pancake == 'true' else "内盘"
                self.log_success(f"发现{len(new_tokens)}个新{market_type}Token，成功入库{saved_count}条")

        except Exception as e:
            self.log_error(f"轮询失败: {e}", e)

    async def _fetch_api(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        调用Four.meme API

        Args:
            params: 请求参数

        Returns:
            API响应，失败返回None
        """
        try:
            # 移除 Accept-Encoding 头，让 requests 自动处理压缩
            headers = self.HEADERS.copy()
            headers.pop('Accept-Encoding', None)
            
            response = self.session.get(
                self.API_BASE_URL,
                params=params,
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()

            # 使用 response.json() 会自动处理解压和 JSON 解析
            result = response.json()

            if result.get('code') == 0:
                return result
            else:
                self.log_error(f"API返回错误码: {result}")
                return None

        except requests.exceptions.Timeout as e:
            self.log_error(f"API请求超时: {e}")
            return None
        except requests.exceptions.ConnectionError as e:
            self.log_error(f"API连接失败: {e}")
            return None
        except requests.exceptions.HTTPError as e:
            self.log_error(f"API HTTP错误: {e}")
            return None
        except ValueError as e:
            # JSON解析失败
            self.log_error(f"JSON解析失败: {e}")
            return None
        except Exception as e:
            self.log_error(f"API请求未知错误: {e}", e)
            return None

    async def _process_tokens(self, tokens: List[Dict[str, Any]], listed_pancake: str) -> int:
        """
        处理Token列表，保存到数据库

        Args:
            tokens: Token列表
            listed_pancake: 'true'=外盘, 'false'=内盘

        Returns:
            成功入库的数量
        """
        saved_count = 0

        for token in tokens:
            try:
                if self._save_token(token, listed_pancake):
                    saved_count += 1
            except Exception as e:
                self.log_error(f"保存Token失败 [{token.get('name')}]: {e}", e)

        return saved_count

    def _save_token(self, token_data: Dict[str, Any], listed_pancake: str) -> bool:
        """
        保存单个Token到数据库

        Args:
            token_data: Token原始数据
            listed_pancake: 'true'=外盘, 'false'=内盘

        Returns:
            是否成功
        """
        try:
            # 提取数据
            ca = token_data['address']
            # 注意：API的symbol字段统一为"BNB_MPC"，不是真正的token符号
            # shortName是token_symbol，name是token_name
            token_name = token_data.get('name')  # Token名称
            token_symbol = token_data.get('shortName') or token_data.get('name')  # Token符号
            twitter_url = token_data.get('twitterUrl')  # Four.meme有直接的twitter字段

            # 处理发射时间（毫秒时间戳转datetime，并转换为UTC+8 北京时间）
            launch_time_ms = token_data.get('launchTime', 0)
            launch_time_utc = datetime.fromtimestamp(launch_time_ms / 1000, tz=timezone.utc)
            # 转换为UTC+8（北京时间）
            launch_time = launch_time_utc + timedelta(hours=8)
            # 移除时区信息，因为数据库存的是naive datetime
            launch_time = launch_time.replace(tzinfo=None)

            # 构建tg_msg_id（使用token id作为标识）
            token_id = token_data.get('id', 0)
            market_type = 'outer' if listed_pancake == 'true' else 'inner'
            tg_msg_id = f"fourmeme_{market_type}_{token_id}"

            # 获取当前市值作为初始highest_market_cap
            token_price = token_data.get('tokenPrice', {})
            market_cap_str = token_price.get('marketCap', '0')
            try:
                market_cap = float(market_cap_str) if market_cap_str else 0
            except (ValueError, TypeError):
                market_cap = 0

            # 调用保存方法
            return self._insert_fourmeme_token(
                ca=ca,
                token_name=token_name,
                token_symbol=token_symbol,
                twitter_url=twitter_url if twitter_url else None,
                launch_time=launch_time,
                tg_msg_id=tg_msg_id,
                market_cap=market_cap,
                market_type=market_type
            )

        except Exception as e:
            self.log_error(f"解析Token数据失败: {e}", e)
            return False

    def _insert_fourmeme_token(
        self,
        ca: str,
        token_name: Optional[str],
        token_symbol: Optional[str],
        twitter_url: Optional[str],
        launch_time: datetime,
        tg_msg_id: str,
        market_cap: float = 0,
        market_type: str = 'outer'
    ) -> bool:
        """
        插入Four.meme token数据（重复数据直接跳过，不更新市值）

        Args:
            ca: 合约地址（BSC地址）
            token_name: Token名称
            token_symbol: Token符号
            twitter_url: Twitter链接
            launch_time: 发射时间
            tg_msg_id: 标识ID
            market_cap: 当前市值（用作初始highest_market_cap）
            market_type: 'outer'=外盘, 'inner'=内盘

        Returns:
            是否成功
        """
        try:
            # 先检查是否已存在（根据ca+source唯一索引）
            check_sql = "SELECT ca FROM token_launch_history WHERE ca = %s AND source = 'fourmeme' LIMIT 1"
            existing = self.token_repo.db.execute_query(check_sql, (ca,), fetch_one=True)

            if existing:
                # 已存在，直接跳过
                logger.debug(f"⏭️  跳过重复数据: {token_name} ({ca[:10]}...)")
                return False

            # 不存在，插入新数据
            sql = """
            INSERT INTO token_launch_history
            (ca, token_name, token_symbol, twitter_url, source, launch_time, tg_msg_id, highest_market_cap, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            params = (
                ca,
                token_name,
                token_symbol,
                twitter_url,
                'fourmeme',  # 设置source为fourmeme
                launch_time,
                tg_msg_id,
                market_cap,  # 使用API返回的当前市值作为初始值
                datetime.now()
            )

            rowcount = self.token_repo.db.execute_update(sql, params)

            # 如果Token插入成功，同时保存Twitter账号（自动识别类型）
            if rowcount > 0 and twitter_url:
                self.token_repo.insert_twitter_account(twitter_url)

            # 输出日志
            twitter_info = f" [Twitter: {twitter_url}]" if twitter_url else ""
            market_info = f" [市值: ${market_cap:,.2f}]" if market_cap > 0 else ""
            type_emoji = "🔵" if market_type == 'outer' else "🟢"

            if rowcount > 0:
                logger.info(f"{type_emoji} 新Four.meme数据已入库: {token_name} ({ca[:10]}...){twitter_info}{market_info}")
                return True
            else:
                logger.debug(f"⚠️  插入失败: {ca[:10]}...")
                return False

        except Exception as e:
            # 报错时打印详细字段信息，帮助定位问题
            logger.error(f"❌ 插入Four.meme数据失败: {e}")
            logger.error(f"   Token: {token_name} ({token_symbol})")
            logger.error(f"   CA: {ca} (长度:{len(ca)})")
            logger.error(f"   Twitter: {twitter_url} (长度:{len(twitter_url) if twitter_url else 0})")
            logger.error(f"   名称长度: {len(token_name) if token_name else 0}")
            logger.error(f"   符号长度: {len(token_symbol) if token_symbol else 0}")
            logger.error(f"   tg_msg_id: {tg_msg_id} (长度:{len(tg_msg_id)})")
            return False


# ==================== 独立运行测试 ====================

async def main():
    """测试运行Four.meme采集器"""
    collector = FourMemeCollector(poll_interval=60)

    try:
        await collector.start()
    except KeyboardInterrupt:
        print("\n收到停止信号...")
        await collector.stop()


if __name__ == '__main__':
    asyncio.run(main())

