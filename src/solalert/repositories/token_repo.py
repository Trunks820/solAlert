"""
Token 数据仓库
处理 token_launch_history 表的数据访问
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..core.database import get_db

logger = logging.getLogger(__name__)


class TokenRepository:
    """Token数据仓库"""
    
    def __init__(self):
        self.db = get_db()
    
    def insert_pump_token(
        self,
        ca: str,
        token_name: Optional[str],
        token_symbol: Optional[str],
        twitter_url: Optional[str],
        launch_time: datetime,
        tg_msg_id: str
    ) -> bool:
        """
        插入Pump token数据
        
        Args:
            ca: 合约地址
            token_name: Token名称
            token_symbol: Token符号
            twitter_url: Twitter链接
            launch_time: 发射时间
            tg_msg_id: TG消息ID
            
        Returns:
            是否成功
        """
        try:
            sql = """
            INSERT INTO token_launch_history 
            (ca, token_name, token_symbol, twitter_url, source, launch_time, tg_msg_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                token_name = VALUES(token_name),
                token_symbol = VALUES(token_symbol),
                twitter_url = CASE 
                    WHEN VALUES(twitter_url) IS NOT NULL AND VALUES(twitter_url) != '' 
                    THEN VALUES(twitter_url) 
                    ELSE twitter_url 
                END,
                tg_msg_id = VALUES(tg_msg_id)
            """
            
            params = (
                ca,
                token_name,
                token_symbol,
                twitter_url,
                'pump',
                launch_time,
                tg_msg_id,
                datetime.now()
            )
            
            rowcount = self.db.execute_update(sql, params)
            
            # 输出日志
            twitter_info = f" [Twitter: {twitter_url}]" if twitter_url else " [Twitter: None]"
            
            if rowcount > 0:
                if rowcount == 1:
                    logger.info(f"✅ 新pump数据已入库: {token_name} ({ca[:8]}...){twitter_info}")
                elif rowcount == 2:
                    logger.info(f"🔄 已更新现有记录: {token_name} ({ca[:8]}...){twitter_info}")
                return True
            else:
                logger.debug(f"⚠️  数据无变化: {ca[:8]}...{twitter_info}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 插入pump数据失败: {e}")
            return False
    
    def get_token_by_ca(self, ca: str) -> Optional[Dict[str, Any]]:
        """
        根据CA地址获取Token信息
        
        Args:
            ca: 合约地址
            
        Returns:
            Token信息字典或None
        """
        try:
            sql = """
            SELECT * FROM token_launch_history 
            WHERE ca = %s
            LIMIT 1
            """
            return self.db.execute_query(sql, (ca,), fetch_one=True)
        except Exception as e:
            logger.error(f"查询Token失败: {e}")
            return None
    
    def get_recent_tokens(
        self,
        source: Optional[str] = None,
        hours: int = 24,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取最近的Token列表
        
        Args:
            source: 数据来源 (pump/bonk)
            hours: 时间范围（小时）
            limit: 返回数量限制
            
        Returns:
            Token列表
        """
        try:
            if source:
                sql = """
                SELECT * FROM token_launch_history 
                WHERE source = %s 
                AND launch_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                ORDER BY launch_time DESC
                LIMIT %s
                """
                params = (source, hours, limit)
            else:
                sql = """
                SELECT * FROM token_launch_history 
                WHERE launch_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                ORDER BY launch_time DESC
                LIMIT %s
                """
                params = (hours, limit)
            
            return self.db.execute_query(sql, params)
        except Exception as e:
            logger.error(f"查询最近Token失败: {e}")
            return []
    
    def update_market_cap(self, ca: str, market_cap: int) -> bool:
        """
        更新Token最高市值
        
        Args:
            ca: 合约地址
            market_cap: 市值
            
        Returns:
            是否成功
        """
        try:
            sql = """
            UPDATE token_launch_history 
            SET highest_market_cap = GREATEST(COALESCE(highest_market_cap, 0), %s)
            WHERE ca = %s
            """
            rowcount = self.db.execute_update(sql, (market_cap, ca))
            return rowcount > 0
        except Exception as e:
            logger.error(f"更新市值失败: {e}")
            return False

