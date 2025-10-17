"""
配置管理模块 - 简化版（仅保留 solAlert 需要的配置）
支持环境变量和YAML配置文件
"""
import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

logger = logging.getLogger(__name__)


# ==================== 数据库配置 ====================
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '47.106.217.116'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD', 'Wy1997@Kakarot'),
    'database': os.getenv('DB_NAME', 'crypto_web3'),
    'charset': 'utf8mb4'
}


# ==================== Redis配置 ====================
REDIS_CONFIG = {
    'host': os.getenv('REDIS_HOST', '47.106.217.116'),
    'port': int(os.getenv('REDIS_PORT', 6379)),
    'password': os.getenv('REDIS_PASSWORD', 'Wy1997@Kakarot'),
    'db': int(os.getenv('REDIS_DB', 0)),
    'decode_responses': True,
    'socket_timeout': 15,
    'socket_connect_timeout': 15,
    'retry_on_timeout': True,
    'health_check_interval': 60,
    'max_connections': 20,
}


# ==================== Telegram配置 ====================
TELEGRAM_CONFIG = {
    'api_id': int(os.getenv('TG_API_ID', 21047003)),
    'api_hash': os.getenv('TG_API_HASH', '0e1f8f9817af2d8b66c09bfa141e962a'),
    'bot_token': os.getenv('TG_BOT_TOKEN', '8321549857:AAHnq64MVroI23UB-DKM5lv9gOY0vG7hi8g'),
    'forum_group_id': int(os.getenv('TG_FORUM_GROUP', -1003073529793)),
    'target_channel_id': int(os.getenv('TG_TARGET_CHANNEL', -1002569647443)),
    'gmgn_channel_id': int(os.getenv('TG_GMGN_CHANNEL', -1002115686230)),  # GMGN频道ID
    # 代理配置（已禁用，上传服务器时不需要代理）
    'proxy': {
        'enabled': False,  # os.getenv('PROXY_ENABLED', 'True').lower() == 'true',
        'host': os.getenv('PROXY_HOST', '127.0.0.1'),
        'port': int(os.getenv('PROXY_PORT', 1081)),
        'type': os.getenv('PROXY_TYPE', 'socks5'),
    }
}


# ==================== 微信配置 ====================
WECHAT_CONFIG = {
    'enabled': os.getenv('WX_ENABLE', 'True').lower() == 'true',
    'api_url': os.getenv('WX_API_URL', 'http://47.106.217.116:1238'),
    'api_key': os.getenv('WX_API_KEY', '1ff5a3a6-bb71-4717-83ef-00511d68e260'),
    'default_target': os.getenv('WX_DEFAULT_TARGET', '47411718824@chatroom'),
}


# ==================== Twitter API配置 ====================
TWITTER_API_CONFIG = {
    'base_url': os.getenv('TWITTER_API_BASE_URL', 'https://alpha.apidance.pro'),
    'authorization': os.getenv('TWITTER_API_TOKEN', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJXYWxsZXRBZGRyZXNzIjoiMHhCRWIwOTVjQWVGNjQ0QzcxMGQ2RmYxOThiNmU1NEQ2RGQxMmVCRGFiIiwiUmVhZE9ubHkiOmZhbHNlLCJNYXhGb2xsb3ciOjAsIk1heFdvcmRzIjowLCJNYXhFeHBvcnQiOjAsImV4cCI6NzgwODQwNjU5NSwiaWF0IjoxNzYwNDA2NTk1fQ.5GPPwTr2pQe7HjR6TLvq_7Ii1KuC3UkdAS7GP4pShS4'),
    'timeout': int(os.getenv('TWITTER_API_TIMEOUT', 10)),
    'max_retries': int(os.getenv('TWITTER_API_MAX_RETRIES', 3)),
    'retry_delay': float(os.getenv('TWITTER_API_RETRY_DELAY', 0.5)),  # API调用间隔（秒）
    
    # API端点配置 (使用PUT方法)
    'endpoints': {
        'follow': '/api/v1/user/follow/push',      # 关注推送
        'tweet': '/api/v1/user/tweet/push',        # 推文推送
        'retweet': '/api/v1/user/retweet/push',    # 转发推送
        'reply': '/api/v1/user/reply/push',        # 回复推送
        'avatar': '/api/v1/user/avatar/push',      # 头像推送
    }
}


# ==================== 日志配置 ====================
LOG_CONFIG = {
    'level': os.getenv('LOG_LEVEL', 'INFO'),
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'datefmt': '%Y-%m-%d %H:%M:%S',
    'file': os.getenv('LOG_FILE', 'logs/solalert.log'),
}


# ==================== 监控配置 ====================
MONITOR_CONFIG = {
    'check_interval': int(os.getenv('MONITOR_CHECK_INTERVAL', 30)),
    'notification_min_interval': int(os.getenv('NOTIFICATION_MIN_INTERVAL', 300)),
    'data_retention_hours': int(os.getenv('DATA_RETENTION_HOURS', 24)),
    'max_api_retries': int(os.getenv('MAX_API_RETRIES', 3)),
    'api_timeout': int(os.getenv('API_TIMEOUT', 10)),
}


# ==================== 配置验证 ====================
def validate_config() -> bool:
    """验证关键配置项"""
    errors = []
    
    # 验证数据库配置
    required_db_fields = ['host', 'port', 'user', 'password', 'database']
    for field in required_db_fields:
        if not DB_CONFIG.get(field):
            errors.append(f"缺少数据库配置: {field}")
    
    # 验证Telegram配置  
    required_tg_fields = ['api_id', 'api_hash', 'bot_token']
    for field in required_tg_fields:
        if not TELEGRAM_CONFIG.get(field):
            errors.append(f"缺少Telegram配置: {field}")
    
    # 验证Redis配置
    required_redis_fields = ['host', 'port']
    for field in required_redis_fields:
        if not REDIS_CONFIG.get(field):
            errors.append(f"缺少Redis配置: {field}")
    
    # 验证日志目录
    log_file = LOG_CONFIG.get('file')
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
                logger.info(f"✅ 创建日志目录: {log_dir}")
            except Exception as e:
                errors.append(f"无法创建日志目录 {log_dir}: {e}")
    
    if errors:
        error_msg = f"配置验证失败: {'; '.join(errors)}"
        logger.error(f"❌ {error_msg}")
        raise ValueError(error_msg)
    
    logger.info("✅ 配置验证通过")
    return True


def get_config_summary() -> Dict[str, Any]:
    """获取配置摘要（不包含敏感信息）"""
    return {
        "database": {
            "host": DB_CONFIG['host'],
            "port": DB_CONFIG['port'],
            "database": DB_CONFIG['database']
        },
        "redis": {
            "host": REDIS_CONFIG['host'],
            "port": REDIS_CONFIG['port'],
            "db": REDIS_CONFIG['db']
        },
        "telegram": {
            "forum_group_id": TELEGRAM_CONFIG['forum_group_id'],
            "target_channel_id": TELEGRAM_CONFIG['target_channel_id'],
            "proxy_enabled": TELEGRAM_CONFIG['proxy']['enabled']
        },
        "wechat": {
            "enabled": WECHAT_CONFIG['enabled'],
            "api_url": WECHAT_CONFIG['api_url']
        },
        "twitter_api": {
            "base_url": TWITTER_API_CONFIG['base_url'],
            "timeout": TWITTER_API_CONFIG['timeout']
        },
        "logging": {
            "level": LOG_CONFIG['level'],
            "file": LOG_CONFIG['file']
        },
        "monitoring": {
            "check_interval": MONITOR_CONFIG['check_interval'],
            "notification_min_interval": MONITOR_CONFIG['notification_min_interval']
        }
    }


# 在模块加载时自动验证配置
if __name__ != "__main__":
    try:
        validate_config()
    except ValueError as e:
        print(f"⚠️ 配置警告: {e}")
        # 开发环境可以继续运行，生产环境应该退出
        # sys.exit(1)

