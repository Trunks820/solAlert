"""
统一的日志配置模块
使用 logging.config.dictConfig + 自定义Filter/Formatter实现：
1. Emoji前缀（通过Filter动态添加）
2. Per-module级别控制（环境变量）
3. 结构化日志（JSON for file, 人类可读 for console）
4. Handler复用（单例模式）
"""
import os
import sys
import json
import logging
import logging.config
from typing import Dict, Any, Optional
from datetime import datetime

# ==================== 环境变量配置 ====================

# 全局默认级别
CONSOLE_LOG_LEVEL = os.getenv('CONSOLE_LOG_LEVEL', 'INFO').upper()
FILE_LOG_LEVEL = os.getenv('FILE_LOG_LEVEL', 'DEBUG').upper()

# 是否启用JSON格式（文件）
ENABLE_JSON_LOGS = os.getenv('ENABLE_JSON_LOGS', 'false').lower() == 'true'

# 日志目录和文件
LOG_DIR = os.getenv('LOG_DIR', 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'solalert.log')

# Per-module级别控制（优先级最高）
# 示例: LOG_LEVEL_BSC_WS=DEBUG, LOG_LEVEL_SOL_WS_CONSOLE=INFO
def get_module_log_level(module_key: str, output_type: str = '') -> Optional[str]:
    """
    获取模块专属日志级别
    
    优先级:
    1. LOG_LEVEL_{MODULE}_{CONSOLE|FILE} (最高)
    2. LOG_LEVEL_{MODULE}
    3. 全局默认
    
    Examples:
        LOG_LEVEL_BSC_WS_CONSOLE=WARNING
        LOG_LEVEL_SOL_WS_FILE=DEBUG
        LOG_LEVEL_BSC_WS=INFO
    """
    module_upper = module_key.upper()
    
    # 优先: 模块+输出类型
    if output_type:
        specific = os.getenv(f'LOG_LEVEL_{module_upper}_{output_type.upper()}')
        if specific:
            return specific.upper()
    
    # 次级: 模块级别
    module_level = os.getenv(f'LOG_LEVEL_{module_upper}')
    if module_level:
        return module_level.upper()
    
    return None


# ==================== 模块Emoji映射 ====================

MODULE_EMOJI_MAP = {
    # 核心监控模块（实际在用）
    'solalert.monitor.bsc_ws': '🔵',       # BSC WebSocket 监控（实时）
    'solalert.monitor.sol_ws': '🟢',       # SOL WebSocket 监控（实时）
    
    # 采集器
    'solalert.collectors.pump': '🟠',       # Pump.fun 采集器
    'solalert.collectors.bonk': '🟤',       # Bonk 采集器
    
    # 通知模块
    'solalert.notifiers': '🟡',             # 通知管理器
    'solalert.notifiers.telegram': '📱',    # Telegram通知
    'solalert.notifiers.wechat': '💬',      # WeChat通知
    
    # API模块
    'solalert.api': '🟣',                   # API通用
    'solalert.api.dbotx_api': '🟣',        # DBotX API
    'solalert.api.gmgn_api': '🟣',         # GMGN API
    'solalert.api.telegram_api': '📱',     # Telegram API
    
    # 核心服务
    'solalert.core.database': '🗄️',        # 数据库
    'solalert.core.redis': '🔶',           # Redis缓存
    
    # 默认
    'solalert': '⚪',
}


# ==================== 自定义Filter（添加Emoji） ====================

class EmojiFilter(logging.Filter):
    """
    为日志记录动态添加emoji前缀
    通过LogRecord.emoji字段传递给Formatter
    """
    def filter(self, record: logging.LogRecord) -> bool:
        # 根据logger名称查找对应的emoji
        logger_name = record.name
        
        # 精确匹配
        if logger_name in MODULE_EMOJI_MAP:
            record.emoji = MODULE_EMOJI_MAP[logger_name]
        else:
            # 层级匹配（例如 solalert.api.xxx -> 🟣）
            for prefix, emoji in MODULE_EMOJI_MAP.items():
                if logger_name.startswith(prefix):
                    record.emoji = emoji
                    break
            else:
                record.emoji = MODULE_EMOJI_MAP['solalert']  # 默认
        
        # 提取模块短名称（用于格式化）
        if logger_name.startswith('solalert.'):
            parts = logger_name.split('.')
            if len(parts) >= 3:
                # solalert.monitor.bsc_ws -> bsc_ws
                record.module_short = parts[-1].upper()
            elif len(parts) == 2:
                # solalert.api -> api
                record.module_short = parts[-1].upper()
            else:
                record.module_short = 'DEFAULT'
        else:
            record.module_short = record.name.upper()[:10]
        
        return True


# ==================== 自定义Formatter ====================

class ConsoleFormatter(logging.Formatter):
    """
    人类可读的控制台格式（带emoji）
    格式: 2025-11-04 11:00:00 [🔵BSC_WS  ] INFO - 消息内容
    """
    def format(self, record: logging.LogRecord) -> str:
        # 确保有emoji和module_short属性
        if not hasattr(record, 'emoji'):
            record.emoji = '⚪'
        if not hasattr(record, 'module_short'):
            record.module_short = 'DEFAULT'
        
        # 固定宽度的模块名（8字符）
        module_display = f"{record.emoji}{record.module_short:8s}"
        
        # 时间戳
        timestamp = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        
        # 级别
        level = record.levelname
        
        # 消息
        message = record.getMessage()
        
        # 组合
        formatted = f"{timestamp} [{module_display}] {level} - {message}"
        
        # 如果有异常，添加traceback
        if record.exc_info:
            formatted += '\n' + self.formatException(record.exc_info)
        
        return formatted


class JSONFormatter(logging.Formatter):
    """
    结构化JSON格式（用于文件输出，便于ELK/Loki采集）
    """
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'module': getattr(record, 'module_short', 'UNKNOWN'),
            'message': record.getMessage(),
            'file': record.pathname,
            'line': record.lineno,
            'function': record.funcName,
        }
        
        # 如果有异常信息
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # 如果有extra字段
        if hasattr(record, 'extra_data'):
            log_data['extra'] = record.extra_data
        
        return json.dumps(log_data, ensure_ascii=False)


# ==================== 生成dictConfig ====================

def get_logging_config() -> Dict[str, Any]:
    """
    生成统一的日志配置字典
    """
        # 确保日志目录存在
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    
    # 选择formatter
    file_formatter = 'json' if ENABLE_JSON_LOGS else 'detailed'
    
    config = {
        'version': 1,
        'disable_existing_loggers': False,
        
        # ========== Formatters ==========
        'formatters': {
            'console': {
                '()': ConsoleFormatter,
            },
            'detailed': {
                'format': '%(asctime)s [%(name)s] %(levelname)s - %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            },
            'json': {
                '()': JSONFormatter,
            }
        },
        
        # ========== Filters ==========
        'filters': {
            'emoji_filter': {
                '()': EmojiFilter,
            }
        },
        
        # ========== Handlers ==========
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': CONSOLE_LOG_LEVEL,
                'formatter': 'console',
                'filters': ['emoji_filter'],
                'stream': 'ext://sys.stdout'
            },
            'file_all': {
                'class': 'logging.handlers.RotatingFileHandler',
                'level': FILE_LOG_LEVEL,
                'formatter': file_formatter,
                'filters': ['emoji_filter'],  # JSON也需要emoji信息（存在module字段里）
                'filename': LOG_FILE,
                'maxBytes': 50 * 1024 * 1024,  # 50MB
                'backupCount': 10,
                'encoding': 'utf-8',
                'delay': True
            }
        },
        
        # ========== Loggers ==========
        'loggers': {}
    }
    
    # 动态生成各模块的logger配置（只配置实际使用的模块）
    modules = [
        # 核心监控（实际在用）
        ('solalert.monitor.bsc_ws', 'BSC_WS'),
        ('solalert.monitor.sol_ws', 'SOL_WS'),
        
        # 采集器
        ('solalert.collectors.pump', 'PUMP'),
        ('solalert.collectors.bonk', 'BONK'),
        
        # 通知
        ('solalert.notifiers', 'NOTIFIER'),
        ('solalert.notifiers.telegram', 'TELEGRAM'),
        
        # API
        ('solalert.api', 'API'),
        ('solalert.api.dbotx_api', 'DBOTX_API'),
        ('solalert.api.telegram_api', 'TG_API'),
        
        # 核心服务
        ('solalert.core.database', 'DATABASE'),
        ('solalert.core.redis', 'REDIS'),
        
        # 默认
        ('solalert', 'DEFAULT'),
    ]
    
    for logger_name, module_key in modules:
        # 获取模块专属级别（如果有）
        module_level = get_module_log_level(module_key)
        
        config['loggers'][logger_name] = {
            'level': module_level or 'DEBUG',
            'handlers': ['console', 'file_all'],
            'propagate': False
        }
    
    # Root logger（兜底）
    config['root'] = {
        'level': 'WARNING',
        'handlers': ['console', 'file_all']
    }
    
    return config


# ==================== 初始化函数 ====================

def init_logging():
    """
    初始化日志系统
    ⚠️ 必须在程序入口（main.py）调用一次
    ⚠️ 必须在导入其他模块之前调用
    
    Returns:
        root logger实例
    """
    config = get_logging_config()
    logging.config.dictConfig(config)
    
    # 抑制第三方库的日志
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('web3').setLevel(logging.ERROR)
    logging.getLogger('asyncio').setLevel(logging.CRITICAL)
    logging.getLogger('httpx').setLevel(logging.ERROR)
    logging.getLogger('httpcore').setLevel(logging.ERROR)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('websockets').setLevel(logging.WARNING)
    
    # 返回root logger
    root_logger = logging.getLogger('solalert')
    root_logger.info("📋 日志系统初始化完成")
    root_logger.info(f"  ├─ Console级别: {CONSOLE_LOG_LEVEL}")
    root_logger.info(f"  ├─ File级别: {FILE_LOG_LEVEL}")
    root_logger.info(f"  ├─ JSON格式: {'✅' if ENABLE_JSON_LOGS else '❌'}")
    root_logger.info(f"  └─ 日志文件: {LOG_FILE}")
    
    return root_logger


# ==================== 便捷API ====================

def get_logger(module_path: str) -> logging.Logger:
    """
    获取指定模块的logger
    
    Args:
        module_path: 模块路径，如 'solalert.monitor.bsc_ws'
        
    Returns:
        配置好的logger实例
        
    Examples:
        >>> from solalert.core.logger import get_logger
        >>> logger = get_logger('solalert.monitor.bsc_ws')
        >>> logger.info("这是BSC WS的日志")
    """
    return logging.getLogger(module_path)


# ==================== 兼容旧API（废弃） ====================

def setup_logger(name: str = "solalert", **kwargs):
    """
    ⚠️ 已废弃，请使用 logging.getLogger('solalert')
    """
    import warnings
    warnings.warn(
        "setup_logger() is deprecated. Use logging.getLogger('solalert') instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return logging.getLogger(name)


def setup_module_logger(module_name: str = "default", **kwargs):
    """
    ⚠️ 已废弃，请使用 logging.getLogger('solalert.module.xxx')
    """
    import warnings
    warnings.warn(
        "setup_module_logger() is deprecated. Use logging.getLogger('solalert.module.xxx') instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # 映射旧名称到新名称
    module_mapping = {
        'bsc_ws': 'solalert.monitor.bsc_ws',
        'sol_ws': 'solalert.monitor.sol_ws',
        'pump': 'solalert.collectors.pump',
        'bonk': 'solalert.collectors.bonk',
        'bsc_block': 'solalert.monitor.bsc_block',
    }
    logger_name = module_mapping.get(module_name, f'solalert.{module_name}')
    return logging.getLogger(logger_name)
