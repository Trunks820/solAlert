"""
SOL WebSocket 字段映射工具
根据时间间隔动态选择对应的数据字段
"""
from typing import Dict, Any, Optional


class SolFieldMapper:
    """
    字段映射器
    根据配置的time_interval，从WebSocket数据中提取对应时间窗口的字段
    """
    
    # 字段映射表
    FIELD_MAP = {
        '1m': {
            'price_change': 'pc1m',
            'buy_volume': 'bv1m',
            'sell_volume': 'sv1m',
            'buy_txs': 'bt1m',
            'sell_txs': 'st1m',
            'buy_amount': 'ba1m',
            'sell_amount': 'sa1m',
        },
        '5m': {
            'price_change': 'pc5m',
            'buy_volume': 'bv5m',
            'sell_volume': 'sv5m',
            'buy_txs': 'bt5m',
            'sell_txs': 'st5m',
            'buy_amount': 'ba5m',
            'sell_amount': 'sa5m',
        },
        '1h': {
            'price_change': 'pc1h',
            'buy_volume': 'bv1h',
            'sell_volume': 'sv1h',
            'buy_txs': 'bt1h',
            'sell_txs': 'st1h',
            'buy_amount': 'ba1h',
            'sell_amount': 'sa1h',
        },
        '6h': {
            'price_change': 'pc6h',
            'buy_volume': 'bv6h',
            'sell_volume': 'sv6h',
            'buy_txs': 'bt6h',
            'sell_txs': 'st6h',
        },
        '24h': {
            'price_change': 'pc24h',
            'buy_volume': 'bv24h',
            'sell_volume': 'sv24h',
            'buy_txs': 'bt24h',
            'sell_txs': 'st24h',
        },
    }
    
    @classmethod
    def get_field_value(
        cls, 
        data: Dict[str, Any], 
        field_name: str, 
        time_interval: str
    ) -> float:
        """
        根据时间间隔获取对应字段的值
        
        Args:
            data: WebSocket返回的数据
            field_name: 字段名（price_change, buy_volume等）
            time_interval: 时间间隔（1m, 5m, 1h, 6h, 24h）
        
        Returns:
            对应字段的值，如果字段不存在返回0
        
        Example:
            >>> data = {'pc1m': 0.05, 'pc5m': 0.08, 'bv1m': 1000}
            >>> SolFieldMapper.get_field_value(data, 'price_change', '1m')
            0.05
            >>> SolFieldMapper.get_field_value(data, 'price_change', '5m')
            0.08
        """
        # 获取字段映射
        interval_map = cls.FIELD_MAP.get(time_interval)
        if not interval_map:
            return 0.0
        
        # 获取实际字段名
        field_key = interval_map.get(field_name)
        if not field_key:
            return 0.0
        
        # 返回字段值
        return float(data.get(field_key, 0))
    
    @classmethod
    def extract_price_change(
        cls, 
        data: Dict[str, Any], 
        time_interval: str
    ) -> float:
        """
        提取价格变化百分比
        
        Args:
            data: WebSocket返回的数据
            time_interval: 时间间隔
        
        Returns:
            价格变化百分比（如 5.23 表示 +5.23%）
        """
        return cls.get_field_value(data, 'price_change', time_interval) * 100
    
    @classmethod
    def extract_volume(
        cls, 
        data: Dict[str, Any], 
        time_interval: str
    ) -> Dict[str, float]:
        """
        提取交易量数据
        
        Args:
            data: WebSocket返回的数据
            time_interval: 时间间隔
        
        Returns:
            交易量数据字典:
            {
                'buy_volume': 买入量,
                'sell_volume': 卖出量,
                'total_volume': 总交易量,
                'buy_txs': 买入次数,
                'sell_txs': 卖出次数,
                'total_txs': 总交易次数
            }
        """
        buy_volume = cls.get_field_value(data, 'buy_volume', time_interval)
        sell_volume = cls.get_field_value(data, 'sell_volume', time_interval)
        buy_txs = cls.get_field_value(data, 'buy_txs', time_interval)
        sell_txs = cls.get_field_value(data, 'sell_txs', time_interval)
        
        return {
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'total_volume': buy_volume + sell_volume,
            'buy_txs': int(buy_txs),
            'sell_txs': int(sell_txs),
            'total_txs': int(buy_txs + sell_txs),
        }
    
    @classmethod
    def extract_all_metrics(
        cls,
        data: Dict[str, Any],
        time_interval: str
    ) -> Dict[str, Any]:
        """
        提取所有监控指标
        
        Args:
            data: WebSocket返回的数据
            time_interval: 时间间隔
        
        Returns:
            完整的监控指标数据
        """
        # 基础数据
        pair = data.get('p', '')
        price = data.get('tp', 0)
        market_cap = data.get('mp', 0)
        
        # 价格变化
        price_change = cls.extract_price_change(data, time_interval)
        
        # 交易量数据
        volume_data = cls.extract_volume(data, time_interval)
        
        # TOP10持仓
        top10_percent = data.get('t10', 0) * 100
        
        # 流动性
        liquidity = data.get('tr', 0)
        
        # 持有者数量
        holders = data.get('h', 0)
        
        return {
            'pair': pair,
            'price': price,
            'market_cap': market_cap,
            'price_change': price_change,
            'buy_volume': volume_data['buy_volume'],
            'sell_volume': volume_data['sell_volume'],
            'total_volume': volume_data['total_volume'],
            'buy_txs': volume_data['buy_txs'],
            'sell_txs': volume_data['sell_txs'],
            'total_txs': volume_data['total_txs'],
            'top10_percent': top10_percent,
            'liquidity': liquidity,
            'holders': holders,
        }
    
    @classmethod
    def validate_data(cls, data: Dict[str, Any]) -> bool:
        """
        验证WebSocket数据的完整性
        
        Args:
            data: WebSocket返回的数据
        
        Returns:
            数据是否有效
        """
        # 必需字段
        required_fields = ['p', 'tp', 'mp']
        
        # 检查必需字段
        for field in required_fields:
            if field not in data:
                return False
        
        # 检查价格和市值是否合理
        price = data.get('tp', 0)
        market_cap = data.get('mp', 0)
        
        if price <= 0 or market_cap <= 0:
            return False
        
        return True
    
    @classmethod
    def is_valid_price_change(cls, price_change: float) -> bool:
        """
        验证价格变化是否在合理范围内
        
        Args:
            price_change: 价格变化百分比
        
        Returns:
            是否合理（-1000% 到 +1000%）
        """
        return -1000.0 <= price_change <= 1000.0

