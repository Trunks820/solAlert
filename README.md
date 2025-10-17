# solAlert - 多链Token监控预警系统

多链Token监控和提醒系统，支持 Solana 和 BSC 链的新币发射监控。

## 📋 功能特性

### 已实现功能 ✅
- ✅ **GMGN Pump监听器** (Solana) - 实时监听Telegram频道的新币发射消息
- ✅ **BONK数据采集器** (Solana) - 从Raydium API采集已毕业的Token数据
- ✅ **Four.meme采集器** (BSC) - 采集BSC链Four.meme平台的内盘和外盘Token
- ✅ **Twitter推送同步** - 自动同步Twitter账号推送配置（查缺补漏机制）
- ✅ **Token监控引擎** - 基于Jupiter API的实时价格/持币人/交易量监控
- ✅ **数据库存储** - MySQL持久化存储Token发射历史和监控配置
- ✅ **配置管理** - 基于环境变量的配置系统
- ✅ **日志系统** - 完整的日志记录和错误追踪
- ✅ **多模块架构** - 模块化设计，易于扩展
- ✅ **多链支持** - 支持Solana和BSC两条链

### 开发中功能 🚧
- 🚧 **更多监控维度** - K线、大额交易、链上数据监控
- 🚧 **更多链支持** - ETH、Base、Polygon等其他EVM链

## 🚀 快速开始

### 1. 环境准备

```bash
# 克隆项目
cd D:\workSpace\solAlert

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 到 `.env` 并修改配置：

```bash
# 数据库配置
DB_HOST=47.106.217.116
DB_PORT=3306
DB_USER=admin
DB_PASSWORD=your_password
DB_NAME=crypto_web3

# Redis配置
REDIS_HOST=47.106.217.116
REDIS_PORT=6379
REDIS_PASSWORD=your_password

# Telegram配置
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
TG_BOT_TOKEN=your_bot_token
```

### 3. 测试连接

```bash
# 测试数据库和Redis连接
python scripts/test_connection.py

# 测试BONK采集器
python scripts/test_bonk.py

# 测试Four.meme采集器
python scripts/test_fourmeme.py
```

### 4. 运行采集器

```bash
# 运行Pump监听器（实时监听）
python main.py --module pump_listener

# 运行Pump监听器（历史采集）
python main.py --module pump_listener --mode history

# 运行BONK采集器（默认60秒轮询）
python main.py --module bonk_collector

# 运行BONK采集器（自定义轮询间隔30秒）
python main.py --module bonk_collector --interval 30

# 运行Four.meme采集器（默认60秒轮询）
python main.py --module fourmeme_collector

# 运行Four.meme采集器（自定义轮询间隔120秒）
python main.py --module fourmeme_collector --interval 120

# 运行Twitter推送同步任务（默认10分钟间隔）
python main.py --module twitter_push_sync

# 运行Twitter推送同步任务（自定义5分钟间隔）
python main.py --module twitter_push_sync --interval 300

# 运行Token监控引擎（默认1分钟间隔）
python main.py --module token_monitor

# 运行Token监控引擎（只执行一次）
python main.py --module token_monitor --once

# 运行Token监控引擎（自定义5分钟间隔）
python main.py --module token_monitor --interval 5

# 同时运行所有采集器
python main.py --module all
```

## 📁 项目结构

```
solAlert/
├── src/solalert/              # 核心代码
│   ├── core/                  # 核心模块
│   │   ├── config.py          # 配置管理
│   │   ├── database.py        # 数据库连接
│   │   ├── redis_client.py    # Redis客户端
│   │   └── logger.py          # 日志系统
│   ├── models/                # 数据模型
│   │   └── schemas.py         # Pydantic模型定义
│   ├── repositories/          # 数据访问层
│   │   └── token_repo.py      # Token数据仓库
│   ├── collectors/            # 数据采集器
│   │   ├── base.py            # 采集器基类
│   │   ├── pump_listener.py   # Pump监听器 (Solana)
│   │   ├── bonk_collector.py  # BONK采集器 (Solana)
│   │   └── fourmeme_collector.py  # Four.meme采集器 (BSC)
│   ├── monitor/               # 监控引擎
│   │   ├── jupiter_api.py     # Jupiter API封装
│   │   ├── trigger_logic.py   # 触发逻辑判断
│   │   ├── notifiers.py       # 通知服务
│   │   └── token_monitor.py   # 核心监控引擎
│   ├── tasks/                 # 定时任务
│   │   └── twitter_push_sync.py # Twitter推送同步
│   └── notifiers/             # 通知服务
│       ├── base.py            # 通知基类
│       ├── telegram.py        # Telegram通知
│       ├── wechat.py          # 微信通知
│       └── manager.py         # 通知管理器
├── scripts/                   # 工具脚本
│   ├── test_connection.py     # 测试数据库连接
│   └── test_bonk.py          # 测试BONK采集器
├── docs/                      # 文档
│   └── ARCHITECTURE.md        # 架构设计文档
├── logs/                      # 日志文件
├── main.py                    # 主入口
├── requirements.txt           # 依赖包
├── .env.example              # 环境变量模板
└── README.md                 # 本文件
```

## 🔧 核心模块说明

### Pump监听器 (pump_listener.py)
- 监听Telegram GMGN频道的新币发射消息
- 解析消息并提取Token信息（CA、名称、符号、Twitter等）
- 自动入库到MySQL数据库
- 支持历史消息采集和实时监听两种模式

### BONK采集器 (bonk_collector.py)
- 从Raydium API获取letsbonk.fun平台已毕业的Token
- 自动采集历史数据（默认5页，约500条）
- 定时轮询新Token（默认60秒）
- 自动去重，只保存新Token

### Four.meme采集器 (fourmeme_collector.py)
- 从Four.meme API采集BSC链上的Meme Token
- **同时采集内盘和外盘Token**：
  - 内盘：未上Pancakeswap的Token（筹款阶段）
  - 外盘：已毕业到Pancakeswap交易的Token
- **智能全量采集**：首次运行时持续采集所有历史数据（直到遇到重复或无数据）
- 定时轮询新Token（默认60秒）
- 自动提取Twitter链接和市值信息
- 自动去重，只保存新Token
- 连续3页无新数据自动停止采集

### Twitter推送同步 (twitter_push_sync.py)
- 定时扫描数据库中的推送配置变化
- 自动调用Twitter API同步推送设置
- 查缺补漏机制（默认10分钟间隔）
- 智能重试，避免重复调用

### Token监控引擎 (token_monitor.py)
- **实时监控**：基于Jupiter API获取Token 5分钟统计数据
- **多维度监控**：
  - 📈 价格涨跌幅（支持上涨/下跌双向阈值）
  - 👥 持币人数变化（支持增加/减少双向阈值）
  - 💰 交易量变化（支持增加/减少双向阈值）
- **灵活触发逻辑**：
  - `any`：任一条件满足即触发
  - `all`：所有条件同时满足才触发
- **智能防重复**：Redis冷却机制，30分钟内不重复通知
- **通知方式**：Telegram Bot / 企业微信
- **完整日志**：记录触发历史、通知状态到数据库

### 数据仓库 (token_repo.py)
- 封装所有数据库操作
- 支持Pump和BONK数据的插入和更新
- 提供Token查询、统计等方法

### 通知管理器 (notifiers/)
- 支持Telegram Bot推送
- 支持微信API推送
- 统一的消息格式化接口

## 📊 数据库表结构

### token_launch_history (Token发射历史)
```sql
- id: 主键
- ca: 合约地址（唯一索引）
- token_name: Token名称
- token_symbol: Token符号
- twitter_url: Twitter链接
- source: 数据来源（pump/bonk）
- launch_time: 发射时间
- highest_market_cap: 历史最高市值
- tg_msg_id: TG消息ID
- created_at: 创建时间
```

### token_monitor_config (Token监控配置)
```sql
- id: 主键
- ca: Token合约地址
- token_name: Token名称
- token_symbol: Token符号
- alert_mode: 监控模式（event）
- events_config: 事件配置JSON（涨跌幅/持币人数/交易量）
- trigger_logic: 触发逻辑（any/all）
- notify_methods: 通知方式（telegram,wechat）
- timer_interval: 监控间隔（分钟）
- status: 启用状态（0停用/1启用）
- last_notification_time: 上次通知时间
- notification_count: 累计通知次数
```

### token_monitor_alert_log (监控触发日志)
```sql
- id: 主键
- config_id: 关联监控配置ID
- ca: Token合约地址
- trigger_time: 触发时间
- trigger_events: 触发事件详情（JSON）
- stats_data: Jupiter API统计数据（JSON）
- notify_methods: 通知方式
- notify_status: 通知状态（success/failed/partial）
```

## 🎯 使用场景

### 场景1：实时监控新币发射
```bash
# 同时运行Pump和BONK采集器
python main.py --module all
```

### 场景2：采集历史数据
```bash
# 采集Pump历史数据
python main.py --module pump_listener --mode history

# BONK采集器启动时会自动采集历史数据
python main.py --module bonk_collector
```

### 场景3：只监控BONK平台
```bash
# 每30秒检查一次新Token
python main.py --module bonk_collector --interval 30
```

### 场景4：Token价格监控
```bash
# 启动监控引擎（默认1分钟检查一次）
python main.py --module token_monitor

# 自定义检查间隔（5分钟）
python main.py --module token_monitor --interval 5

# 只执行一次（测试用）
python main.py --module token_monitor --once
```

**监控配置示例（在数据库中配置）：**
```json
{
  "priceChange": {
    "enabled": true,
    "risePercent": 10,    // 价格上涨 ≥10% 触发
    "fallPercent": 10     // 价格下跌 ≥10% 触发
  },
  "holders": {
    "enabled": true,
    "increasePercent": 30,  // 持币人数增加 ≥30% 触发
    "decreasePercent": 20   // 持币人数减少 ≥20% 触发
  },
  "volume": {
    "enabled": false,
    "increasePercent": null,
    "decreasePercent": null
  }
}
```

## 📝 命令行参数

### main.py 参数说明
```bash
--module       选择运行模块
               - pump_listener: Pump监听器
               - bonk_collector: BONK采集器
               - twitter_push_sync: Twitter推送同步（查缺补漏）
               - token_monitor: Token监控引擎
               - all: 所有服务

--mode         Pump监听器模式
               - listen: 实时监听（默认）
               - history: 历史采集

--interval     采集器/任务轮询间隔
               - BONK采集器: 秒（默认60）
               - Twitter推送同步: 秒（默认600=10分钟）
               - Token监控: 分钟（默认1）

--once         只执行一次（适用于twitter_push_sync和token_monitor）

--no-check     跳过启动时的依赖检查
```

## 🔍 测试和调试

### 测试数据库连接
```bash
python scripts/test_connection.py
```

### 测试BONK采集器
```bash
python scripts/test_bonk.py
```

### 查看日志
```bash
# 日志文件位置
logs/solalert.log

# Windows下实时查看日志（需要安装Git Bash或WSL）
tail -f logs/solalert.log
```

## 🛠️ 开发指南

### 添加新的采集器
1. 在 `src/solalert/collectors/` 创建新文件
2. 继承 `BaseCollector` 类
3. 实现 `start()` 和 `stop()` 方法
4. 在 `main.py` 中注册新模块

### 添加新的通知渠道
1. 在 `src/solalert/notifiers/` 创建新文件
2. 继承 `BaseNotifier` 类
3. 实现 `send()` 方法
4. 在 `manager.py` 中集成

## ⚠️ 注意事项

1. **Telegram代理设置**：如果在国内环境使用Pump监听器，需要配置代理
2. **API限流**：
   - BONK采集器建议轮询间隔不低于30秒
   - Jupiter API单个查询，建议间隔≥0.1秒
   - Token监控默认1分钟间隔，可根据实际情况调整
3. **数据库连接**：确保数据库服务器允许远程连接
4. **时区问题**：所有时间戳都会转换为UTC时间存储
5. **Redis依赖**：Token监控引擎需要Redis支持防重复功能
6. **监控配置**：需要在数据库中配置监控规则（通过前端或直接SQL）

## 📞 问题反馈

如有问题，请检查：
1. 日志文件 `logs/solalert.log`
2. 数据库连接是否正常
3. 环境变量配置是否正确

## 📅 更新日志

### v0.2.0 (2025-10-16) 🎉
- ✅ **新增Token监控引擎**
  - 基于Jupiter API实时监控
  - 支持价格、持币人数、交易量三维监控
  - any/all灵活触发逻辑
  - Redis防重复机制
  - Telegram自动通知
- ✅ 完成Twitter推送同步任务
- ✅ 优化数据库架构

### v0.1.0 (2025-10-01)
- ✅ 完成Pump监听器
- ✅ 完成BONK采集器
- ✅ 完成基础架构搭建
- ✅ 完成数据库存储模块
- ✅ 完成通知管理器框架

## 🚀 下一步计划

1. ✅ ~~实现监控引擎（价格、持币人数监控）~~ **已完成**
2. 完善监控引擎（增加更多监控维度）
3. 开发Web API接口
4. 开发前端界面
5. 增加更多数据源集成

---

**Happy Monitoring! 🎉**
