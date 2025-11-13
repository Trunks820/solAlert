"""Pair Address 更新器 - 通过 DBotX API 获取并保存 pair_address"""

import logging
import asyncio
from typing import Dict
from solalert.core.database import DatabaseManager
from solalert.api.dbotx_api import DBotXAPI

logger = logging.getLogger(__name__)


class PairAddressUpdater:
    """Pair Address 更新器"""
    
    def __init__(self):
        self.db = DatabaseManager()
        self.api = DBotXAPI()
    
    async def update_missing_pairs(self, chain_type: str = 'sol', batch_size: int = 100, max_batches: int = None) -> int:
        """
        更新所有缺失的 pair_address（异步，并发模式）
        
        Args:
            chain_type: 链类型
            batch_size: 每批处理数量
            max_batches: 最多处理几批（None=不限制，处理全部）
        
        Returns:
            更新数量
        """
        total_updated = 0
        batch_count = 0
        
        while True:
            # 检查是否达到最大批次限制
            if max_batches is not None and batch_count >= max_batches:
                logger.info(f"⏸️ 已达到最大批次限制 ({max_batches})，停止补齐")
                break
            
            batch_count += 1
            # 查询 pair_address 为空的目标（批量处理）
            query = """
                SELECT id, ca, token_symbol
                FROM monitor_task_target_v2
                WHERE chain_type = %s
                  AND (pair_address IS NULL OR pair_address = '')
                  AND status = 1
                LIMIT %s
            """
            
            targets = self.db.execute_query(query, (chain_type, batch_size))
            
            if not targets:
                if total_updated == 0:
                    logger.info("✅ 所有目标的 pair_address 都已存在")
                else:
                    logger.info(f"✅ 本次共补齐 {total_updated} 个 pair_address")
                return total_updated
            
            logger.info(f"🔍 找到 {len(targets)} 个缺失 pair_address 的目标，开始并发获取...")
            
            # 🚀 并发获取所有 pair_address（API支持6000/分钟，足够快）
            tasks = [self._fetch_and_update_one(target) for target in targets]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 统计成功数量
            updated_count = sum(1 for r in results if r is True)
            
            total_updated += updated_count
            logger.info(f"📊 本批更新 {updated_count}/{len(targets)} 个，累计 {total_updated} 个")
            
            # 如果本批没有更新任何记录，说明剩余的都无法获取，退出循环
            if updated_count == 0:
                logger.warning("⚠️ 剩余目标无法获取 pair_address，停止处理")
                break
            
            # 如果本批处理的数量小于 batch_size，说明已经处理完了
            if len(targets) < batch_size:
                break
    
    async def _fetch_pair_address_async(self, ca: str) -> str:
        """
        通过 DBotX API 获取 pair_address（异步）
        
        Args:
            ca: Token 地址
        
        Returns:
            pair_address 或 None
        """
        try:
            # 调用 DBotX API 的 search_pairs 方法
            result = await self.api.search_pairs(ca)
            
            if result and result.get('pair_address'):
                return result['pair_address']
            else:
                return None
        
        except Exception as e:
            logger.debug(f"获取 pair_address 失败: {ca[:10]}... - {e}")
            return None
    
    async def _fetch_and_update_one(self, target: dict) -> bool:
        """
        获取并更新单个目标的 pair_address（用于并发）
        
        Args:
            target: 目标字典 {id, ca, token_symbol}
        
        Returns:
            是否成功更新
        """
        target_id = target['id']
        ca = target['ca']
        token_symbol = target.get('token_symbol', 'Unknown')
        
        try:
            # 通过 DBotX API 获取 pair_address（异步）
            pair_address = await self._fetch_pair_address_async(ca)
            
            if pair_address:
                # 更新数据库
                update_query = """
                    UPDATE monitor_task_target_v2
                    SET pair_address = %s, update_time = NOW()
                    WHERE id = %s
                """
                self.db.execute_update(update_query, (pair_address, target_id))
                logger.debug(f"✅ 已更新: {token_symbol} ({ca[:10]}...) -> {pair_address[:10]}...")
                return True
            else:
                logger.debug(f"⚠️ 无法获取 pair_address: {token_symbol} ({ca[:10]}...)")
                return False
        
        except Exception as e:
            logger.debug(f"❌ 获取失败: {ca[:10]}... - {e}")
            return False


async def update_all_missing_pairs():
    """更新所有缺失的 pair_address（可作为独立脚本运行）"""
    updater = PairAddressUpdater()
    updated = await updater.update_missing_pairs('sol')
    await updater.api.close()
    print(f"✅ 完成！共更新 {updated} 个 pair_address")


if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
    
    asyncio.run(update_all_missing_pairs())

