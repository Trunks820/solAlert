"""
预警记录器
将监控预警写入数据库，并推送到 WebSocket
"""
import json
import logging
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional

from ..core.database import get_db
from ..core.config import WEBSOCKET_PUSH_CONFIG

logger = logging.getLogger(__name__)


class AlertRecorder:
    """预警记录器"""
    
    def __init__(self):
        """初始化预警记录器"""
        self.db = get_db()
        
        # WebSocket 推送配置
        self.ws_push_enabled = WEBSOCKET_PUSH_CONFIG.get('enabled', True)
        self.backend_url = WEBSOCKET_PUSH_CONFIG.get('backend_url')
        self.secret_token = WEBSOCKET_PUSH_CONFIG.get('secret_token')
        self.timeout = WEBSOCKET_PUSH_CONFIG.get('timeout', 3)
        
        if self.ws_push_enabled:
            logger.info(f"✅ 预警记录器初始化完成（WebSocket 推送已启用: {self.backend_url}）")
        else:
            logger.info("✅ 预警记录器初始化完成（WebSocket 推送已禁用）")
    
    def write_alert(
        self,
        config_id: int,
        ca: str,
        token_name: str,
        token_symbol: str,
        trigger_events: List[Dict[str, Any]],
        stats_data: Dict[str, Any],
        notify_methods: str = "telegram"
    ) -> bool:
        """
        将预警记录写入数据库
        前端会自动从 token_monitor_alert_log 表读取并显示
        
        Args:
            config_id: 监控配置ID
            ca: 合约地址
            token_name: Token名称
            token_symbol: Token符号
            trigger_events: 触发事件列表，格式:
                [{
                    "type": "价格上涨",
                    "value": 30.79,
                    "threshold": 10,
                    "description": "📈 涨幅 +30.79% (阈值: ≥10%)"
                }]
            stats_data: 统计数据，格式:
                {
                    "priceChange": 30.79,
                    "holderChange": 5.2,
                    "volumeChange": 120.5
                }
            notify_methods: 通知方式，默认"telegram"
        
        Returns:
            bool: 是否写入成功
        """
        try:
            # 准备数据
            trigger_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            trigger_events_json = json.dumps(trigger_events, ensure_ascii=False)
            stats_data_json = json.dumps(stats_data, ensure_ascii=False)
            
            # 从 stats_data 中提取 market_cap 和 chain_type
            market_cap = stats_data.get('marketCap') or stats_data.get('market_cap')
            chain_type = stats_data.get('chain')  # 必须明确提供，不设默认值
            
            # 如果没有提供 chain 字段，记录警告并跳过
            if not chain_type:
                logger.warning(f"⚠️ stats_data 中缺少 'chain' 字段，无法保存预警记录: {ca[:10]}...")
                return False
            
            # SQL插入语句（添加 market_cap 和 chain_type 字段）
            sql = """
            INSERT INTO token_monitor_alert_log 
            (config_id, ca, token_name, token_symbol, trigger_time, trigger_events, stats_data, notify_methods, notify_status, market_cap, chain_type, create_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            # 执行插入
            rowcount = self.db.execute_update(sql, (
                config_id,
                ca,
                token_name,
                token_symbol,
                trigger_time,
                trigger_events_json,
                stats_data_json,
                notify_methods,
                'success',  # 通知状态
                market_cap,
                chain_type,
                trigger_time
            ))
            
            if rowcount > 0:
                logger.info(
                    f"✅ 预警记录已写入数据库: {token_symbol} ({ca[:10]}...) - "
                    f"{trigger_events[0].get('description', '未知触发')} "
                )
                return True
            else:
                logger.warning(f"⚠️  预警记录写入失败，影响行数为 0")
                return False
        
        except Exception as e:
            logger.error(f"❌ 写入预警记录失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def send_realtime_notification(
        self,
        token_symbol: str,
        ca: str,
        description: str,
        alert_time: Optional[str] = None,
        chain: str = "bsc",
        price: Optional[float] = None,
        price_change: Optional[float] = None,
        market_cap: Optional[float] = None,
        volume_24h: Optional[float] = None,
        holders: Optional[int] = None,
        logo: Optional[str] = None
    ) -> bool:
        """
        发送实时通知到后端（通过 WebSocket 推送给前端）
        
        Args:
            token_symbol: Token符号
            ca: 合约地址
            description: 描述信息
            alert_time: 预警时间（可选，默认当前时间）
            chain: 链类型（sol/bsc/eth）
            price: 当前价格
            price_change: 价格变化百分比
            market_cap: 市值
            volume_24h: 24小时交易量
            holders: 持币人数
            logo: Token Logo URL
        
        Returns:
            bool: 是否发送成功
        """
        if not self.ws_push_enabled:
            logger.debug("WebSocket 推送已禁用，跳过实时通知")
            return False
        
        # 生成 GMGN 链接作为 actionUrl
        action_url = f"https://gmgn.ai/{chain}/token/{ca}"
        
        push_data = {
            "token": self.secret_token,
            "data": {
                # 必填字段
                "ca": ca,
                "tokenSymbol": token_symbol,
                "triggerEvents": description,
                "alertTime": alert_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "chain": chain,
                "actionUrl": action_url  # 跳转到 GMGN 页面
            }
        }
        
        # 添加可选字段
        if price is not None:
            push_data["data"]["price"] = str(price)
        if price_change is not None:
            push_data["data"]["priceChange"] = price_change
        if market_cap is not None:
            push_data["data"]["marketCap"] = market_cap
        if volume_24h is not None:
            push_data["data"]["volume24h"] = volume_24h
        if holders is not None:
            push_data["data"]["holders"] = holders
        if logo:
            push_data["data"]["extraData"] = {
                "avatar": logo,
                "logo": logo
            }
        
        try:
            # 调试日志：打印完整的推送数据
            import json
            logger.info(f"   📤 WebSocket 推送完整数据:")
            logger.info(json.dumps(push_data, indent=2, ensure_ascii=False))
            
            response = requests.post(
                self.backend_url,
                json=push_data,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 200:
                    logger.info(f"✅ 实时通知已推送到 WebSocket: {token_symbol} - {description}")
                    return True
                else:
                    logger.warning(f"⚠️  WebSocket 推送失败: {result.get('msg', 'Unknown error')}")
                    return False
            else:
                logger.warning(f"⚠️  WebSocket 推送失败: HTTP {response.status_code}")
                return False
        
        except requests.exceptions.Timeout:
            logger.warning(f"⚠️  WebSocket 推送超时（{self.timeout}秒）")
            return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️  WebSocket 推送异常（后端可能未启动）: {e}")
            return False
    
    def send_alert_notification(
        self,
        config_id: int,
        ca: str,
        token_name: str,
        token_symbol: str,
        trigger_events: List[Dict[str, Any]],
        stats_data: Dict[str, Any],
        notify_methods: str = "telegram"
    ) -> Dict[str, bool]:
        """
        发送完整的预警通知（推荐使用此方法）
        会同时：
        1. 写入数据库（持久化）
        2. 发送实时通知到前端（如果用户在线）
        
        Args:
            config_id: 监控配置ID
            ca: 合约地址
            token_name: Token名称
            token_symbol: Token符号
            trigger_events: 触发事件列表
            stats_data: 统计数据
            notify_methods: 通知方式
        
        Returns:
            dict: {"db_success": bool, "realtime_success": bool}
        """
        # 1. 写入数据库（必须成功）
        db_result = self.write_alert(
            config_id=config_id,
            ca=ca,
            token_name=token_name,
            token_symbol=token_symbol,
            trigger_events=trigger_events,
            stats_data=stats_data,
            notify_methods=notify_methods
        )
        
        # 2. 发送实时通知（尽力而为，失败了也没关系）
        # 合并所有触发原因
        descriptions = [event.get('description', '') for event in trigger_events if event.get('description')]
        description = '; '.join(descriptions) if descriptions else '触发监控条件'
        
        # 从stats_data中获取链类型（必须明确指定，否则报错）
        chain = stats_data.get('chain')
        if not chain:
            logger.warning(f"⚠️ stats_data 中缺少 'chain' 字段，跳过实时推送")
            return {"db_success": db_result, "realtime_success": False}
        
        chain = chain.lower()
        realtime_result = self.send_realtime_notification(
            token_symbol=token_symbol,
            ca=ca,
            description=description,
            chain=chain,
            price=stats_data.get('price'),
            price_change=stats_data.get('priceChange'),
            market_cap=stats_data.get('marketCap'),
            volume_24h=stats_data.get('volume24h'),
            holders=stats_data.get('holders'),
            logo=stats_data.get('logo')
        )
        
        return {
            "db_success": db_result,
            "realtime_success": realtime_result
        }
    
    async def write_sol_alert(
        self,
        config_id: int,
        ca: str,
        token_name: str,
        token_symbol: str,
        alert_reasons: List[str],
        price: float = 0.0,
        price_change: float = 0.0,
        market_cap: float = 0.0,
        volume_24h: float = 0.0,
        holders: int = 0,
        logo: str = "",
        notify_methods: str = "telegram"
    ) -> bool:
        """
        写入 SOL 链监控预警（数据库 + WebSocket）
        
        Args:
            config_id: 监控配置ID
            ca: 合约地址
            token_name: Token名称
            token_symbol: Token符号
            alert_reasons: 触发原因列表
            price: 当前价格
            price_change: 价格变化百分比
            market_cap: 市值
            volume_24h: 24小时交易量
            holders: 持有人数
            logo: Token图标URL
            notify_methods: 通知方式
        
        Returns:
            bool: 是否写入成功
        """
        # 构建触发事件列表
        trigger_events = []
        for reason in alert_reasons:
            trigger_events.append({
                "type": "指标触发",
                "description": reason
            })
        
        # 构建统计数据
        stats_data = {
            "price": price,
            "priceChange": price_change,
            "marketCap": market_cap,
            "volume24h": volume_24h,
            "holders": holders,
            "logo": logo,
            "chain": "sol",  # SOL 链
            "dex": "Jupiter"
        }
        
        # 使用完整的预警通知（数据库 + WebSocket）
        result = self.send_alert_notification(
            config_id=config_id,
            ca=ca,
            token_name=token_name,
            token_symbol=token_symbol,
            trigger_events=trigger_events,
            stats_data=stats_data,
            notify_methods=notify_methods
        )
        
        return result['db_success']
    
    def write_bsc_alert(
        self,
        ca: str,
        token_name: str,
        token_symbol: str,
        single_max: float,
        total_sum: float,
        alert_reasons: List[str],
        block_number: int,
        price_usdt: float = 0.0,
        pair_address: str = "",
        market_cap: float = 0.0,
        price_change: float = 0.0,
        volume_24h: float = 0.0,
        holders: int = 0,
        logo: str = ""
    ) -> bool:
        """
        写入 BSC 监控预警
        
        Args:
            ca: 合约地址
            token_name: Token名称
            token_symbol: Token符号
            single_max: 单笔最大金额
            total_sum: 区块累计金额
            alert_reasons: 触发原因列表
            block_number: 区块号
            price_usdt: 当前价格（USDT）
            pair_address: 交易对地址
            market_cap: 市值
        
        Returns:
            bool: 是否写入成功
        """
        # 构建触发事件列表（只用第二层的触发原因，不包含第一层的条件）
        trigger_events = []
        
        # 直接使用传入的触发原因（来自第二层过滤）
        for reason in alert_reasons:
            trigger_events.append({
                "type": "指标触发",
                "description": reason
            })
        
        # 构建统计数据
        stats_data = {
            "singleMax": single_max,
            "totalSum": total_sum,
            "blockNumber": block_number,
            "priceUsdt": price_usdt,
            "pairAddress": pair_address,
            "marketCap": market_cap,
            "chain": "bsc",  # 小写链类型
            "dex": "PancakeSwap V2",
            # WebSocket 推送所需字段
            "price": price_usdt,
            "priceChange": price_change,
            "volume24h": volume_24h,
            "holders": holders,
            "logo": logo
        }
        
        # 使用完整的预警通知（数据库 + WebSocket）
        result = self.send_alert_notification(
            config_id=0,  # BSC 监控使用特殊的 config_id
            ca=ca,
            token_name=token_name,
            token_symbol=token_symbol,
            trigger_events=trigger_events,
            stats_data=stats_data,
            notify_methods="telegram,wechat"
        )
        
        return result['db_success']


# 全局实例
_alert_recorder = None


def get_alert_recorder() -> AlertRecorder:
    """获取全局预警记录器实例"""
    global _alert_recorder
    if _alert_recorder is None:
        _alert_recorder = AlertRecorder()
    return _alert_recorder

