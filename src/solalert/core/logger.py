"""
日志配置模块
统一的日志格式和输出配置
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
from .config import LOG_CONFIG


def setup_logger(
    name: str = "solalert",
    level: Optional[str] = None,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    配置并返回日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件路径
        
    Returns:
        配置好的日志记录器
    """
    # 获取日志记录器
    logger = logging.getLogger(name)
    
    # 如果已经配置过，直接返回
    if logger.handlers:
        return logger
    
    # 设置日志级别
    log_level = level or LOG_CONFIG.get('level', 'INFO')
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # 日志格式
    log_format = LOG_CONFIG.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    date_format = LOG_CONFIG.get('datefmt', '%Y-%m-%d %H:%M:%S')
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器
    log_file_path = log_file or LOG_CONFIG.get('file')
    if log_file_path:
        # 确保日志目录存在
        log_dir = os.path.dirname(log_file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        # 使用RotatingFileHandler支持日志轮转
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


# 创建默认日志记录器
default_logger = setup_logger()


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        日志记录器实例
    """
    return logging.getLogger(f"solalert.{name}")

