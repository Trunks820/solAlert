"""
通用格式化工具函数
提供数字、金额等格式化功能
"""
from typing import Union


def format_number(value: Union[int, float], include_dollar: bool = False) -> str:
    """
    格式化数字为K/M/B格式
    
    Args:
        value: 数值
        include_dollar: 是否包含$符号
        
    Returns:
        格式化后的字符串
        
    Examples:
        >>> format_number(1234)
        '1.23K'
        >>> format_number(1234567)
        '1.23M'
        >>> format_number(1234567890)
        '1.23B'
        >>> format_number(1234567890, include_dollar=True)
        '$1.23B'
    """
    prefix = "$" if include_dollar else ""
    
    if value >= 1_000_000_000:  # >= 1B
        return f"{prefix}{value / 1_000_000_000:.2f}B"
    elif value >= 1_000_000:  # >= 1M
        return f"{prefix}{value / 1_000_000:.2f}M"
    elif value >= 1_000:  # >= 1K
        return f"{prefix}{value / 1_000:.2f}K"
    else:
        return f"{prefix}{value:.2f}" if isinstance(value, float) else f"{prefix}{value}"


def format_price(value: float, precision: int = 10) -> str:
    """
    格式化价格（保留更多小数位）
    
    Args:
        value: 价格
        precision: 小数位数
        
    Returns:
        格式化后的价格字符串
        
    Examples:
        >>> format_price(0.000012345)
        '$0.0000123450'
        >>> format_price(123.456789, precision=4)
        '$123.4568'
    """
    return f"${value:.{precision}f}"


def format_percentage(value: float, show_sign: bool = True) -> str:
    """
    格式化百分比
    
    Args:
        value: 百分比值
        show_sign: 是否显示正负号
        
    Returns:
        格式化后的百分比字符串
        
    Examples:
        >>> format_percentage(12.345)
        '+12.35%'
        >>> format_percentage(-5.678)
        '-5.68%'
        >>> format_percentage(5.678, show_sign=False)
        '5.68%'
    """
    if show_sign:
        return f"{value:+.2f}%"
    else:
        return f"{value:.2f}%"

