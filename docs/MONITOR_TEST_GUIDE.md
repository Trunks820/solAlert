# Token监控系统测试指南

## 🧪 测试步骤

### 步骤1：安装依赖

```bash
# 安装新增的依赖（包含SOCKS代理支持）
pip install "httpx[socks]==0.25.2"
pip install "python-telegram-bot[socks]==20.7"

# 或重新安装所有依赖
pip install -r requirements.txt
```

**注意：** 如果本地测试需要使用代理，必须安装 `[socks]` 扩展。服务器部署时如果不需要代理，可以不安装。

### 步骤2：准备测试数据

在数据库中确保有监控配置（已有数据可跳过）：

```sql
-- 查看现有配置
SELECT * FROM token_monitor_config WHERE status='1' AND del_flag='0';

-- 如果没有，可以使用已有的测试数据（DEAR token）
-- ID=1, CA=3vreYfM6AxbEKdisdpuGpkPpxmtXn6Ema9unhxympump
```

### 步骤3：配置本地代理（如需要）

**本地测试需要代理访问Telegram和Jupiter API：**

编辑测试脚本 `scripts/test_token_monitor.py`：

```python
# 前几行代理配置
USE_PROXY = True  # 本地测试设置为True
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 1081  # 你的代理端口（默认1081）
```

**代理支持（httpx原生）：**
- ✅ httpx 原生支持 HTTP/SOCKS 代理
- ✅ 自动读取环境变量 `HTTP_PROXY` 和 `HTTPS_PROXY`
- ✅ 无需安装额外依赖
- ✅ Telegram Bot 也会通过代理连接

### 步骤4：运行测试脚本

```bash
python scripts/test_token_monitor.py
```

**测试内容：**
1. ✅ 数据库连接测试
2. ✅ Jupiter API调用测试（通过代理）
3. ✅ 触发逻辑判断测试
4. ✅ 完整监控流程测试（包括Telegram通知）

### 步骤5：测试实际运行

**注意：** 实际运行时需要在服务器上，本地测试请使用测试脚本。

```bash
# 只执行一次（测试）
python main.py --module token_monitor --once
```

**预期输出：**
```
🚀 开始执行Token监控任务
==========================================
📋 发现 X 个启用的监控配置
📊 开始批量获取 X 个Token数据...
进度: X/X | 成功: X
✅ 批量获取完成: X/X 成功
...
📊 监控任务完成
   配置总数: X
   获取数据: X/X
   触发监控: 0
   发送通知: 0
   耗时: X.XX秒
```

---

## 🔍 测试场景

### 场景1：基础功能测试（当前）

**目标：** 验证系统能正常运行

**步骤：**
1. 运行 `python scripts/test_token_monitor.py`
2. 所有测试应该通过

**预期结果：**
- ✅ 数据库连接正常
- ✅ Jupiter API能获取数据
- ✅ 触发逻辑判断正确
- ✅ 监控引擎能完整执行

---

### 场景2：触发通知测试

**目标：** 验证监控触发和通知功能

**步骤：**

1. **降低触发阈值**（让条件更容易满足）：

```sql
UPDATE token_monitor_config 
SET events_config = '{
  "priceChange": {
    "enabled": true,
    "risePercent": 1,
    "fallPercent": 1
  },
  "holders": {
    "enabled": true,
    "increasePercent": 1,
    "decreasePercent": 1
  },
  "volume": {
    "enabled": false,
    "increasePercent": null,
    "decreasePercent": null
  }
}'
WHERE id = 1;
```

2. **清空Redis冷却**（如果之前触发过）：

```bash
redis-cli -h 47.106.217.116 -a Wy1997@Kakarot
> KEYS alert:*
> DEL alert:xxxxx  # 删除对应的key
> exit
```

3. **执行监控**：

```bash
python main.py --module token_monitor --once
```

4. **检查Telegram**：
   - 查看群组 `-1002926135363` 是否收到通知
   - 通知格式应该包含Token信息和触发事件

5. **检查数据库日志**：

```sql
SELECT * FROM token_monitor_alert_log 
ORDER BY trigger_time DESC 
LIMIT 5;
```

**预期结果：**
- ✅ 触发监控
- ✅ 发送Telegram通知
- ✅ 记录到日志表
- ✅ 更新notification_count

---

### 场景3：冷却期测试

**目标：** 验证30分钟内不重复通知

**步骤：**

1. 触发一次通知（参考场景2）
2. 立即再次执行：

```bash
python main.py --module token_monitor --once
```

**预期结果：**
- ⏸️ 日志显示"在冷却期内，跳过通知"
- ❌ 不会发送Telegram通知
- ❌ 不会增加notification_count

---

### 场景4：定时执行测试

**目标：** 验证1分钟间隔运行

**步骤：**

```bash
# 启动定时监控（默认1分钟）
python main.py --module token_monitor
```

**预期结果：**
- 🔄 每1分钟执行一次
- 📝 持续输出日志
- ⏹️ Ctrl+C可以停止

**监控日志：**
```bash
# Windows (PowerShell)
Get-Content logs\solalert.log -Wait

# Linux/Mac
tail -f logs/solalert.log
```

---

### 场景5：多配置测试

**目标：** 验证批量监控

**步骤：**

1. **添加更多监控配置**：

```sql
-- 复制现有配置，改为不同的CA
INSERT INTO token_monitor_config 
(ca, token_name, token_symbol, alert_mode, events_config, 
 trigger_logic, notify_methods, status, timer_interval, remark)
SELECT 
  'CrFBNi3GHQ8boqivWUTbdwd1a3xwSMfkaxPzkocipump',  -- METAL
  'METAL', NULL, 'event',
  events_config, trigger_logic, notify_methods, 
  '1', 1, '测试配置2'
FROM token_monitor_config 
WHERE id = 1;
```

2. **执行监控**：

```bash
python main.py --module token_monitor --once
```

**预期结果：**
- 📋 显示"发现 2 个启用的监控配置"
- 🔄 批量获取2个Token数据
- ⚡ 根据实际情况触发监控

---

## ⚠️ 常见问题

### 1. 测试脚本报错：No module named 'aiohttp'

**解决：**
```bash
pip install aiohttp==3.9.1
```

### 2. Jupiter API调用失败

**原因：** 网络问题或API限流

**解决：**
- 检查网络连接
- 稍后重试
- 增加delay参数（已设置0.1秒）

### 3. Telegram通知未收到

**检查：**
1. Bot Token是否正确
2. Chat ID是否正确（-1002926135363）
3. Bot是否加入了该群组
4. 查看日志中的错误信息

### 4. Redis连接失败

**检查：**
```bash
redis-cli -h 47.106.217.116 -p 6379 -a Wy1997@Kakarot ping
```

应该返回 `PONG`

### 5. 数据库配置为空

**检查：**
```sql
SELECT * FROM token_monitor_config;
```

如果为空，执行：
```sql
INSERT INTO token_monitor_config VALUES 
(1, '3vreYfM6AxbEKdisdpuGpkPpxmtXn6Ema9unhxympump', 
 'DEAR', NULL, 'event', NULL, NULL, NULL, NULL, NULL, 
 '{"priceChange":{"enabled":true,"risePercent":10,"fallPercent":10},"holders":{"enabled":true,"increasePercent":30,"decreasePercent":20},"volume":{"enabled":false,"increasePercent":null,"decreasePercent":null}}', 
 'any', 'telegram', NULL, NULL, '1', NULL, 0, '0', '', 
 '2025-10-15 14:26:56', '', '2025-10-15 14:27:50', '测试');
```

---

## 📊 成功标准

**测试通过条件：**
1. ✅ 所有测试脚本通过
2. ✅ 能成功获取Jupiter API数据
3. ✅ 触发逻辑判断正确
4. ✅ Telegram通知发送成功
5. ✅ 数据库日志记录正常
6. ✅ Redis冷却机制生效
7. ✅ 定时任务稳定运行

---

## 🚀 下一步

测试通过后，可以：

1. **调整配置**：
   - 修改阈值为合理值
   - 添加更多监控Token
   - 调整监控间隔

2. **生产部署**：
   ```bash
   # 后台运行
   nohup python main.py --module token_monitor &
   
   # 或使用systemd（推荐）
   sudo systemctl start token-monitor
   ```

3. **监控运行状态**：
   ```bash
   # 查看日志
   tail -f logs/solalert.log
   
   # 查看触发历史
   SELECT * FROM token_monitor_alert_log 
   ORDER BY trigger_time DESC LIMIT 10;
   ```

---

**祝测试顺利！** 🎉

