"""V2 数据加载器 - 从新表结构加载任务和批次数据"""

import json
import logging
from typing import List, Dict, Tuple
from solalert.core.database import DatabaseManager

logger = logging.getLogger(__name__)


def load_all_active_batches_v2(chain_type: str = 'sol') -> List[Dict]:
    """
    加载所有活跃任务的批次信息
    
    Args:
        chain_type: 链类型 ('sol', 'bsc', 'eth')
    
    Returns:
        [
            {
                "task_id": 1,
                "task_name": "SOL智能监控",
                "batch_id": 123,       # monitor_batch_v2.id（全局唯一）⭐
                "batch_no": 1,         # 任务内批次号（显示用）
                "epoch": 1,
                "item_count": 99
            },
            ...
        ]
    """
    db = DatabaseManager()
    
    try:
        # 查询所有活跃批次
        # 注意：monitor_task_v2.status 可能是 varchar('enabled') 或 tinyint(1)
        # 如果 current_epoch = 0，说明还没开始，查询所有 epoch 的批次
        query = """
            SELECT 
                b.id AS batch_id,          -- 全局唯一ID，Python端使用 ⭐
                b.task_id,
                b.batch_no,                -- 任务内批次号，显示用
                b.epoch,
                b.item_count,
                b.status AS batch_status,
                t.task_name,
                t.task_type,
                t.current_epoch
            FROM monitor_batch_v2 b
            INNER JOIN monitor_task_v2 t ON b.task_id = t.id
            WHERE (t.status = 1 OR t.status = 'enabled')
              AND t.chain_type = %s
              AND (
                  b.epoch = t.current_epoch 
                  OR (t.current_epoch = 0 AND b.epoch = (
                      SELECT MAX(epoch) FROM monitor_batch_v2 
                      WHERE task_id = t.id
                  ))
              )
            ORDER BY t.id, b.batch_no
        """
        
        results = db.execute_query(query, (chain_type,))
        
        batches = []
        for row in results:
            batches.append({
                'batch_id': row['batch_id'],        # monitor_batch_v2.id（全局唯一）⭐
                'task_id': row['task_id'],
                'task_name': row['task_name'],
                'task_type': row['task_type'],
                'batch_no': row['batch_no'],        # 任务内批次号（显示用）
                'epoch': row['epoch'],
                'item_count': row['item_count'],
                'batch_status': row['batch_status']
            })
        
        logger.info(f"✅ 加载 {len(batches)} 个活跃批次 (chain_type={chain_type})")
        
        return batches
    
    except Exception as e:
        logger.error(f"❌ 加载批次失败: {e}", exc_info=True)
        return []


def load_batch_data_v2(task_id: int, batch_id: int) -> Tuple[List[Dict], Dict]:
    """
    加载单个批次的数据（兼容旧接口）
    
    Args:
        task_id: 任务ID
        batch_id: 批次ID（monitor_batch_v2.id，全局唯一）⭐
    
    Returns:
        (pairs列表, pair_to_full_config映射)
        
        pairs: [{"pair": "0x123...", "token": "0xabc..."}, ...]
        pair_to_full_config: {
            "0x123...": {
                "ca": "0xabc...",
                "token_symbol": "SOL",
                "token_name": "Solana",
                "pair_address": "0x123...",
                "config_id": 1,
                "config_name": "智能模板",
                "time_interval": "1m",
                "events_config": {...},
                "trigger_logic": "OR",
                ...
            }
        }
    """
    db = DatabaseManager()
    
    try:
        # 1. 加载配置（一个任务只有一个配置）
        config_query = """
            SELECT c.*
            FROM monitor_config_v2 c
            INNER JOIN monitor_task_config_v2 tc ON c.id = tc.config_id
            WHERE tc.task_id = %s 
              AND c.status = 1
              AND c.del_flag = 0
            ORDER BY tc.config_order
            LIMIT 1
        """
        
        config_results = db.execute_query(config_query, (task_id,))
        
        if not config_results:
            logger.error(f"❌ 任务 {task_id} 无配置")
            return [], {}
        
        config = config_results[0]
        
        # 解析 events_config JSON
        if config.get('events_config'):
            try:
                if isinstance(config['events_config'], str):
                    config['events_config'] = json.loads(config['events_config'])
            except Exception as e:
                logger.error(f"❌ 解析 events_config 失败: {e}")
                config['events_config'] = {}
        else:
            config['events_config'] = {}
        
        # 2. 加载该批次的目标（通过 batch_id 关联 monitor_batch_item_v2）
        # 注意：这里需要通过 monitor_batch_v2.id 反查 task_id 和 batch_no，
        # 然后再从 monitor_task_target_v2 中查询对应的目标
        
        # 先查询批次信息
        batch_query = """
            SELECT task_id, batch_no
            FROM monitor_batch_v2
            WHERE id = %s
        """
        batch_info = db.execute_query(batch_query, (batch_id,))
        
        if not batch_info:
            logger.error(f"❌ 批次 {batch_id} 不存在")
            return [], {}
        
        batch_no = batch_info[0]['batch_no']
        
        # 🔍 问题：monitor_task_target_v2.batch_id 可能是 NULL（未分配）
        # 解决方案：通过 monitor_batch_item_v2 表获取批次项
        # 如果没有 monitor_batch_item_v2 表，则需要使用一致性哈希自己分配
        
        # 先尝试查询 monitor_batch_item_v2（如果存在）
        try:
            items_query = """
                SELECT
                    t.target_value as ca,
                    t.token_symbol,
                    t.token_name,
                    t.pair_address,
                    t.market_cap,
                    t.source,
                    bi.item_order as batch_order
                FROM monitor_batch_item_v2 bi
                INNER JOIN monitor_task_target_v2 t ON bi.target_id = t.id
                WHERE bi.batch_id = %s
                ORDER BY bi.item_order
            """
            targets = db.execute_query(items_query, (batch_id,))
            
            if not targets:
                raise Exception("No items in monitor_batch_item_v2")
                
        except Exception as e:
            # 如果 monitor_batch_item_v2 不存在或无数据，回退到简单查询
            logger.warning(f"monitor_batch_item_v2 查询失败，回退到简单查询: {e}")
            
            # 查询该任务的所有目标，使用一致性哈希分配
            targets_query = """
                SELECT
                    target_value as ca,
                    token_symbol,
                    token_name,
                    pair_address,
                    market_cap,
                    source
                FROM monitor_task_target_v2
                WHERE task_id = %s 
                  AND status = 1
                ORDER BY id
            """
            
            all_targets = db.execute_query(targets_query, (task_id,))
            
            # 简单分配：按 batch_no 取模
            targets = []
            for idx, target in enumerate(all_targets):
                # 计算该目标应该属于哪个批次
                target_batch_no = (idx % 99) + 1  # 假设每批99个
                if target_batch_no == batch_no:
                    targets.append(target)
        
        if not targets:
            logger.warning(f"⚠️ 批次 {batch_id} (task_id={task_id}, batch_no={batch_no}) 无目标")
            return [], {}
        
        # 3. 转换为旧格式（兼容现有代码）
        pairs = []
        pair_to_full_config = {}
        
        for target in targets:
            ca = target['ca']
            pair_address = target.get('pair_address')
            
            # 如果 pair_address 为空，需要通过 DBotX API 获取
            # 这里先用 ca 作为 pair，实际监控时会通过 API 获取真实的 pair_address
            if not pair_address:
                logger.debug(f"目标 {ca} 的 pair_address 为空，需要通过 API 获取")
                pair_address = ca  # 临时使用 ca，后续通过 API 更新
            
            # DBotX API 格式
            pairs.append({
                "pair": pair_address,
                "token": ca
            })
            
            # 合并配置和目标信息
            full_config = {
                # 从配置表来的字段
                'config_id': config['id'],
                'config_name': config['config_name'],
                'config_category': config.get('config_category'),
                'source': config.get('source') or target.get('source'),
                'market_type': config.get('market_type'),
                'time_interval': config['time_interval'],
                'events_config': config['events_config'],
                'trigger_logic': config['trigger_logic'],
                'min_transaction_usd': config.get('min_transaction_usd'),
                'cumulative_min_amount_usd': config.get('cumulative_min_amount_usd'),
                'top_holders_threshold': config.get('top_holders_threshold'),
                'notify_methods': config.get('notify_methods'),
                'version': config.get('version'),
                
                # 从目标表来的字段
                'ca': ca,
                'token_symbol': target.get('token_symbol'),
                'token_name': target.get('token_name'),
                'pair_address': pair_address,
                'market_cap': target.get('market_cap'),
                
                # 兼容性字段（旧代码使用）
                'template_id': config['id'],
                'template_name': config['config_name'],
            }
            
            pair_to_full_config[pair_address] = full_config
        
        logger.info(f"✅ 批次 {batch_id} 加载 {len(pairs)} 个目标 (task_id={task_id}, batch_no={batch_no})")
        
        return pairs, pair_to_full_config
    
    except Exception as e:
        logger.error(f"❌ 加载批次数据失败: task_id={task_id}, batch_id={batch_id}, 错误: {e}", exc_info=True)
        return [], {}

