#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化 SOL WebSocket 批次池
从 quick_monitor_template、twitter_account_manage、token_launch_history 读取数据
调用 DBotX API 获取 pair 地址，按市值排序并分批，写入 sol_ws_batch_pool

使用方法：
  python scripts/initialize_sol_ws_batches.py              # 完整初始化（清空重建）
  python scripts/initialize_sol_ws_batches.py --incremental # 增量模式（只处理新CA）
"""
import sys
import os
import asyncio
import json
import argparse
from typing import List, Dict, Set, Optional
from datetime import datetime
from decimal import Decimal

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.solalert.core.database import DatabaseManager
from src.solalert.api.dbotx_api import DBotXAPI


class SolWsBatchInitializer:
    """SOL WebSocket 批次池初始化器"""
    
    def __init__(self, incremental: bool = False):
        self.db = DatabaseManager()
        self.api = DBotXAPI()
        self.incremental = incremental
        
        # 统计信息
        self.stats = {
            'total_templates': 0,
            'total_profile_urls': 0,
            'total_matched_cas': 0,
            'existing_cas': 0,
            'new_cas_to_process': 0,
            'total_with_pairs': 0,
            'total_batches': 0,
            'failed_pairs': []
        }
    
    def load_templates(self) -> List[Dict]:
        """加载模板配置"""
        print("=" * 80)
        print("步骤 1/6: 加载模板配置")
        print("=" * 80)
        
        sql = """
            SELECT 
                id, config_name, min_market_cap, has_twitter,
                time_interval, top_holders_threshold, events_config, trigger_logic
            FROM quick_monitor_template
            WHERE del_flag = 0
            ORDER BY min_market_cap DESC
        """
        
        templates = self.db.execute_query(sql)
        self.stats['total_templates'] = len(templates)
        
        print(f"✅ 加载了 {len(templates)} 个模板配置：")
        for t in templates:
            print(f"   - ID={t['id']}, 名称={t['config_name']}, "
                  f"最小市值=${t['min_market_cap']:,.0f}, "
                  f"Twitter要求={t['has_twitter']}")
        
        return templates
    
    def get_profile_twitter_urls(self) -> Set[str]:
        """获取所有 profile 类型的 Twitter URLs"""
        print("\n" + "=" * 80)
        print("步骤 2/6: 获取 Profile Twitter URLs")
        print("=" * 80)
        
        sql = """
            SELECT DISTINCT twitter_url
            FROM twitter_account_manage
            WHERE twitter_type = 'profile'
            AND twitter_url IS NOT NULL
            AND twitter_url != ''
        """
        
        rows = self.db.execute_query(sql)
        urls = {row['twitter_url'] for row in rows}
        self.stats['total_profile_urls'] = len(urls)
        
        print(f"✅ 获取了 {len(urls):,} 个 profile 类型的 Twitter URL")
        
        return urls
    
    def get_existing_cas(self) -> Set[str]:
        """获取数据库中已有的CA列表（增量模式使用）"""
        if not self.incremental:
            return set()
        
        sql = """
            SELECT DISTINCT ca
            FROM sol_ws_batch_pool
            WHERE is_active = 1
        """
        
        rows = self.db.execute_query(sql)
        existing_cas = {row['ca'] for row in rows}
        self.stats['existing_cas'] = len(existing_cas)
        
        print(f"\n📦 增量模式: 数据库中已有 {len(existing_cas):,} 个CA")
        
        return existing_cas
    
    def match_cas_with_templates(
        self, 
        templates: List[Dict], 
        profile_urls: Set[str],
        existing_cas: Set[str] = None
    ) -> List[Dict]:
        """
        查询并匹配CA到对应模板
        
        Args:
            templates: 模板配置列表
            profile_urls: profile类型Twitter URL集合
            existing_cas: 已存在的CA集合（增量模式使用）
        
        Returns:
            List of dict with keys: ca, token_symbol, token_name, market_cap, 
                                    twitter_url, template_id, template_name, 
                                    time_interval, events_config, trigger_logic
        """
        print("\n" + "=" * 80)
        print("步骤 3/6: 查询并匹配 CA")
        print("=" * 80)
        
        if existing_cas is None:
            existing_cas = set()
        
        # 按市值区间划分模板（降序：$10M, $1M, $500K, $300K）
        templates_sorted = sorted(templates, key=lambda x: x['min_market_cap'], reverse=True)
        
        all_cas = []
        
        for i, template in enumerate(templates_sorted):
            min_cap = template['min_market_cap']
            
            # 确定市值区间
            # 第一个模板（最高市值）：市值 >= min_cap（无上限）
            # 其他模板：前一个模板的 min_cap <= 市值 < 当前模板的 min_cap
            if i == 0:
                # 第一个模板（如 $10M）：市值 >= $10M
                cap_condition = f"highest_market_cap >= {min_cap}"
            else:
                # 后续模板：上一个模板的min_cap 作为当前的上限
                max_cap = templates_sorted[i - 1]['min_market_cap']
                cap_condition = f"highest_market_cap >= {min_cap} AND highest_market_cap < {max_cap}"
            
            # 查询CA
            sql = f"""
                SELECT 
                    ca,
                    token_symbol,
                    token_name,
                    highest_market_cap as market_cap,
                    twitter_url
                FROM token_launch_history
                WHERE source IN ('pump', 'bonk')
                AND {cap_condition}
                AND twitter_url IS NOT NULL
                AND twitter_url != ''
            """
            
            rows = self.db.execute_query(sql)
            
            # 过滤：只保留 twitter_url 在 profile_urls 中的
            matched_rows = [
                row for row in rows 
                if row['twitter_url'] in profile_urls
            ]
            
            # 增量模式：过滤掉已存在的CA
            if self.incremental and existing_cas:
                new_matched_rows = [
                    row for row in matched_rows
                    if row['ca'] not in existing_cas
                ]
                skipped = len(matched_rows) - len(new_matched_rows)
                matched_rows = new_matched_rows
            else:
                skipped = 0
            
            print(f"\n📊 模板 [{template['config_name']}] (ID={template['id']})")
            if i == 0:
                print(f"   市值区间: ${min_cap:,.0f} 以上")
            else:
                max_cap = templates_sorted[i - 1]['min_market_cap']
                print(f"   市值区间: ${min_cap:,.0f} - ${max_cap:,.0f}")
            print(f"   查询到: {len(rows):,} 个CA")
            if self.incremental and existing_cas:
                print(f"   匹配 profile: {len(matched_rows) + skipped:,} 个CA (跳过已有: {skipped})")
                print(f"   新增处理: {len(matched_rows):,} 个CA")
            else:
                print(f"   匹配 profile: {len(matched_rows):,} 个CA")
            
            # 添加模板信息
            for row in matched_rows:
                all_cas.append({
                    'ca': row['ca'],
                    'token_symbol': row['token_symbol'],
                    'token_name': row['token_name'],
                    'market_cap': float(row['market_cap']) if row['market_cap'] else 0.0,
                    'twitter_url': row['twitter_url'],
                    'template_id': template['id'],
                    'template_name': template['config_name'],
                    'time_interval': template['time_interval'],
                    'events_config': template['events_config'],
                    'trigger_logic': template['trigger_logic']
                })
        
        self.stats['total_matched_cas'] = len(all_cas)
        self.stats['new_cas_to_process'] = len(all_cas)
        
        if self.incremental and existing_cas:
            print(f"\n✅ 增量模式总计:")
            print(f"   已有CA: {len(existing_cas):,}")
            print(f"   新增CA: {len(all_cas):,}")
        else:
            print(f"\n✅ 总计匹配到 {len(all_cas):,} 个CA")
        
        return all_cas
    
    async def fetch_pair_addresses(self, cas: List[Dict]) -> List[Dict]:
        """
        并发获取每个CA的 pair 地址
        使用限流控制避免API限流
        """
        print("\n" + "=" * 80)
        print("步骤 4/6: 获取 Pair 地址")
        print("=" * 80)
        
        print(f"🔍 开始查询 {len(cas):,} 个CA的pair地址...")
        print(f"   策略: 10个/批，串行批次，每批间隔3秒（避免API限流）")
        
        results = []
        batch_size = 50  # 降低到10个/批
        
        for i in range(0, len(cas), batch_size):
            batch = cas[i:i+batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(cas) + batch_size - 1) // batch_size
            
            print(f"\n   批次 {batch_num}/{total_batches}: 处理 {len(batch)} 个CA")
            
            # 并发查询（但批次数量小）
            tasks = [
                self._fetch_single_pair(ca_info)
                for ca_info in batch
            ]
            
            batch_results = await asyncio.gather(*tasks)
            
            # 统计成功/失败
            success_count = sum(1 for r in batch_results if r is not None)
            failed_count = len(batch_results) - success_count
            
            print(f"      成功: {success_count}, 失败: {failed_count}")
            
            # 添加成功的结果
            for result in batch_results:
                if result is not None:
                    results.append(result)
            
            # 批次间延迟（增加到3秒）
            if i + batch_size < len(cas):
                await asyncio.sleep(3.0)
                print(f"      ⏳ 等待3秒后继续...")
        
        self.stats['total_with_pairs'] = len(results)
        
        print(f"\n✅ 成功获取 {len(results):,} 个CA的pair地址")
        if self.stats['failed_pairs']:
            print(f"❌ 失败 {len(self.stats['failed_pairs'])} 个CA")
            print(f"   失败的CA已记录，后续可手动补充")
        
        return results
    
    async def _fetch_single_pair(self, ca_info: Dict) -> Optional[Dict]:
        """获取单个CA的pair地址"""
        try:
            # 调用 DBotX API（只需要传CA地址）
            pair_data = await self.api.search_pairs(ca_info['ca'])
            
            if not pair_data:
                raise ValueError(f"未找到pair数据")
            
            # search_pairs 返回的是单个字典，包含 pair_address 键
            pair_address = pair_data.get('pair_address')
            
            if not pair_address:
                raise ValueError(f"pair地址为空")
            
            # 返回完整信息
            return {
                **ca_info,
                'pair_address': pair_address
            }
        
        except Exception as e:
            # 记录失败
            self.stats['failed_pairs'].append({
                'ca': ca_info['ca'],
                'token_symbol': ca_info.get('token_symbol'),
                'error': str(e)
            })
            return None
    
    def sort_and_batch(self, cas_with_pairs: List[Dict]) -> List[List[Dict]]:
        """
        按市值排序并分批
        每批最多99个CA
        """
        print("\n" + "=" * 80)
        print("步骤 5/6: 排序并分批")
        print("=" * 80)
        
        # 按市值降序排序
        sorted_cas = sorted(
            cas_with_pairs,
            key=lambda x: x['market_cap'],
            reverse=True
        )
        
        # 分批（每批99个）
        batch_size = 99
        batches = []
        
        for i in range(0, len(sorted_cas), batch_size):
            batch = sorted_cas[i:i+batch_size]
            batches.append(batch)
        
        self.stats['total_batches'] = len(batches)
        
        print(f"✅ 共分为 {len(batches)} 个批次：")
        for i, batch in enumerate(batches, 1):
            print(f"   批次 {i}: {len(batch)} 个CA")
        
        return batches
    
    def write_to_database(self, batches: List[List[Dict]]):
        """
        写入批次池表和批次统计表
        """
        print("\n" + "=" * 80)
        print("步骤 6/6: 写入数据库")
        print("=" * 80)
        
        # 1. 清空现有数据（仅在非增量模式）
        if not self.incremental:
            print("🗑️  清空现有批次池数据...")
            self.db.execute_update("DELETE FROM sol_ws_batch_pool")
            self.db.execute_update("DELETE FROM sol_ws_batch_stats")
        else:
            print("📦 增量模式: 保留现有数据，追加新数据...")
        
        # 2. 写入批次池
        print(f"💾 写入 {len(batches)} 个批次到 sol_ws_batch_pool...")
        
        # 查询当前最大的 batch_id 和 priority（增量模式）
        if self.incremental:
            max_info = self.db.execute_query("""
                SELECT 
                    COALESCE(MAX(batch_id), 0) as max_batch_id,
                    COALESCE(MAX(priority), 0) as max_priority
                FROM sol_ws_batch_pool
            """, fetch_one=True)
            
            start_batch_id = max_info['max_batch_id'] + 1
            start_priority = max_info['max_priority'] + 1
            print(f"   从批次 {start_batch_id} 开始，优先级从 {start_priority} 开始")
        else:
            start_batch_id = 1
            start_priority = len(batches) * 99  # 从最大值开始递减
        
        insert_sql = """
            INSERT INTO sol_ws_batch_pool (
                batch_id, ca, token_symbol, token_name, pair_address,
                market_cap, twitter_url, template_id, template_name,
                time_interval, events_config, trigger_logic,
                priority, sort_order, is_active
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """
        
        total_inserted = 0
        global_priority = start_priority
        
        for i, batch in enumerate(batches):
            batch_id = start_batch_id + i
            print(f"\n   批次 {batch_id}: {len(batch)} 个CA")
            
            for sort_order, ca_info in enumerate(batch):
                self.db.execute_update(insert_sql, (
                    batch_id,
                    ca_info['ca'],
                    ca_info['token_symbol'],
                    ca_info['token_name'],
                    ca_info['pair_address'],
                    ca_info['market_cap'],
                    ca_info['twitter_url'],
                    ca_info['template_id'],
                    ca_info['template_name'],
                    ca_info['time_interval'],
                    ca_info['events_config'],
                    ca_info['trigger_logic'],
                    global_priority,
                    sort_order,
                    1  # is_active
                ))
                
                total_inserted += 1
                # 增量模式：priority递增；非增量模式：priority递减
                if self.incremental:
                    global_priority += 1
                else:
                    global_priority -= 1
            
            print(f"      ✓ 已插入 {len(batch)} 条记录")
        
        # 3. 初始化批次统计表
        print(f"\n📊 初始化批次统计表...")
        
        if self.incremental:
            # 增量模式：插入新批次或更新已有批次
            stats_sql = """
                INSERT INTO sol_ws_batch_stats (
                    batch_id, ca_count, subscribed_count, alert_count, 
                    total_messages, error_count
                ) VALUES (
                    %s, %s, 0, 0, 0, 0
                )
                ON DUPLICATE KEY UPDATE
                    ca_count = ca_count + VALUES(ca_count)
            """
        else:
            # 非增量模式：直接插入
            stats_sql = """
                INSERT INTO sol_ws_batch_stats (
                    batch_id, ca_count, subscribed_count, alert_count, 
                    total_messages, error_count
                ) VALUES (
                    %s, %s, 0, 0, 0, 0
                )
            """
        
        for i, batch in enumerate(batches):
            batch_id = start_batch_id + i
            self.db.execute_update(stats_sql, (batch_id, len(batch)))
        
        print(f"   ✓ 已初始化 {len(batches)} 个批次统计")
        
        print(f"\n✅ 数据写入完成！")
        print(f"   总计插入: {total_inserted} 条记录")
    
    def save_failed_pairs(self):
        """保存失败的CA列表到文件"""
        if not self.stats['failed_pairs']:
            return
        
        failed_file = os.path.join(project_root, 'logs', 'failed_pairs.json')
        
        try:
            # 确保logs目录存在
            os.makedirs(os.path.dirname(failed_file), exist_ok=True)
            
            # 保存失败信息
            with open(failed_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'total_failed': len(self.stats['failed_pairs']),
                    'failed_pairs': self.stats['failed_pairs']
                }, f, indent=2, ensure_ascii=False)
            
            print(f"\n💾 失败的CA列表已保存到: {failed_file}")
            print(f"   可使用补充脚本重试: python scripts/retry_failed_pairs.py")
        
        except Exception as e:
            print(f"\n⚠️  保存失败CA列表时出错: {e}")
    
    def print_summary(self):
        """打印总结报告"""
        print("\n" + "=" * 80)
        print("初始化完成 - 总结报告")
        print("=" * 80)
        
        print(f"\n📊 数据统计:")
        print(f"   模板配置数量:      {self.stats['total_templates']}")
        print(f"   Profile URLs:      {self.stats['total_profile_urls']:,}")
        
        if self.incremental:
            print(f"   已有CA数量:        {self.stats['existing_cas']:,}")
            print(f"   新增CA数量:        {self.stats['new_cas_to_process']:,}")
            print(f"   获取到Pair的CA:    {self.stats['total_with_pairs']:,}")
        else:
            print(f"   匹配的CA数量:      {self.stats['total_matched_cas']:,}")
            print(f"   获取到Pair的CA:    {self.stats['total_with_pairs']:,}")
        
        print(f"   失败的CA数量:      {len(self.stats['failed_pairs'])}")
        print(f"   生成的批次数:      {self.stats['total_batches']}")
        
        if self.stats['failed_pairs']:
            print(f"\n❌ 失败的CA列表（前10个）:")
            for fail in self.stats['failed_pairs'][:10]:
                print(f"   - {fail['ca']} ({fail['token_symbol']}): {fail['error']}")
            
            if len(self.stats['failed_pairs']) > 10:
                print(f"   ... 还有 {len(self.stats['failed_pairs']) - 10} 个")
        
        print(f"\n✅ 批次池已就绪！")
        print(f"   可以启动监控程序：python -m src.solalert.monitor.sol_websocket_manager")
    
    async def run(self):
        """运行初始化流程"""
        print("\n")
        print("╔" + "=" * 78 + "╗")
        mode_text = "增量更新" if self.incremental else "完整初始化"
        title = f"SOL WebSocket 批次池初始化工具 ({mode_text})"
        padding = (78 - len(title.encode('gbk'))) // 2
        print("║" + " " * padding + title + " " * (78 - padding - len(title.encode('gbk'))) + "║")
        print("╚" + "=" * 78 + "╝")
        print(f"\n启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"运行模式: {'增量模式（只处理新CA）' if self.incremental else '完整模式（清空重建）'}\n")
        
        try:
            # 1. 加载模板配置
            templates = self.load_templates()
            
            # 2. 获取 profile Twitter URLs
            profile_urls = self.get_profile_twitter_urls()
            
            # 2.5. 获取已有CA列表（增量模式）
            existing_cas = self.get_existing_cas()
            
            # 3. 查询并匹配CA
            all_cas = self.match_cas_with_templates(templates, profile_urls, existing_cas)
            
            if not all_cas:
                print("\n❌ 没有匹配到任何CA，退出初始化")
                return
            
            # 4. 获取 pair 地址
            cas_with_pairs = await self.fetch_pair_addresses(all_cas)
            
            if not cas_with_pairs:
                print("\n❌ 没有获取到任何pair地址，退出初始化")
                return
            
            # 5. 排序并分批
            batches = self.sort_and_batch(cas_with_pairs)
            
            # 6. 写入数据库
            self.write_to_database(batches)
            
            # 7. 保存失败的CA列表
            self.save_failed_pairs()
            
            # 8. 打印总结
            self.print_summary()
        
        except KeyboardInterrupt:
            print("\n\n⚠️  用户中断初始化")
        except Exception as e:
            print(f"\n\n❌ 初始化失败: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='SOL WebSocket 批次池初始化工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用示例:
  完整初始化（清空重建）:
    python scripts/initialize_sol_ws_batches.py
  
  增量模式（只处理新CA）:
    python scripts/initialize_sol_ws_batches.py --incremental
        '''
    )
    
    parser.add_argument(
        '--incremental',
        action='store_true',
        help='增量模式：只处理新CA，不清空现有数据'
    )
    
    args = parser.parse_args()
    
    # 创建初始化器并运行
    initializer = SolWsBatchInitializer(incremental=args.incremental)
    await initializer.run()


if __name__ == "__main__":
    asyncio.run(main())

