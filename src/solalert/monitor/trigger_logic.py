"""
监控触发逻辑判断
根据配置判断是否满足触发条件
"""
import json
from typing import Dict, Any, List, Optional, Tuple
from ..core.logger import get_logger

logger = get_logger(__name__)


class TriggerEvent:
    """触发事件"""
    
    def __init__(self, event_type: str, value: float, threshold: float, description: str):
        self.type = event_type
        self.value = value
        self.threshold = threshold
        self.description = description
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "value": self.value,
            "threshold": self.threshold,
            "description": self.description
        }


class TriggerLogic:
    """触发逻辑判断器"""
    
    @staticmethod
    def check_price_change(
        stats: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Tuple[bool, Optional[TriggerEvent]]:
        """
        判断价格变化是否触发
        
        Args:
            stats: stats5m 数据
            config: priceChange 配置
            
        Returns:
            (是否触发, 触发事件对象)
        """
        if not config.get("enabled"):
            return False, None
        
        price_change = stats.get("priceChange", 0)
        rise_threshold = config.get("risePercent")
        fall_threshold = config.get("fallPercent")
        
        # 判断上涨
        # 注意：rise_threshold 为 0 或 None 时不监控上涨
        if rise_threshold is not None and rise_threshold > 0 and price_change >= rise_threshold:
            event = TriggerEvent(
                event_type="价格上涨",
                value=price_change,
                threshold=rise_threshold,
                description=f"📈 涨幅 +{price_change:.2f}% (阈值: ≥{rise_threshold}%)"
            )
            return True, event
        
        # 判断下跌
        # 注意：fall_threshold 为 0 或 None 时不监控下跌
        if fall_threshold is not None and fall_threshold > 0 and price_change <= -fall_threshold:
            event = TriggerEvent(
                event_type="价格下跌",
                value=price_change,
                threshold=-fall_threshold,
                description=f"📉 跌幅 {price_change:.2f}% (阈值: ≤-{fall_threshold}%)"
            )
            return True, event
        
        return False, None
    
    @staticmethod
    def check_holder_change(
        stats: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Tuple[bool, Optional[TriggerEvent]]:
        """
        判断持币人数变化是否触发
        
        Args:
            stats: stats5m 数据
            config: holders 配置
            
        Returns:
            (是否触发, 触发事件对象)
        """
        if not config.get("enabled"):
            return False, None
        
        holder_change = stats.get("holderChange", 0)
        increase_threshold = config.get("increasePercent")
        decrease_threshold = config.get("decreasePercent")
        
        # 判断增加
        # 注意：increase_threshold 为 0 或 None 时不监控增加
        if increase_threshold is not None and increase_threshold > 0 and holder_change >= increase_threshold:
            event = TriggerEvent(
                event_type="持币人数增加",
                value=holder_change,
                threshold=increase_threshold,
                description=f"👥 持币人数 +{holder_change:.2f}% (阈值: ≥{increase_threshold}%)"
            )
            return True, event
        
        # 判断减少
        # 注意：decrease_threshold 为 0 或 None 时不监控减少
        if decrease_threshold is not None and decrease_threshold > 0 and holder_change <= -decrease_threshold:
            event = TriggerEvent(
                event_type="持币人数减少",
                value=holder_change,
                threshold=-decrease_threshold,
                description=f"👥 持币人数 {holder_change:.2f}% (阈值: ≤-{decrease_threshold}%)"
            )
            return True, event
        
        return False, None
    
    @staticmethod
    def check_volume_change(
        stats: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Tuple[bool, Optional[TriggerEvent]]:
        """
        判断交易量是否触发（使用绝对值）
        
        Args:
            stats: stats5m 数据（包含 volume_24h 等字段）
            config: volume 配置（使用 threshold 绝对值，单位 USDT）
            
        Returns:
            (是否触发, 触发事件对象)
        """
        if not config.get("enabled"):
            return False, None
        
        # 只使用 threshold（绝对值，单位 USDT）
        volume_threshold = config.get("threshold")
        
        if volume_threshold is None or volume_threshold <= 0:
            # 没有配置有效阈值，不触发
            return False, None
        
        # 获取当前交易量（优先使用 volume_5m，其次 volume）
        current_volume = stats.get("volume_5m", 0) or stats.get("volume", 0)
        
        # 判断是否达到阈值
        if current_volume >= volume_threshold:
            event = TriggerEvent(
                event_type="交易量达标",
                value=current_volume,
                threshold=volume_threshold,
                description=f"💰 5分钟交易量 ${current_volume:,.0f} (阈值: ≥${volume_threshold:,.0f})"
            )
            return True, event
        
        return False, None
    
    @classmethod
    def evaluate_trigger(
        cls,
        stats: Dict[str, Any],
        events_config: Dict[str, Any],
        trigger_logic: str
    ) -> Tuple[bool, List[TriggerEvent]]:
        """
        综合判断是否触发监控
        
        Args:
            stats: stats5m 数据
            events_config: 事件配置（已解析的dict）
            trigger_logic: "any" 或 "all"
            
        Returns:
            (是否触发, 触发事件列表)
        """
        triggered_events = []
        
        # 检查价格变化
        is_triggered, event = cls.check_price_change(
            stats, 
            events_config.get("priceChange", {})
        )
        if is_triggered and event:
            triggered_events.append(event)
        
        # 检查持币人数变化
        is_triggered, event = cls.check_holder_change(
            stats,
            events_config.get("holders", {})
        )
        if is_triggered and event:
            triggered_events.append(event)
        
        # 检查交易量变化
        is_triggered, event = cls.check_volume_change(
            stats,
            events_config.get("volume", {})
        )
        if is_triggered and event:
            triggered_events.append(event)
        
        # 根据触发逻辑判断
        if trigger_logic == "any":
            # 任一条件满足即触发
            should_trigger = len(triggered_events) > 0
        else:  # "all"
            # 需要所有启用的事件都触发
            enabled_count = sum([
                1 for event_type in ["priceChange", "holders", "volume"]
                if events_config.get(event_type, {}).get("enabled", False)
            ])
            should_trigger = (
                len(triggered_events) == enabled_count and enabled_count > 0
            )
        
        return should_trigger, triggered_events
    
    @staticmethod
    def parse_events_config(events_config_str: str) -> Optional[Dict[str, Any]]:
        """
        解析 events_config JSON 字符串
        
        Args:
            events_config_str: JSON字符串
            
        Returns:
            解析后的字典，失败返回None
        """
        try:
            return json.loads(events_config_str)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"❌ 解析 events_config 失败: {e}")
            return None

