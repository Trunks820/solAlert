"""
Twitter推送配置同步任务
定时扫描数据库中需要同步的Twitter账号推送配置，并调用API进行同步
作用：查缺补漏，处理前端保存时API调用失败的情况
"""
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests
import json
import urllib3

from ..core.database import get_db
from ..core.logger import get_logger
from ..core.config import TWITTER_API_CONFIG

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = get_logger(__name__)


class TwitterPushSyncService:
    """Twitter推送同步服务"""
    
    def __init__(self):
        self.db = get_db()
        self.base_url = TWITTER_API_CONFIG['base_url']
        self.authorization = TWITTER_API_CONFIG['authorization']
        self.timeout = TWITTER_API_CONFIG['timeout']
        self.max_retries = TWITTER_API_CONFIG['max_retries']
        self.retry_delay = TWITTER_API_CONFIG['retry_delay']
        self.endpoints = TWITTER_API_CONFIG['endpoints']
        
        # API类型与数据库字段映射
        self.push_type_mapping = {
            'follow': 'enable_follow_push',
            'tweet': 'enable_tweet_push',
            'retweet': 'enable_retweet_push',
            'reply': 'enable_reply_push',
            'avatar': 'enable_avatar_push'
        }
    
    def get_pending_accounts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        查询待同步的Twitter账号（状态发生变化的账号）
        
        Args:
            limit: 每次查询的最大记录数
            
        Returns:
            待同步账号列表
        """
        try:
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
                  -- 条件A: 至少有一个推送当前是开启状态（处理新开启）
                  (
                      a.enable_follow_push = 1 
                      OR a.enable_tweet_push = 1 
                      OR a.enable_retweet_push = 1 
                      OR a.enable_reply_push = 1 
                      OR a.enable_avatar_push = 1
                  )
                  OR
                  -- 条件B: 曾经同步过的账号（可以检测关闭操作）
                  EXISTS (
                      SELECT 1 FROM twitter_push_sync_status 
                      WHERE account_id = a.id
                  )
              )
              AND (
                  -- 且至少有一个推送状态发生了变化
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
            LIMIT %s
            """
            
            result = self.db.execute_query(sql, (limit,))
            return result if result else []
            
        except Exception as e:
            logger.error(f"❌ 查询待同步账号失败: {e}")
            return []
    
    def call_twitter_api(
        self, 
        push_type: str, 
        user_id: str, 
        enable: bool
    ) -> tuple[bool, Optional[str]]:
        """
        调用Twitter API配置推送
        
        Args:
            push_type: 推送类型 (follow/tweet/retweet/reply/avatar)
            user_id: Twitter用户ID
            enable: 是否启用推送
            
        Returns:
            (是否成功, 错误信息)
        """
        if push_type not in self.endpoints:
            return False, f"未知的推送类型: {push_type}"
        
        url = f"{self.base_url}{self.endpoints[push_type]}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'authorization': self.authorization,
            'origin': self.base_url,
            'referer': f'{self.base_url}/'
        }
        
        payload = {
            'user_id': user_id,
            'push_status': enable
        }
        
        try:
            response = requests.put(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=self.timeout,
                verify=False  # 跳过SSL证书验证
            )
            
            # 检查响应状态
            if response.status_code == 200:
                logger.debug(f"✅ API调用成功: {push_type} | user_id={user_id} | enable={enable}")
                return True, None
            else:
                error_msg = f"API返回错误: {response.status_code} - {response.text}"
                logger.warning(f"⚠️ {error_msg}")
                return False, error_msg
                
        except requests.exceptions.Timeout:
            error_msg = "API调用超时"
            logger.warning(f"⚠️ {error_msg}")
            return False, error_msg
        except requests.exceptions.RequestException as e:
            error_msg = f"API调用失败: {e}"
            logger.warning(f"⚠️ {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = f"未知错误: {e}"
            logger.error(f"❌ {error_msg}")
            return False, error_msg
    
    def get_last_synced_status(self, account_id: int) -> Dict[str, Optional[int]]:
        """
        获取账号上次同步的推送状态
        
        Args:
            account_id: 账号ID
            
        Returns:
            各推送类型的上次同步状态 {push_type: status}
        """
        try:
            sql = """
            SELECT push_type, current_status 
            FROM twitter_push_sync_status 
            WHERE account_id = %s
            """
            result = self.db.execute_query(sql, (account_id,))
            
            # 转换为字典
            status_dict = {}
            if result:
                for row in result:
                    status_dict[row['push_type']] = row['current_status']
            
            return status_dict
            
        except Exception as e:
            logger.error(f"❌ 查询上次同步状态失败: {e}")
            return {}
    
    def update_push_sync_status(
        self, 
        account_id: int, 
        push_type: str, 
        status: int,
        api_response: Optional[str] = None
    ) -> bool:
        """
        更新推送同步状态表
        
        Args:
            account_id: 账号ID
            push_type: 推送类型
            status: 状态（0关闭，1开启）
            api_response: API响应信息
            
        Returns:
            是否成功
        """
        try:
            sql = """
            INSERT INTO twitter_push_sync_status 
            (account_id, push_type, current_status, last_sync_time, api_response)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                current_status = VALUES(current_status),
                last_sync_time = VALUES(last_sync_time),
                api_response = VALUES(api_response),
                update_time = NOW()
            """
            params = (account_id, push_type, status, datetime.now(), api_response)
            rowcount = self.db.execute_update(sql, params)
            return rowcount > 0
            
        except Exception as e:
            logger.error(f"❌ 更新同步状态表失败: {e}")
            return False
    
    def sync_account_push_config(self, account: Dict[str, Any]) -> bool:
        """
        同步单个账号的推送配置（只同步状态发生变化的）
        
        Args:
            account: 账号信息
            
        Returns:
            是否全部成功
        """
        account_id = account['id']
        user_id = account['twitter_user_id']
        twitter_url = account.get('twitter_url', '')
        
        logger.info(f"开始同步账号: ID={account_id} | user_id={user_id} | URL={twitter_url}")
        
        # 获取上次同步的状态
        last_status = self.get_last_synced_status(account_id)
        
        # 统计同步结果
        success_count = 0
        fail_count = 0
        total_count = 0
        error_messages = []
        
        # 遍历所有推送类型
        for push_type, db_field in self.push_type_mapping.items():
            # 当前配置的状态
            current_enable = bool(account.get(db_field, 0))
            current_status = 1 if current_enable else 0
            
            # 上次同步的状态（如果没有记录，返回None）
            last_synced = last_status.get(push_type)
            
            # 判断是否需要调用API
            need_sync = False
            
            if last_synced is None:
                # 首次配置：只同步需要开启的（current_status=1）
                if current_status == 1:
                    need_sync = True
                    change_info = f"首次配置 -> 开启"
                # current_status=0时不需要调用API（默认就是关闭）
            else:
                # 已有同步记录：状态发生变化才同步
                if current_status != last_synced:
                    need_sync = True
                    change_info = f"{'开启' if last_synced else '关闭'} -> {'开启' if current_status else '关闭'}"
            
            if need_sync:
                total_count += 1
                logger.info(f"  🔄 {push_type} 状态变化: {change_info}")
                
                # 调用API
                success, error_msg = self.call_twitter_api(push_type, user_id, current_enable)
                
                if success:
                    success_count += 1
                    # 更新同步状态表
                    self.update_push_sync_status(account_id, push_type, current_status, "success")
                    logger.info(f"  ✅ {push_type} 推送配置成功")
                else:
                    fail_count += 1
                    error_messages.append(f"{push_type}: {error_msg}")
                    logger.warning(f"  ❌ {push_type} 推送配置失败: {error_msg}")
                
                # API调用间隔，避免限流
                time.sleep(self.retry_delay)
            else:
                # 不需要同步
                logger.debug(f"  ⏭️  {push_type} 无需同步（当前: {current_status}, 上次: {last_synced}）")
        
        # 判断同步结果
        if total_count == 0:
            # 没有需要同步的推送
            logger.info(f"✅ 账号 {account_id} 所有推送状态未变化，无需同步")
            self.update_sync_retry_count(account_id, reset=True)
            return True
        elif fail_count == 0:
            # 全部成功
            logger.info(f"✅ 账号 {account_id} 推送配置同步成功: {success_count}/{total_count}")
            self.update_sync_retry_count(account_id, reset=True)
            return True
        else:
            # 部分失败或全部失败
            error_msg = "; ".join(error_messages)
            logger.warning(f"⚠️ 账号 {account_id} 推送配置同步失败: {success_count}/{total_count} 成功")
            self.update_sync_retry_count(account_id, reset=False, error_msg=error_msg)
            return False
    
    def update_sync_retry_count(
        self, 
        account_id: int, 
        reset: bool = True,
        error_msg: Optional[str] = None
    ) -> bool:
        """
        更新账号重试次数
        
        Args:
            account_id: 账号ID
            reset: 是否重置重试次数
            error_msg: 错误信息
            
        Returns:
            是否更新成功
        """
        try:
            if reset:
                # 同步成功：重置retry_count
                sql = """
                UPDATE twitter_account_manage 
                SET retry_count = 0,
                    update_time = %s
                WHERE id = %s
                """
                params = (datetime.now(), account_id)
            else:
                # 同步失败：增加retry_count, 记录错误信息
                sql = """
                UPDATE twitter_account_manage 
                SET retry_count = retry_count + 1,
                    update_time = %s,
                    remark = %s
                WHERE id = %s
                """
                remark = f"同步失败: {error_msg}" if error_msg else "同步失败"
                params = (datetime.now(), remark, account_id)
            
            rowcount = self.db.execute_update(sql, params)
            return rowcount > 0
            
        except Exception as e:
            logger.error(f"❌ 更新重试次数失败: {e}")
            return False
    
    def run_sync_once(self) -> Dict[str, int]:
        """
        执行一次同步任务（查缺补漏）
        
        Returns:
            同步统计信息
        """
        logger.info("=" * 80)
        logger.info("🔍 开始执行Twitter推送同步任务（查缺补漏）")
        logger.info("=" * 80)
        
        # 查询待同步账号
        accounts = self.get_pending_accounts(limit=100)
        
        if not accounts:
            logger.info("✅ 没有待同步的账号")
            return {'total': 0, 'success': 0, 'failed': 0}
        
        logger.info(f"📋 发现 {len(accounts)} 个待同步账号")
        
        # 统计结果
        total = len(accounts)
        success = 0
        failed = 0
        
        # 遍历同步
        for i, account in enumerate(accounts, 1):
            logger.info(f"\n[{i}/{total}] 处理账号 ID={account['id']}")
            
            try:
                result = self.sync_account_push_config(account)
                if result:
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"❌ 同步账号 {account['id']} 时发生异常: {e}", exc_info=True)
                failed += 1
        
        # 输出统计
        logger.info("\n" + "=" * 80)
        logger.info("📊 同步任务完成")
        logger.info(f"   总计: {total} | 成功: {success} | 失败: {failed}")
        logger.info("=" * 80)
        
        return {
            'total': total,
            'success': success,
            'failed': failed
        }
    
    def run_schedule(self, interval_seconds: int = 600):
        """
        定时执行同步任务（查缺补漏，默认10分钟）
        
        Args:
            interval_seconds: 执行间隔（秒），默认600秒（10分钟）
        """
        logger.info(f"🔄 启动定时同步任务（查缺补漏模式），执行间隔: {interval_seconds}秒（{interval_seconds//60}分钟）")
        
        try:
            while True:
                try:
                    self.run_sync_once()
                except Exception as e:
                    logger.error(f"❌ 同步任务执行失败: {e}", exc_info=True)
                
                # 等待下次执行
                logger.info(f"\n⏰ 等待 {interval_seconds} 秒（{interval_seconds//60}分钟）后执行下一次同步...\n")
                time.sleep(interval_seconds)
                
        except KeyboardInterrupt:
            logger.info("\n⏹️  用户停止定时任务")
        except Exception as e:
            logger.error(f"❌ 定时任务异常退出: {e}", exc_info=True)

