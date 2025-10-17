"""
Jupiter API 封装
用于获取Token的价格和统计数据
"""
import httpx
import asyncio
from typing import Optional, Dict, Any
from ..core.logger import get_logger

logger = get_logger(__name__)


class JupiterAPI:
    """Jupiter API 客户端"""
    
    def __init__(self, timeout: int = 10, max_retries: int = 3):
        """
        初始化Jupiter API客户端
        
        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        self.base_url = "https://lite-api.jup.ag"
        self.timeout = timeout
        self.max_retries = max_retries
        self.client: Optional[httpx.AsyncClient] = None
    
    async def init_session(self):
        """初始化HTTP会话"""
        if not self.client:
            # httpx会自动读取环境变量的代理配置(HTTP_PROXY/HTTPS_PROXY)
            self.client = httpx.AsyncClient(timeout=self.timeout)
            logger.info("✅ Jupiter API 会话已初始化")
    
    async def close_session(self):
        """关闭HTTP会话"""
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("🔒 Jupiter API 会话已关闭")
    
    async def get_token_data(self, ca: str) -> Optional[Dict[str, Any]]:
        """
        获取单个Token的数据
        
        Args:
            ca: Token合约地址
            
        Returns:
            Token数据（包含stats5m），失败返回None
        """
        if not self.client:
            await self.init_session()
        
        url = f"{self.base_url}/tokens/v2/search"
        params = {"query": ca}
        
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(url, params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # API返回的是数组，取第一个元素
                    if data and len(data) > 0:
                        token_data = data[0]
                        
                        # 优先使用stats5m，如果没有则fallback到stats1h
                        if "stats5m" in token_data:
                            logger.debug(f"✅ 获取Token数据成功: {ca[:8]}... (使用stats5m)")
                            return token_data
                        elif "stats1h" in token_data:
                            logger.warning(f"⚠️ Token {ca[:8]}... 无stats5m数据，使用stats1h作为替代")
                            # 将stats1h映射到stats5m字段，保持兼容性
                            token_data['stats5m'] = token_data['stats1h']
                            return token_data
                        else:
                            logger.warning(f"⚠️ Token {ca[:8]}... 无可用统计数据")
                            return None
                    else:
                        logger.warning(f"⚠️ Token {ca[:8]}... 未找到数据")
                        return None
                else:
                    logger.warning(f"⚠️ Jupiter API 返回错误: {response.status_code}")
                    
            except httpx.TimeoutException:
                logger.warning(f"⏱️ 请求超时 (尝试 {attempt + 1}/{self.max_retries}): {ca[:8]}...")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))  # 递增延迟
                    
            except Exception as e:
                logger.error(f"❌ 请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
        
        logger.error(f"❌ 获取Token数据最终失败: {ca[:8]}...")
        return None
    
    async def get_multiple_tokens(
        self, 
        ca_list: list, 
        delay: float = 0.1
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        批量获取多个Token的数据
        
        Args:
            ca_list: Token合约地址列表
            delay: 每次请求间隔（秒），防止API限流
            
        Returns:
            {ca: token_data} 字典
        """
        results = {}
        total = len(ca_list)
        
        logger.info(f"📊 开始批量获取 {total} 个Token数据...")
        
        for i, ca in enumerate(ca_list, 1):
            token_data = await self.get_token_data(ca)
            results[ca] = token_data
            
            # 进度日志（每10个或最后一个）
            if i % 10 == 0 or i == total:
                success_count = sum(1 for v in results.values() if v is not None)
                logger.info(f"进度: {i}/{total} | 成功: {success_count}")
            
            # 请求间隔（最后一个不需要等待）
            if i < total:
                await asyncio.sleep(delay)
        
        success_count = sum(1 for v in results.values() if v is not None)
        logger.info(f"✅ 批量获取完成: {success_count}/{total} 成功")
        
        return results
    
    async def __aenter__(self):
        """支持 async with 语法"""
        await self.init_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时自动关闭会话"""
        await self.close_session()

