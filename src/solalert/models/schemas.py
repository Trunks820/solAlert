"""
数据模型定义
使用 Pydantic 定义数据结构
"""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


# ==================== Token 相关模型 ====================

class TokenLaunchHistory(BaseModel):
    """Token发射历史数据模型"""
    id: Optional[int] = None
    ca: str = Field(..., description="合约地址")
    token_name: Optional[str] = Field(None, description="Token名称")
    token_symbol: Optional[str] = Field(None, description="Token符号")
    twitter_url: Optional[str] = Field(None, description="Twitter链接")
    source: str = Field(..., description="数据来源: pump, bonk")
    launch_time: datetime = Field(..., description="发射时间(北京时间)")
    highest_market_cap: Optional[int] = Field(None, description="历史最高市值(USD)")
    tg_msg_id: Optional[str] = Field(None, description="TG消息ID")
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class TokenMarketData(BaseModel):
    """Token实时行情数据模型"""
    id: Optional[int] = None
    ca: str = Field(..., description="合约地址")
    price: Optional[float] = Field(None, description="当前价格")
    market_cap: Optional[int] = Field(None, description="当前市值(USD)")
    volume_24h: Optional[int] = Field(None, description="24小时交易量")
    holder_count: Optional[int] = Field(None, description="持有人数")
    top10_holder_percentage: Optional[float] = Field(None, description="前十持有人占比(%)")
    price_change_1m: Optional[float] = Field(None, description="1分钟涨幅(%)")
    price_change_5m: Optional[float] = Field(None, description="5分钟涨幅(%)")
    price_change_1h: Optional[float] = Field(None, description="1小时涨幅(%)")
    data_source: Optional[str] = Field(None, description="数据来源")
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


# ==================== 监控相关模型 ====================

class MonitorConfig(BaseModel):
    """监控配置模型"""
    id: Optional[int] = None
    ca: str = Field(..., description="合约地址")
    user_id: Optional[str] = Field(None, description="用户ID")
    config_name: Optional[str] = Field(None, description="配置名称")
    
    # 市值监控
    market_cap_min: Optional[int] = Field(None, description="市值下限(USD)")
    market_cap_max: Optional[int] = Field(None, description="市值上限(USD)")
    
    # 涨幅监控
    price_change_1m: Optional[float] = Field(None, description="1分钟价格涨幅阈值(%)")
    price_change_5m: Optional[float] = Field(None, description="5分钟价格涨幅阈值(%)")
    price_change_1h: Optional[float] = Field(None, description="1小时价格涨幅阈值(%)")
    
    # 持有人监控
    holder_change_1m: Optional[int] = Field(None, description="1分钟持有人变化阈值")
    holder_change_5m: Optional[int] = Field(None, description="5分钟持有人变化阈值")
    holder_change_1h: Optional[int] = Field(None, description="1小时持有人变化阈值")
    
    # 前十持仓监控
    top10_change_threshold: Optional[float] = Field(None, description="前十持仓占比变化阈值(%)")
    
    # 推文异动监控
    twitter_silence_hours: Optional[int] = Field(None, description="推文静默时间(小时)")
    twitter_min_followers: int = Field(100000, description="推文异动最小粉丝数阈值")
    
    # 定时监控
    schedule_interval_minutes: int = Field(5, description="定时监控间隔(分钟)")
    
    # 通知设置
    notify_channels: Optional[List[str]] = Field(None, description="通知渠道 [tg,wechat,web]")
    cooldown_minutes: int = Field(15, description="冷却时间(分钟)")
    
    # 状态
    is_active: bool = Field(True, description="是否启用")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class AlertRecord(BaseModel):
    """提醒记录模型"""
    id: Optional[int] = None
    ca: str = Field(..., description="合约地址")
    config_id: int = Field(..., description="监控配置ID")
    alert_type: str = Field(..., description="提醒类型")
    alert_condition: Optional[dict] = Field(None, description="触发条件详情")
    market_data_snapshot: Optional[dict] = Field(None, description="触发时市场数据快照")
    
    # 通知状态
    notify_tg_status: int = Field(0, description="TG通知状态 0-未发送 1-成功 2-失败")
    notify_wechat_status: int = Field(0, description="微信通知状态")
    notify_web_status: int = Field(0, description="Web通知状态")
    
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


# ==================== API 请求/响应模型 ====================

class TokenListRequest(BaseModel):
    """Token列表查询请求"""
    source: Optional[str] = Field(None, description="数据来源: pump|bonk|all")
    market_cap_min: Optional[int] = Field(None, description="最小市值")
    market_cap_max: Optional[int] = Field(None, description="最大市值")
    time_range: Optional[str] = Field("24h", description="时间范围: 24h|7d|custom")
    start_time: Optional[datetime] = Field(None, description="自定义开始时间")
    end_time: Optional[datetime] = Field(None, description="自定义结束时间")
    page: int = Field(1, ge=1, description="页码")
    size: int = Field(20, ge=1, le=100, description="每页大小")


class TokenListResponse(BaseModel):
    """Token列表响应"""
    total: int
    list: List[TokenLaunchHistory]


class ApiResponse(BaseModel):
    """统一API响应"""
    code: int = Field(200, description="响应码")
    message: str = Field("success", description="响应消息")
    data: Optional[any] = None


# ==================== 监控告警模型 ====================

class PumpAlert(BaseModel):
    """Pump发射提醒"""
    ca: str
    token_name: Optional[str]
    token_symbol: Optional[str]
    market_cap: Optional[str]
    price: Optional[str]
    holders: Optional[int]
    twitter_url: Optional[str]
    launch_time: str
    
    def format_message(self) -> str:
        """格式化为Telegram HTML消息"""
        lines = [
            "<b>🚀 新币发射提醒</b>",
            "",
            f"💰 代币: {self.token_name} ({self.token_symbol})",
        ]
        
        if self.market_cap:
            lines.append(f"📊 市值: {self.market_cap}")
        if self.price:
            lines.append(f"💵 价格: {self.price}")
        if self.holders:
            lines.append(f"👥 持有人: {self.holders}")
        
        lines.append(f"\n合约地址: <code>{self.ca}</code>")
        lines.append(f"\n⏰ 发射时间: {self.launch_time}")
        lines.append(f"📡 播报时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(lines)

