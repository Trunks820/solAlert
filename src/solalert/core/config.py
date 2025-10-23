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


# ==================== HTTP 代理配置 ====================
# 服务器默认关闭代理，开发环境需要时设置 HTTP_PROXY_ENABLED=true
HTTP_PROXY_CONFIG = {
    'enabled': os.getenv('HTTP_PROXY_ENABLED', 'false').lower() == 'true',
    'http_proxy': os.getenv('HTTP_PROXY', 'socks5://127.0.0.1:1081'),
    'https_proxy': os.getenv('HTTPS_PROXY', 'socks5://127.0.0.1:1081'),
}



# ==================== GMGN Cookie 配置 ====================
GMGN_COOKIE_CONFIG = {
    'sol': os.getenv('GMGN_COOKIE_SOL', '_ga=GA1.1.777785332.1712628586; GMGN_LOCALE=zh-CN; GMGN_THEME=dark; _ga_0XM0LYXGC8=deleted; GMGN_CHAIN=sol; cf_clearance=kGk1fviudvjYoycoB8p0lDvQlLZ.NVwppcBClXoCyuU-1758871878-1.2.1.1-8bKzlJQgrLdW2FYpzTOc3GV0uVLtag97R7TmcvV3czBzp4X9eJ5GKKDNcDR1ieB_sdid5hjhvjzRZLBBZA79HnkWXp4eePChJZ2M5OtlmZg0COt806bwv3vVUGWwefahHy7B6SNQq8wyhiKWvWZviAPNTcqSXgVRycDXlSypZZNz8aRYSS.kP8aZQb.TGcQyhcVoMHLOyaak2QZz4LElXrSrPP_Iu9QiXpO4Gp5oXg0; sid=gmgn%7Cdd0338480e80cb62fd084a997f5ba619; _ga_UGLVBMV4Z0=GS1.2.1761122267756211.245e6720757dcf65dbf07509eaa78e45.TqKHb9RERMXgyylHkzVEdw%3D%3D.cUVssZurif44ulffusd0jg%3D%3D.7yaEtYt33EhexBLBa3Preg%3D%3D.vROC4NpoXjbF3TCihM4z2w%3D%3D; __cf_bm=yz2XKoOxZ00nQmhdqdOh0VfMQFELYPmT4KD7I_kEnlU-1761123382-1.0.1.1-phndFEJydfCR22kUhC4tgVdqdFwEpq30cCvpcrE7dCI_7ujjWu231tKZEdfKtQRrusEtfzvngHc5rbMjJZMXb3XDEsSGCshmMmGyNML3xhg; _ga_0XM0LYXGC8=GS2.1.s1761118331$o625$g1$t1761123866$j48$l0$h0'),
    'bsc': os.getenv('GMGN_COOKIE_BSC', '_ga=GA1.1.777785332.1712628586; GMGN_LOCALE=zh-CN; GMGN_THEME=dark; _ga_0XM0LYXGC8=deleted; GMGN_CHAIN=sol; cf_clearance=kGk1fviudvjYoycoB8p0lDvQlLZ.NVwppcBClXoCyuU-1758871878-1.2.1.1-8bKzlJQgrLdW2FYpzTOc3GV0uVLtag97R7TmcvV3czBzp4X9eJ5GKKDNcDR1ieB_sdid5hjhvjzRZLBBZA79HnkWXp4eePChJZ2M5OtlmZg0COt806bwv3vVUGWwefahHy7B6SNQq8wyhiKWvWZviAPNTcqSXgVRycDXlSypZZNz8aRYSS.kP8aZQb.TGcQyhcVoMHLOyaak2QZz4LElXrSrPP_Iu9QiXpO4Gp5oXg0; sid=gmgn%7C130169b01a24f719a17c10b047b55881; _ga_UGLVBMV4Z0=GS1.2.1761198622311863.245e6720757dcf65dbf07509eaa78e45.TqKHb9RERMXgyylHkzVEdw%3D%3D.1cn3%2BNuIbD1IXqQv1y46UQ%3D%3D.o07Z2c3QjCdYzRpwBAyaCg%3D%3D.2aH0H5WFcCKPuS49b34smQ%3D%3D; __cf_bm=X3AW96iQWf0H6uH5X.3o.bMfSzfo2bbMv_LqcXsRDeg-1761199664-1.0.1.1-jj8XAmS2zMyG_YpUMHOyVEf8r.xsuG8R8mlPH5ZMWgX4AlD468Yb_6xpVthTVUneLywLYsHEYtKmuVVRLGm3wXEs0kaLWxTxmEoA8iNaHj0; _ga_0XM0LYXGC8=GS2.1.s1761198600$o632$g1$t1761199651$j60$l0$h0'),
}


# ==================== Telegram配置 ====================
TELEGRAM_CONFIG = {
    'api_id': int(os.getenv('TG_API_ID', 21047003)),
    'api_hash': os.getenv('TG_API_HASH', '0e1f8f9817af2d8b66c09bfa141e962a'),
    'bot_token': os.getenv('TG_BOT_TOKEN', '8321549857:AAHnq64MVroI23UB-DKM5lv9gOY0vG7hi8g'),
    'forum_group_id': int(os.getenv('TG_FORUM_GROUP', -1003073529793)),
    'target_channel_id': int(os.getenv('TG_TARGET_CHANNEL', -1002569647443)),
    'gmgn_channel_id': int(os.getenv('TG_GMGN_CHANNEL', -1002115686230)),  # GMGN频道ID
    'bsc_channel_id': int(os.getenv('TG_BSC_CHANNEL', -1002569554228)),  # BSC监控专用频道ID
    # 代理配置（默认禁用，本地开发时可以设置 TG_PROXY_ENABLED=true 启用）
    'proxy': {
        'enabled': os.getenv('TG_PROXY_ENABLED', 'false').lower() == 'true',  # 默认禁用
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


# ==================== WebSocket 推送配置 ====================
WEBSOCKET_PUSH_CONFIG = {
    'enabled': os.getenv('WS_PUSH_ENABLE', 'True').lower() == 'true',
    'backend_url': os.getenv('WS_BACKEND_URL', 'http://kakarot8.fun:8080/api/notification/push'),
    'secret_token': os.getenv('WS_SECRET_TOKEN', 'Wy1997@Kakarot'),
    'timeout': int(os.getenv('WS_PUSH_TIMEOUT', 3)),
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


# ===== 配置验证 ====================
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


# ==================== BSC 监控配置 ====================
BSC_MONITOR_CONFIG = {
    'rpc_endpoints': [
        "https://bnb-mainnet.g.alchemy.com/v2/tWN9bIGWh3OMCNHpii7ui",  # Alchemy（稳定）
    ],
    'confirmations': 12,
    'poll_interval': 0.8,
    'pancake_v2_factory': '0xca143ce32fe78f1f7019d7d551a6402fc5350c73',
    'topic_pair_created': '0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9',
    'topic_swap': '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822',
    'usdt_address': '0x55d398326f99059ff775485246999027b3197955',
    'wbnb_address': '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
    'usdt_wbnb_pair': '0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae',
    'reserve_fresh_seconds': 15,
}


# 在模块加载时自动验证配置
if __name__ != "__main__":
    try:
        validate_config()
    except ValueError as e:
        print(f"⚠️ 配置警告: {e}")
        # 开发环境可以继续运行，生产环境应该退出
        # sys.exit(1)

