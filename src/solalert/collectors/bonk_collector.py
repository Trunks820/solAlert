"""
BONK 数据采集器
从 Raydium API 采集 BONK 平台毕业的 Token 数据
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


class BonkCollector(BaseCollector):
    """BONK采集器 - 采集Raydium平台毕业的Token"""
    
    # Raydium API配置
    API_BASE_URL = "https://launch-mint-v1.raydium.io"
    API_ENDPOINT = "/get/list"
    
    # 请求参数
    DEFAULT_PARAMS = {
        'sort': 'new',                  # 按时间排序
        'size': 100,                    # 每页100条
        'mintType': 'graduated',        # 只获取已毕业的
        'includeNsfw': False,           # 不包含NSFW
        'platformId': 'FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1'  # letsbonk.fun平台ID
    }
    
    # HTTP Headers
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'sec-ch-ua-platform': '"Windows"',
        'sec-ch-ua': '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        'sec-ch-ua-mobile': '?0',
        'origin': 'https://raydium.io',
        'sec-fetch-site': 'same-site',
        'sec-fetch-mode': 'cors',
        'sec-fetch-dest': 'empty',
        'referer': 'https://raydium.io/',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'priority': 'u=1, i'
    }
    
    def __init__(self, poll_interval: int = 60):
        """
        初始化BONK采集器
        
        Args:
            poll_interval: 轮询间隔（秒），默认60秒
        """
        super().__init__("BonkCollector")
        self.poll_interval = poll_interval
        self.token_repo = TokenRepository()
        self.session = self._create_session()
        self.last_seen_mint = None  # 记录最后处理的mint地址，避免重复
    
    def _create_session(self) -> requests.Session:
        """
        创建带重试机制的HTTP会话
        
        Returns:
            配置好的Session对象
        """
        session = requests.Session()
        
        # 配置重试策略
        retry_strategy = Retry(
            total=3,                    # 最多重试3次
            backoff_factor=1,           # 重试间隔倍数
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
        self.log_info("BONK采集器启动")
        
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
                    await asyncio.sleep(10)  # 出错后等待10秒再重试
                    
        except Exception as e:
            self.log_error(f"采集器异常退出: {e}", e)
        finally:
            self.is_running = False
            self.session.close()
    
    async def stop(self):
        """停止采集器"""
        self.log_info("正在停止BONK采集器...")
        self.is_running = False
        self.session.close()
    
    async def _collect_initial_data(self):
        """
        初始化时采集历史数据
        默认采集最近500条（5页）
        """
        self.log_info("开始采集BONK历史数据...")
        
        total_collected = 0
        total_saved = 0
        next_page_id = None
        max_pages = 5  # 最多采集5页
        
        for page in range(max_pages):
            try:
                # 构建请求参数
                params = self.DEFAULT_PARAMS.copy()
                if next_page_id:
                    params['nextPageId'] = next_page_id
                
                # 发起请求
                data = await self._fetch_api(params)
                if not data:
                    break
                
                rows = data.get('rows', [])
                if not rows:
                    self.log_info(f"第{page + 1}页无数据，停止采集")
                    break
                
                # 处理数据
                saved_count = await self._process_tokens(rows)
                total_collected += len(rows)
                total_saved += saved_count
                
                self.log_info(f"第{page + 1}页: 获取{len(rows)}条，入库{saved_count}条")
                
                # 获取下一页ID
                next_page_id = data.get('nextPageId')
                if not next_page_id:
                    self.log_info("已到达最后一页")
                    break
                
                # 避免请求过快
                await asyncio.sleep(1)
                
            except Exception as e:
                self.log_error(f"采集第{page + 1}页失败: {e}", e)
                break
        
        self.log_success(f"历史数据采集完成: 共获取{total_collected}条，成功入库{total_saved}条")
    
    async def _poll_new_tokens(self):
        """
        轮询获取最新的Token
        只获取第一页数据，检查是否有新Token
        """
        try:
            # 只获取第一页
            data = await self._fetch_api(self.DEFAULT_PARAMS)
            if not data:
                return
            
            rows = data.get('rows', [])
            if not rows:
                return
            
            # 如果是首次轮询，记录第一个mint
            if self.last_seen_mint is None:
                self.last_seen_mint = rows[0]['mint']
                self.log_info(f"开始监控，当前最新Token: {rows[0]['symbol']}")
                return
            
            # 找出新Token（在last_seen_mint之前的所有记录）
            new_tokens = []
            for token in rows:
                if token['mint'] == self.last_seen_mint:
                    break
                new_tokens.append(token)
            
            if new_tokens:
                # 更新last_seen_mint
                self.last_seen_mint = new_tokens[0]['mint']
                
                # 处理新Token
                saved_count = await self._process_tokens(new_tokens)
                self.log_success(f"发现{len(new_tokens)}个新Token，成功入库{saved_count}条")
            
        except Exception as e:
            self.log_error(f"轮询失败: {e}", e)
    
    async def _fetch_api(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        调用Raydium API
        
        Args:
            params: 请求参数
            
        Returns:
            API响应的data字段，失败返回None
        """
        try:
            url = f"{self.API_BASE_URL}{self.API_ENDPOINT}"
            
            response = self.session.get(
                url,
                params=params,
                headers=self.HEADERS,
                timeout=30
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('success') and result.get('data'):
                return result['data']
            else:
                self.log_error(f"API返回失败: {result}")
                return None
                
        except requests.exceptions.RequestException as e:
            self.log_error(f"API请求失败: {e}", e)
            return None
        except Exception as e:
            self.log_error(f"解析API响应失败: {e}", e)
            return None
    
    async def _process_tokens(self, tokens: List[Dict[str, Any]]) -> int:
        """
        处理Token列表，保存到数据库
        
        Args:
            tokens: Token列表
            
        Returns:
            成功入库的数量
        """
        saved_count = 0
        
        for token in tokens:
            try:
                if self._save_token(token):
                    saved_count += 1
            except Exception as e:
                self.log_error(f"保存Token失败 [{token.get('symbol')}]: {e}", e)
        
        return saved_count
    
    def _save_token(self, token_data: Dict[str, Any]) -> bool:
        """
        保存单个Token到数据库
        
        Args:
            token_data: Token原始数据
            
        Returns:
            是否成功
        """
        try:
            # 提取数据
            ca = token_data['mint']
            token_name = token_data.get('name')
            token_symbol = token_data.get('symbol')
            
            # 处理Twitter URL（只保存正常的Twitter链接，过滤掉搜索链接等异常URL）
            twitter_url = token_data.get('twitter')
            if twitter_url:
                # 检查是否为正常的Twitter链接
                if '/search?' in twitter_url or len(twitter_url) > 255:
                    # 如果是搜索链接或超长链接，记录警告并设为None
                    logger.warning(f"⚠️ 过滤异常Twitter URL: {token_name} | URL: {twitter_url[:80]}...")
                    twitter_url = None
            
            # 处理发射时间（毫秒时间戳转datetime，并转换为UTC+8北京时间）
            create_at_ms = token_data.get('createAt', 0)
            launch_time_utc = datetime.fromtimestamp(create_at_ms / 1000, tz=timezone.utc)
            # 转换为UTC+8（北京时间）
            launch_time = launch_time_utc + timedelta(hours=8)
            # 移除时区信息，因为数据库存的是naive datetime
            launch_time = launch_time.replace(tzinfo=None)
            
            # 构建tg_msg_id（使用poolId作为标识）
            pool_id = token_data.get('poolId', '')
            tg_msg_id = f"bonk_{pool_id[:8]}"
            
            # 获取市值
            market_cap = int(token_data.get('marketCap', 0))
            
            # 调用保存方法
            return self._insert_bonk_token(
                ca=ca,
                token_name=token_name,
                token_symbol=token_symbol,
                twitter_url=twitter_url if twitter_url else None,
                launch_time=launch_time,
                tg_msg_id=tg_msg_id,
                market_cap=market_cap
            )
            
        except Exception as e:
            self.log_error(f"解析Token数据失败: {e}", e)
            return False
    
    def _insert_bonk_token(
        self,
        ca: str,
        token_name: Optional[str],
        token_symbol: Optional[str],
        twitter_url: Optional[str],
        launch_time: datetime,
        tg_msg_id: str,
        market_cap: int = 0
    ) -> bool:
        """
        插入BONK token数据（重复数据直接跳过，不更新市值）
        
        Args:
            ca: 合约地址
            token_name: Token名称
            token_symbol: Token符号
            twitter_url: Twitter链接
            launch_time: 发射时间
            tg_msg_id: 标识ID
            market_cap: 市值（不保存，设为0）
            
        Returns:
            是否成功
        """
        try:
            # 先检查是否已存在（根据ca+source唯一索引）
            check_sql = "SELECT ca FROM token_launch_history WHERE ca = %s AND source = 'bonk' LIMIT 1"
            existing = self.token_repo.db.execute_query(check_sql, (ca,), fetch_one=True)
            
            if existing:
                # 已存在，直接跳过
                logger.debug(f"⏭️  跳过重复数据: {token_name} ({ca[:8]}...)")
                return False
            
            # 不存在，插入新数据（市值设为0，后续由定时任务更新）
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
                'bonk',  # 设置source为bonk
                launch_time,
                tg_msg_id,
                0,  # 市值设为0，不保存API返回的当前市值
                datetime.now()
            )
            
            rowcount = self.token_repo.db.execute_update(sql, params)
            
            # 如果Token插入成功，同时保存Twitter账号（自动识别类型）
            if rowcount > 0 and twitter_url:
                self.token_repo.insert_twitter_account(twitter_url)
            
            # 输出日志
            twitter_info = f" [Twitter: {twitter_url}]" if twitter_url else ""
            
            if rowcount > 0:
                logger.info(f"✅ 新BONK数据已入库: {token_name} ({ca[:8]}...){twitter_info}")
                return True
            else:
                logger.debug(f"⚠️  插入失败: {ca[:8]}...")
                return False
                
        except Exception as e:
            # 报错时打印详细字段信息，帮助定位问题
            logger.error(f"❌ 插入BONK数据失败: {e}")
            logger.error(f"   Token: {token_name} ({token_symbol})")
            logger.error(f"   CA: {ca} (长度:{len(ca)})")
            logger.error(f"   Twitter: {twitter_url} (长度:{len(twitter_url) if twitter_url else 0})")
            logger.error(f"   名称长度: {len(token_name) if token_name else 0}")
            logger.error(f"   符号长度: {len(token_symbol) if token_symbol else 0}")
            logger.error(f"   tg_msg_id: {tg_msg_id} (长度:{len(tg_msg_id)})")
            return False


# ==================== 独立运行测试 ====================

async def main():
    """测试运行BONK采集器"""
    collector = BonkCollector(poll_interval=60)
    
    try:
        await collector.start()
    except KeyboardInterrupt:
        print("\n收到停止信号...")
        await collector.stop()


if __name__ == '__main__':
    asyncio.run(main())

