"""
测试Twitter推送同步脚本
"""
import sys
from pathlib import Path

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from solalert.core.database import get_db
from solalert.core.logger import setup_logger

logger = setup_logger()


# API类型与数据库字段映射
PUSH_TYPE_MAPPING = {
    'follow': {'field': 'enable_follow_push', 'name': '新关注推送', 'emoji': '👥'},
    'tweet': {'field': 'enable_tweet_push', 'name': '推文推送', 'emoji': '💬'},
    'retweet': {'field': 'enable_retweet_push', 'name': '转发推送', 'emoji': '🔄'},
    'reply': {'field': 'enable_reply_push', 'name': '回复推送', 'emoji': '💭'},
    'avatar': {'field': 'enable_avatar_push', 'name': '头像推送', 'emoji': '🖼️'}
}


def get_enabled_push_types(account: dict) -> list:
    """
    获取账号启用的推送类型列表
    
    Args:
        account: 账号信息字典
        
    Returns:
        启用的推送类型列表
    """
    enabled = []
    for push_type, info in PUSH_TYPE_MAPPING.items():
        if account.get(info['field'], 0) == 1:
            enabled.append({
                'type': push_type,
                'name': info['name'],
                'emoji': info['emoji']
            })
    return enabled


def test_query_accounts():
    """测试查询所有账号"""
    db = get_db()
    
    logger.info("=" * 80)
    logger.info("测试1: 查询所有Twitter账号")
    logger.info("=" * 80)
    
    sql = """
    SELECT id, twitter_url, twitter_user_id, 
           enable_follow_push, enable_tweet_push, 
           enable_retweet_push, enable_reply_push, 
           enable_avatar_push, retry_count, sync_status
    FROM twitter_account_manage
    WHERE twitter_user_id IS NOT NULL 
      AND twitter_user_id != ''
      AND del_flag = '0'
    ORDER BY create_time DESC
    LIMIT 10
    """
    
    result = db.execute_query(sql)
    
    if result:
        logger.info(f"\n找到 {len(result)} 条记录:\n")
        for i, account in enumerate(result, 1):
            logger.info(f"{i}. 账号ID={account['id']}")
            logger.info(f"   Twitter URL: {account.get('twitter_url', '')}")
            logger.info(f"   User ID: {account.get('twitter_user_id', '')}")
            logger.info(f"   同步状态: {'✅ 已同步' if account.get('sync_status') == 1 else '⏳ 待同步'}")
            logger.info(f"   重试次数: {account.get('retry_count', 0)}")
            
            # 自动识别启用的推送类型
            enabled_types = get_enabled_push_types(account)
            if enabled_types:
                logger.info(f"   启用的推送:")
                for push in enabled_types:
                    logger.info(f"     {push['emoji']} {push['name']}")
            else:
                logger.info(f"   启用的推送: 无")
            
            logger.info("")
    else:
        logger.warning("⚠️ 没有找到记录")


def test_pending_accounts():
    """测试查询待同步账号（状态发生变化的账号）"""
    db = get_db()
    
    logger.info("=" * 80)
    logger.info("测试2: 查询待同步账号 (推送状态发生变化的账号)")
    logger.info("=" * 80)
    
    sql = """
    SELECT DISTINCT a.id, a.twitter_url, a.twitter_user_id, 
           a.enable_follow_push, a.enable_tweet_push, 
           a.enable_retweet_push, a.enable_reply_push, 
           a.enable_avatar_push, a.retry_count, a.create_time
    FROM twitter_account_manage a
    WHERE a.twitter_user_id IS NOT NULL 
      AND a.twitter_user_id != ''
      AND a.retry_count < 3
      AND a.del_flag = '0'
      AND (
          -- 至少有一个推送状态发生了变化
          a.enable_follow_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'follow'), -1
          )
          OR a.enable_tweet_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'tweet'), -1
          )
          OR a.enable_retweet_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'retweet'), -1
          )
          OR a.enable_reply_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'reply'), -1
          )
          OR a.enable_avatar_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'avatar'), -1
          )
      )
    ORDER BY a.create_time ASC
    LIMIT 10
    """
    
    result = db.execute_query(sql)
    
    if result:
        logger.info(f"\n✅ 找到 {len(result)} 条待同步记录:\n")
        
        for i, account in enumerate(result, 1):
            logger.info(f"{'='*70}")
            logger.info(f"[{i}/{len(result)}] 账号ID: {account['id']}")
            logger.info(f"{'='*70}")
            logger.info(f"Twitter URL: {account.get('twitter_url', 'N/A')}")
            logger.info(f"User ID: {account.get('twitter_user_id', 'N/A')}")
            logger.info(f"重试次数: {account.get('retry_count', 0)}/3")
            
            # 获取启用的推送类型
            enabled_types = get_enabled_push_types(account)
            
            logger.info(f"\n需要调用的API ({len(enabled_types)}个):")
            logger.info(f"{'-'*70}")
            
            for j, push in enumerate(enabled_types, 1):
                api_endpoint = f"/api/v1/user/{push['type']}/push"
                logger.info(f"  {j}. {push['emoji']} {push['name']}")
                logger.info(f"     API: PUT https://alpha.apidance.pro{api_endpoint}")
                logger.info(f"     参数: {{'user_id': '{account.get('twitter_user_id', '')}', 'push_status': true}}")
                logger.info("")
            
            logger.info("")
    else:
        logger.info("✅ 没有待同步的账号")


def test_simulate_sync():
    """模拟同步流程（不实际调用API）"""
    db = get_db()
    
    logger.info("=" * 80)
    logger.info("测试3: 模拟同步流程")
    logger.info("=" * 80)
    
    sql = """
    SELECT DISTINCT a.id, a.twitter_url, a.twitter_user_id, 
           a.enable_follow_push, a.enable_tweet_push, 
           a.enable_retweet_push, a.enable_reply_push, 
           a.enable_avatar_push, a.retry_count, a.create_time
    FROM twitter_account_manage a
    WHERE a.twitter_user_id IS NOT NULL 
      AND a.twitter_user_id != ''
      AND a.retry_count < 3
      AND a.del_flag = '0'
      AND (
          a.enable_follow_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'follow'), -1
          )
          OR a.enable_tweet_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'tweet'), -1
          )
          OR a.enable_retweet_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'retweet'), -1
          )
          OR a.enable_reply_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'reply'), -1
          )
          OR a.enable_avatar_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'avatar'), -1
          )
      )
    ORDER BY a.create_time ASC
    LIMIT 5
    """
    
    result = db.execute_query(sql)
    
    if not result:
        logger.info("\n✅ 没有待同步的账号，模拟流程结束")
        return
    
    logger.info(f"\n📋 将模拟同步 {len(result)} 个账号的推送配置\n")
    
    for i, account in enumerate(result, 1):
        logger.info(f"\n{'='*70}")
        logger.info(f"🔄 [{i}/{len(result)}] 开始同步账号 ID={account['id']}")
        logger.info(f"{'='*70}")
        
        user_id = account.get('twitter_user_id', '')
        enabled_types = get_enabled_push_types(account)
        
        logger.info(f"User ID: {user_id}")
        
        # 查询上次同步状态
        sql_status = """
        SELECT push_type, current_status 
        FROM twitter_push_sync_status 
        WHERE account_id = %s
        """
        synced = db.execute_query(sql_status, (account['id'],))
        last_status = {}
        if synced:
            for row in synced:
                last_status[row['push_type']] = row['current_status']
        
        # 检查需要同步的推送（状态变化的）
        changes = []
        for push_type, info in PUSH_TYPE_MAPPING.items():
            current = 1 if account.get(info['field'], 0) else 0
            last = last_status.get(push_type)
            
            if current != last:
                if last is None:
                    change_info = f"首次配置 -> {'开启' if current else '关闭'}"
                else:
                    change_info = f"{'开启' if last else '关闭'} -> {'开启' if current else '关闭'}"
                changes.append({
                    'type': push_type,
                    'name': info['name'],
                    'current': current,
                    'change': change_info
                })
        
        logger.info(f"需要同步的推送: {len(changes)}个\n")
        
        # 模拟API调用
        for j, change in enumerate(changes, 1):
            logger.info(f"  [{j}/{len(changes)}] {change['name']}")
            logger.info(f"      状态变化: {change['change']}")
            logger.info(f"      API: PUT /api/v1/user/{change['type']}/push")
            logger.info(f"      Payload: {{'user_id': '{user_id}', 'push_status': {str(bool(change['current'])).lower()}}}")
            logger.info(f"      ✅ 模拟调用成功")
            logger.info("")
        
        logger.info(f"✅ 账号 {account['id']} 同步完成 ({len(changes)}个API调用成功)")
        logger.info(f"💾 将更新 twitter_push_sync_status 表")


def test_statistics():
    """测试统计信息"""
    db = get_db()
    
    logger.info("\n" + "=" * 80)
    logger.info("测试4: 统计信息")
    logger.info("=" * 80)
    
    # 总账号数
    sql1 = "SELECT COUNT(*) as count FROM twitter_account_manage WHERE del_flag = '0'"
    result1 = db.execute_query(sql1, fetch_one=True)
    total_count = result1['count'] if result1 else 0
    
    # 有user_id的账号数
    sql2 = """
    SELECT COUNT(*) as count FROM twitter_account_manage 
    WHERE twitter_user_id IS NOT NULL AND twitter_user_id != '' AND del_flag = '0'
    """
    result2 = db.execute_query(sql2, fetch_one=True)
    has_user_id = result2['count'] if result2 else 0
    
    # 待同步账号数（推送状态发生变化的）
    sql3 = """
    SELECT COUNT(DISTINCT a.id) as count
    FROM twitter_account_manage a
    WHERE a.twitter_user_id IS NOT NULL 
      AND a.twitter_user_id != ''
      AND a.retry_count < 3
      AND a.del_flag = '0'
      AND (
          a.enable_follow_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'follow'), -1
          )
          OR a.enable_tweet_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'tweet'), -1
          )
          OR a.enable_retweet_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'retweet'), -1
          )
          OR a.enable_reply_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'reply'), -1
          )
          OR a.enable_avatar_push != IFNULL(
              (SELECT current_status FROM twitter_push_sync_status 
               WHERE account_id = a.id AND push_type = 'avatar'), -1
          )
      )
    """
    result3 = db.execute_query(sql3, fetch_one=True)
    pending_count = result3['count'] if result3 else 0
    
    # 已同步账号数（有同步记录的）
    sql4 = """
    SELECT COUNT(DISTINCT account_id) as count 
    FROM twitter_push_sync_status
    """
    result4 = db.execute_query(sql4, fetch_one=True)
    synced_count = result4['count'] if result4 else 0
    
    # 同步失败账号数（重试次数>=3）
    sql5 = """
    SELECT COUNT(*) as count FROM twitter_account_manage 
    WHERE twitter_user_id IS NOT NULL 
      AND twitter_user_id != ''
      AND retry_count >= 3
      AND del_flag = '0'
    """
    result5 = db.execute_query(sql5, fetch_one=True)
    failed_count = result5['count'] if result5 else 0
    
    # 各类推送启用统计
    sql6 = """
    SELECT 
        SUM(enable_follow_push) as follow_count,
        SUM(enable_tweet_push) as tweet_count,
        SUM(enable_retweet_push) as retweet_count,
        SUM(enable_reply_push) as reply_count,
        SUM(enable_avatar_push) as avatar_count
    FROM twitter_account_manage
    WHERE twitter_user_id IS NOT NULL 
      AND twitter_user_id != ''
      AND del_flag = '0'
    """
    result6 = db.execute_query(sql6, fetch_one=True)
    
    logger.info(f"\n📊 账号统计:")
    logger.info(f"   总账号数: {total_count}")
    logger.info(f"   有user_id: {has_user_id}")
    logger.info(f"   ├─ 待同步: {pending_count} ⏳")
    logger.info(f"   ├─ 已同步: {synced_count} ✅")
    logger.info(f"   └─ 同步失败(重试>=3): {failed_count} ❌")
    
    if result6:
        logger.info(f"\n📈 推送类型统计 (有user_id的账号):")
        logger.info(f"   👥 新关注推送: {result6.get('follow_count', 0)} 个账号启用")
        logger.info(f"   💬 推文推送: {result6.get('tweet_count', 0)} 个账号启用")
        logger.info(f"   🔄 转发推送: {result6.get('retweet_count', 0)} 个账号启用")
        logger.info(f"   💭 回复推送: {result6.get('reply_count', 0)} 个账号启用")
        logger.info(f"   🖼️ 头像推送: {result6.get('avatar_count', 0)} 个账号启用")
    
    logger.info("")


if __name__ == '__main__':
    try:
        # 按顺序执行测试
        test_statistics()
        test_query_accounts()
        test_pending_accounts()
        test_simulate_sync()
        
        logger.info("\n" + "=" * 80)
        logger.info("✅ 所有测试完成")
        logger.info("=" * 80)
        logger.info("\n💡 提示: 如需实际执行同步，请运行:")
        logger.info("   python scripts/twitter_push_sync.py --mode once")
        logger.info("   或")
        logger.info("   python scripts/twitter_push_sync.py --mode schedule --interval 60")
        logger.info("")
        
    except Exception as e:
        logger.error(f"❌ 测试失败: {e}", exc_info=True)
