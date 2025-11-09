"""
SOL WebSocket 告警条件检查器
根据配置的监控指标和触发逻辑，检查是否需要发送告警
"""
import json
import random
import logging
from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime

from .sol_field_mapper import SolFieldMapper
from ..core.formatters import format_number

# Telegram 按钮支持
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    HAS_TELEGRAM_BUTTONS = True
except ImportError:
    HAS_TELEGRAM_BUTTONS = False
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None

logger = logging.getLogger(__name__)


class SolAlertChecker:
    """
    告警条件检查器
    负责检查WebSocket数据是否满足告警条件
    """
    
    def __init__(self, redis_client):
        """
        初始化告警检查器
        
        Args:
            redis_client: Redis客户端（用于冷却期管理）
        """
        self.redis = redis_client
        self.field_mapper = SolFieldMapper
    
    def check_alert_conditions(
        self,
        data: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        检查是否满足告警条件
        
        Args:
            data: WebSocket返回的pair数据
            config: CA的配置信息（从sol_ws_batch_pool读取）
        
        Returns:
            (是否告警, 触发原因列表, 提取的指标数据)
            
        Example:
            >>> should_alert, reasons, metrics = checker.check_alert_conditions(data, config)
            >>> if should_alert:
            >>>     send_alert(reasons, metrics)
        """
        # 1. 验证数据有效性
        if not self.field_mapper.validate_data(data):
            logger.debug(f"数据无效，跳过检查")
            return False, [], {}
        
        # 2. 提取配置（兼容 triggerLogic 和 trigger_logic 两种命名）
        time_interval = config.get('time_interval', '1m')
        events_config_str = config.get('events_config', '{}')
        # 兼容驼峰(Redis)和蛇形(Database)命名
        trigger_logic = config.get('trigger_logic') or config.get('triggerLogic', 'any')
        ca = config.get('ca', '')
        
        # 解析events_config
        try:
            events_config = json.loads(events_config_str)
        except json.JSONDecodeError:
            logger.error(f"解析events_config失败: {events_config_str}")
            return False, [], {}
        
        # 3. 提取监控指标
        metrics = self.field_mapper.extract_all_metrics(data, time_interval)
        
        # 3.1 添加历史最高市值（ATH）到 metrics
        historical_high_cap = float(config.get('market_cap', 0))
        metrics['historical_high_cap'] = historical_high_cap
        
        # 3.2 计算当前市值占ATH的比例
        if historical_high_cap > 0:
            metrics['ath_ratio'] = (metrics['market_cap'] / historical_high_cap) * 100
        else:
            metrics['ath_ratio'] = 0
        
        # 4. 检查各项条件
        triggered_conditions = []
        
        # 4.1 检查价格变化
        price_change_result = self._check_price_change(
            metrics, events_config, time_interval
        )
        if price_change_result:
            triggered_conditions.append(price_change_result)
        
        # 4.2 检查交易量
        volume_result = self._check_volume(
            metrics, events_config, time_interval
        )
        if volume_result:
            triggered_conditions.append(volume_result)
        
        # 4.3 检查持有者变化（如果配置了）
        holders_result = self._check_holders(
            metrics, events_config
        )
        if holders_result:
            triggered_conditions.append(holders_result)
        
        # 5. 根据trigger_logic判断是否触发
        should_alert = self._evaluate_trigger_logic(
            triggered_conditions, events_config, trigger_logic
        )
        
        # 6. 检查冷却期
        if should_alert:
            in_cooldown = self._check_cooldown(ca)
            if in_cooldown:
                logger.debug(f"CA {ca[:10]}... 在冷却期内，跳过告警")
                return False, [], metrics
        
        return should_alert, triggered_conditions, metrics
    
    def _check_price_change(
        self,
        metrics: Dict[str, Any],
        events_config: Dict[str, Any],
        time_interval: str
    ) -> Optional[str]:
        """检查价格变化条件"""
        price_change_config = events_config.get('priceChange', {})
        
        if not price_change_config.get('enabled'):
            return None
        
        price_change = metrics['price_change']
        
        # 验证价格变化合理性
        if not self.field_mapper.is_valid_price_change(price_change):
            logger.warning(f"价格变化异常: {price_change}%")
            return None
        
        # 检查上涨
        rise_percent = price_change_config.get('risePercent')
        if rise_percent and price_change >= rise_percent:
            return f"价格{time_interval}上涨 {price_change:+.2f}% (阈值: {rise_percent}%)"
        
        # 检查下跌
        fall_percent = price_change_config.get('fallPercent')
        if fall_percent and price_change <= -fall_percent:
            return f"价格{time_interval}下跌 {price_change:+.2f}% (阈值: -{fall_percent}%)"
        
        return None
    
    def _check_volume(
        self,
        metrics: Dict[str, Any],
        events_config: Dict[str, Any],
        time_interval: str
    ) -> Optional[str]:
        """检查交易量条件"""
        volume_config = events_config.get('volume', {})
        
        if not volume_config.get('enabled'):
            return None
        
        threshold = volume_config.get('threshold')
        if not threshold:
            return None
        
        total_volume = metrics['total_volume']
        
        if total_volume >= threshold:
            return f"{time_interval}交易量 ${total_volume:,.0f} (阈值: ${threshold:,.0f})"
        
        return None
    
    def _check_holders(
        self,
        metrics: Dict[str, Any],
        events_config: Dict[str, Any]
    ) -> Optional[str]:
        """检查持有者数量变化（暂不实现，预留接口）"""
        holders_config = events_config.get('holders', {})
        
        if not holders_config.get('enabled'):
            return None
        
        # TODO: 需要缓存历史持有者数量才能计算变化
        # 暂时不实现
        return None
    
    def _evaluate_trigger_logic(
        self,
        triggered_conditions: List[str],
        events_config: Dict[str, Any],
        trigger_logic: str
    ) -> bool:
        """
        根据触发逻辑评估是否告警
        
        Args:
            triggered_conditions: 已触发的条件列表
            events_config: 事件配置
            trigger_logic: 触发逻辑（any/all）
        
        Returns:
            是否应该告警
        """
        if not triggered_conditions:
            return False
        
        if trigger_logic == 'any':
            # 任一条件满足即触发
            return True
        
        elif trigger_logic == 'all':
            # 所有启用的条件都必须满足
            enabled_count = sum([
                1 for k, v in events_config.items()
                if isinstance(v, dict) and v.get('enabled')
            ])
            
            return len(triggered_conditions) >= enabled_count
        
        else:
            logger.warning(f"未知的trigger_logic: {trigger_logic}，默认使用any")
            return True
    
    def _check_cooldown(self, ca: str) -> bool:
        """
        检查CA是否在冷却期内
        
        Args:
            ca: Token CA地址
        
        Returns:
            是否在冷却期内
        """
        key = f"quick_monitor:ws:cooldown:{ca}"
        return self.redis.client.exists(key)
    
    def set_cooldown(self, ca: str, base_seconds: int = 180) -> None:
        """
        设置告警冷却期
        
        Args:
            ca: Token CA地址
            base_seconds: 基础冷却时间（秒），默认180秒（3分钟）
        """
        # 添加随机抖动（0-30秒），避免告警风暴
        jitter = random.randint(0, 30)
        cooldown_seconds = base_seconds + jitter
        
        key = f"quick_monitor:ws:cooldown:{ca}"
        self.redis.client.setex(key, cooldown_seconds, "1")
        
        logger.debug(f"设置冷却期: {ca[:10]}... ({cooldown_seconds}秒)")
    
    def format_config_summary(self, config: Dict[str, Any]) -> str:
        """
        格式化配置摘要（用于日志和 TG 通知）
        
        Args:
            config: 配置字典
        
        Returns:
            配置摘要字符串
        """
        time_interval = config.get('time_interval', '1m')
        trigger_logic = config.get('trigger_logic') or config.get('triggerLogic', 'any')
        trigger_logic_cn = 'any' if trigger_logic == 'any' else '全部'
        events_config_str = config.get('events_config', '{}')
        
        # 解析监控条件
        try:
            events_config = json.loads(events_config_str)
        except json.JSONDecodeError:
            return f"时间:{time_interval} | 触发:{trigger_logic_cn}"
        
        conditions = []
        
        # 价格变化条件
        price_change = events_config.get('priceChange', {})
        if price_change.get('enabled'):
            rise = price_change.get('risePercent')
            fall = price_change.get('fallPercent')
            if rise and fall:
                conditions.append(f"价格±{rise}/{fall}%")
            elif rise:
                conditions.append(f"价格↑{rise}%")
            elif fall:
                conditions.append(f"价格↓{fall}%")
        
        # 交易量条件
        volume = events_config.get('volume', {})
        if volume.get('enabled'):
            threshold = volume.get('threshold', 0)
            if threshold > 0:
                conditions.append(f"交易量>${threshold:,.0f}")
        
        # 持有者条件（如果未来支持）
        holders = events_config.get('holders', {})
        if holders.get('enabled'):
            conditions.append("持有者数变化")
        
        if not conditions:
            conditions.append("无条件")
        
        return f"时间:{time_interval} | 触发:{trigger_logic_cn} | 条件:{' & '.join(conditions)}"
    
    def format_alert_message(
        self,
        config: Dict[str, Any],
        metrics: Dict[str, Any],
        reasons: List[str]
    ) -> str:
        """
        格式化告警消息
        
        Args:
            config: CA配置信息
            metrics: 提取的指标数据
            reasons: 触发原因列表
        
        Returns:
            格式化的告警消息
        """
        ca = config.get('ca', '')
        token_symbol = config.get('token_symbol', 'Unknown')
        token_name = config.get('token_name', '')
        template_name = config.get('template_name', '')
        time_interval = config.get('time_interval', '1m')

        # 🚀 提取触发逻辑和监控条件
        trigger_logic = config.get('trigger_logic') or config.get('triggerLogic', 'any')
        trigger_logic_cn = '任一条件' if trigger_logic == 'any' else '全部条件'
        events_config_str = config.get('events_config', '{}')
        
        # 解析events_config以显示详细配置
        try:
            events_config = json.loads(events_config_str)
        except json.JSONDecodeError:
            events_config = {}
        
        monitor_conditions = []
        price_change = events_config.get('priceChange', {})
        if price_change.get('enabled'):
            rise = price_change.get('risePercent')
            fall = price_change.get('fallPercent')
            if rise and fall:
                monitor_conditions.append(f"  • 价格变化: ±{rise}% / ±{fall}%")
            elif rise:
                monitor_conditions.append(f"  • 价格上涨: >{rise}%")
            elif fall:
                monitor_conditions.append(f"  • 价格下跌: >{fall}%")
        
        volume = events_config.get('volume', {})
        if volume.get('enabled'):
            threshold = volume.get('threshold', 0)
            if threshold > 0:
                monitor_conditions.append(f"  • 交易量: >${threshold:,.0f}")
        
        monitor_str = '\n'.join(monitor_conditions) if monitor_conditions else '  • 无特定条件'
        
        # 构造消息（HTML格式）
        # 🚀 CA 链接：蓝色文本 + 可点击复制 + 点击跳转solscan
        ca_link = f'<a href="https://solscan.io/token/{ca}"><code>{ca}</code></a>'
        
        # 格式化市值和流动性
        market_cap_str = format_number(metrics['market_cap'], include_dollar=True)
        liquidity_str = format_number(metrics['liquidity'], include_dollar=True)

        message = f"""<b>🔔 SOL实时告警</b>

💰 Token: <b>{token_symbol}</b>
📝 名称: {token_name}
🔗 CA: {ca_link}
🏷️ 模板: {template_name}

📊 <b>监控配置</b>
⏰ 时间窗口: {time_interval}
🎯 触发逻辑: {trigger_logic_cn}
🔍 监控条件:
{monitor_str}

💵 当前价格: <b>${metrics['price']:.10f}</b>
💎 当前市值: <b>{market_cap_str}</b>
📈 价格变化: {metrics['price_change']:+.2f}%
💧 流动性: {liquidity_str}
📊 TOP10持仓: {metrics['top10_percent']:.2f}%

✨ 触发原因:
"""
        
        for i, reason in enumerate(reasons, 1):
            message += f"{i}. {reason}\n"
        
        message += f"\n⏰ 告警时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return message
    
    def create_sol_buttons(self, ca: str, pair_address: str = None):
        """
        创建SOL Token的Telegram内联按钮
        
        Args:
            ca: Token CA地址
            pair_address: Pair地址（用于AXIOM链接，如果不提供则使用CA）
        
        Returns:
            InlineKeyboardMarkup对象，如果不支持则返回None
        """
        if not HAS_TELEGRAM_BUTTONS:
            logger.warning("未安装python-telegram-bot库，无法创建按钮")
            return None
        
        # 🚀 Axiom 使用 CA 地址，固定添加 ?chain=sol 参数
        buttons = [
            [
                InlineKeyboardButton("💹 GMGN", url=f"https://gmgn.ai/sol/token/{ca}"),
                InlineKeyboardButton("📊 AXIOM", url=f"https://axiom.trade/meme/{pair_address}")
            ]
        ]
        return InlineKeyboardMarkup(buttons)

