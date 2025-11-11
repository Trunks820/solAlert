# BSC交易丢失问题调查报告

## 问题描述
用户反馈BSC上的一笔交易没有被系统抓到，并且该token的日志一笔都没有。

## 交易信息
- **交易Hash**: `0x9c24672099db2f5ca3dbe54ecd26b02d09d6cd70b2c5c08ed3782fb9475618e8`
- **区块号**: 67807610
- **Pair地址**: `0xff0ea6a9af135434629581ed1c0432d36623070c`
- **Token地址**: `0xfbfa61e85dcbcee6d3e895e90f75b3605d054444`
- **Token名称**: Beyond Borders
- **交易对**: WBNB/Beyond Borders

## 交易详情分析

### Swap事件数据
```
Amount0In:  511960477060000000 (0.512 BNB)
Amount1In:  0
Amount0Out: 0
Amount1Out: 1001865034090377296170397

Token0: 0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c (WBNB)
Token1: 0xfbfa61e85dcbcee6d3e895e90f75b3605d054444 (Beyond Borders)

交易类型: 买入（用WBNB买Beyond Borders）
```

### USD价值计算
```
WBNB数量: 0.512 BNB
WBNB价格: $978.15 (从USDT/WBNB池获取)
USD价值: $500.77
```

### 第一层过滤检查
```
配置的最小金额（外盘）: $400
实际USD价值: $500.77
结论: ✅ 应该通过第一层过滤
```

## 数据库查询结果

### 告警记录
- `monitor_alert_log`表: **完全没有任何记录**（不仅是这个token，所有token都没有）
- `token_monitor_alert_log`表: 未查询到该token的记录

### 日志文件
- `logs/bsc_websocket.log`: 未找到该token或交易的相关日志

## 问题根因分析

### 可能的原因

1. **系统未运行或刚启动** ⚠️ 最可能
   - `monitor_alert_log`表完全没有记录，说明系统可能还没有产生过任何告警
   - 需要确认：系统是什么时候启动的？交易发生时系统是否在运行？

2. **WBNB价格获取失败** ⚠️ 需要验证
   - 系统使用Gate.io API获取WBNB价格：
     ```python
     resp = self.session.get(
         'https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BNB_USDT',
         timeout=3,
         verify=False
     )
     ```
   - 如果API失败或返回0，USD价值计算会错误
   - 系统有价格缓存（5分钟TTL），但初始值是$600，如果API一直失败会使用默认值
   - **问题**: 0.512 BNB × $600 = $307.2 < $400，会被第一层过滤拦截！

3. **WebSocket订阅配置问题** ⚠️ 需要检查
   - 需要确认WebSocket是否订阅了PancakeSwap V2的Swap事件
   - Topic: `0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822`

4. **fourmeme检查失败** ⚠️ 外盘需要fourmeme验证
   - 外盘交易需要通过fourmeme检查才会进入第二层
   - 如果DBotX API调用失败或返回非fourmeme，会直接跳过

## 关键代码位置

### 第一层过滤
`src/solalert/monitor/bsc_websocket_monitor.py:1465-1468`
```python
def first_layer_filter(self, usd_value: float, is_internal: bool) -> bool:
    """第一层过滤：金额"""
    threshold = self.min_amount_internal if is_internal else self.min_amount_external
    return usd_value >= threshold
```

### USD价值计算（外盘）
`src/solalert/monitor/bsc_websocket_monitor.py:2339-2344`
```python
quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
if quote_token == self.WBNB:
    wbnb_price = await asyncio.to_thread(self.get_wbnb_price)
    usd_value = float(quote_value) * wbnb_price
else:
    usd_value = float(quote_value)
```

### WBNB价格获取
`src/solalert/monitor/bsc_websocket_monitor.py:1134-1164`
- 使用Gate.io API
- 缓存5分钟
- 默认值: $600

## 验证步骤

### 1. 检查系统运行状态
```bash
# 检查进程
ps aux | grep bsc_websocket_monitor

# 检查最近的日志
tail -100 logs/bsc_websocket.log
```

### 2. 验证WBNB价格获取
```bash
# 测试Gate.io API
curl 'https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BNB_USDT'
```

### 3. 检查WebSocket订阅
- 确认订阅的topics包含PancakeSwap V2 Swap事件
- 确认WebSocket连接正常

### 4. 检查fourmeme验证
- Beyond Borders是否在fourmeme白名单中？
- DBotX API是否正常响应？

## 推荐修复方案

### 立即修复
1. **增加WBNB价格获取日志**
   - 记录每次价格更新的时间和价格
   - 记录API失败情况

2. **增加第一层过滤详细日志**
   - 记录被过滤的交易（包括USD价值、阈值）
   - 帮助诊断为什么交易被过滤

3. **增加告警记录**
   - 确保所有通过第一层的交易都被记录（即使没有发送告警）

### 长期改进
1. **多源价格获取**
   - 添加CoinGecko、CoinMarketCap等备用价格源
   - 价格异常检测（如果价格偏差>10%，使用备用源）

2. **交易回补机制**
   - 系统重启后，回补丢失的区块交易
   - 需要记录最后处理的区块号

3. **监控告警**
   - WBNB价格获取失败告警
   - WebSocket断线告警
   - 长时间无交易告警（可能是订阅失败）

## 实际检查结果

### 系统运行状态 ❌ **关键问题**
```bash
$ ps aux | grep bsc_websocket
(无进程)

$ ls -lh logs/bsc_websocket.log
日志文件不存在
```

**结论：BSC监控系统没有在运行！**

### 交易时间
- 区块号：67807610
- 时间：2025-11-11 10:25:16
- **距离现在：刚刚发生（今天）**

## 根本原因 🔴

**BSC WebSocket监控系统没有运行，因此无法捕获任何交易！**

这解释了所有现象：
1. ✅ 数据库中没有任何告警记录（monitor_alert_log表为空）
2. ✅ 日志文件不存在
3. ✅ 该token的所有交易都没有被抓到

## 解决方案

### 1. 立即启动BSC监控系统

```bash
cd /workspace

# 方法1：直接启动
python3 start_bsc_websocket_monitor.py

# 方法2：后台运行（推荐）
nohup python3 start_bsc_websocket_monitor.py > logs/bsc_websocket.log 2>&1 &

# 方法3：使用screen（推荐）
screen -S bsc_monitor
python3 start_bsc_websocket_monitor.py
# 按 Ctrl+A, D 分离会话
```

### 2. 验证系统正常运行

```bash
# 检查进程
ps aux | grep bsc_websocket

# 查看日志
tail -f logs/bsc_websocket.log

# 应该看到类似以下输出：
# ✓ 订阅 Fourmeme Router 所有事件（内盘）
# ✓ 订阅 Fourmeme Proxy 所有事件（内盘）
# ✓ 订阅 PancakeV2 Swap 事件（外盘）
# ⏳ 等待链上交易...
```

### 3. 回补历史交易（可选）

如果需要回补丢失的交易，可以使用以下脚本：

```python
# backfill_bsc_transactions.py
import asyncio
from src.solalert.monitor.bsc_websocket_monitor import BSCWebSocketMonitor

async def backfill_blocks(start_block, end_block):
    """回补指定区块范围的交易"""
    monitor = BSCWebSocketMonitor(
        ws_url="wss://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e",
        rpc_url="https://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e",
        enable_telegram=True
    )
    
    # TODO: 实现区块回补逻辑
    # 1. 通过RPC获取指定区块范围的所有交易
    # 2. 过滤出PancakeSwap V2 Swap事件
    # 3. 调用monitor的处理逻辑
    pass

if __name__ == "__main__":
    # 回补今天的交易
    asyncio.run(backfill_blocks(67800000, 67810000))
```

### 4. 设置进程监控（防止再次停止）

```bash
# 安装supervisor
sudo apt-get install supervisor

# 创建配置文件
sudo nano /etc/supervisor/conf.d/bsc_monitor.conf
```

配置内容：
```ini
[program:bsc_monitor]
command=/usr/bin/python3 /workspace/start_bsc_websocket_monitor.py
directory=/workspace
autostart=true
autorestart=true
stderr_logfile=/workspace/logs/bsc_monitor_err.log
stdout_logfile=/workspace/logs/bsc_monitor_out.log
user=ubuntu
environment=HOME="/home/ubuntu",USER="ubuntu"
```

```bash
# 重启supervisor
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start bsc_monitor

# 查看状态
sudo supervisorctl status
```

## 总结

**问题原因**：BSC监控系统没有运行，导致无法捕获任何链上交易。

**解决方法**：
1. ✅ 立即启动监控系统
2. ✅ 验证系统正常工作
3. ✅ 设置自动重启机制（supervisor）
4. ⚠️ 如需要，编写回补脚本抓取历史交易

**预防措施**：
1. 添加监控告警（如果系统停止运行，发送通知）
2. 使用supervisor等进程管理工具自动重启
3. 添加健康检查接口，定期检测系统状态
