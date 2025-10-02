# solAlert - Solana Token 监控预警系统

Solana链上Token监控和提醒系统，支持GMGN Pump和BONK平台的新币发射监控。

## 📋 功能特性

### 已实现功能 ✅
- ✅ **GMGN Pump监听器** - 实时监听Telegram频道的新币发射消息
- ✅ **BONK数据采集器** - 从Raydium API采集已毕业的Token数据
- ✅ **数据库存储** - MySQL持久化存储Token发射历史
- ✅ **配置管理** - 基于环境变量的配置系统
- ✅ **日志系统** - 完整的日志记录和错误追踪
- ✅ **多模块架构** - 模块化设计，易于扩展

### 开发中功能 🚧
- 🚧 **行情数据采集** - 定时采集Token实时行情
- 🚧 **监控引擎** - 市值、价格、持仓等多维度监控
- 🚧 **多渠道通知** - Telegram、微信、Web推送
- 🚧 **API服务** - RESTful API接口
- 🚧 **Web前端** - 数据展示和配置管理界面

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
│   │   ├── pump_listener.py   # Pump监听器
│   │   └── bonk_collector.py  # BONK采集器
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

## 📝 命令行参数

### main.py 参数说明
```bash
--module       选择运行模块
               - pump_listener: Pump监听器
               - bonk_collector: BONK采集器
               - monitor: 监控引擎（开发中）
               - all: 所有服务

--mode         Pump监听器模式
               - listen: 实时监听（默认）
               - history: 历史采集

--interval     BONK采集器轮询间隔（秒，默认60）

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
2. **API限流**：BONK采集器建议轮询间隔不低于30秒，避免触发API限流
3. **数据库连接**：确保数据库服务器允许远程连接
4. **时区问题**：所有时间戳都会转换为UTC时间存储

## 📞 问题反馈

如有问题，请检查：
1. 日志文件 `logs/solalert.log`
2. 数据库连接是否正常
3. 环境变量配置是否正确

## 📅 更新日志

### v0.1.0 (2025-10-01)
- ✅ 完成Pump监听器
- ✅ 完成BONK采集器
- ✅ 完成基础架构搭建
- ✅ 完成数据库存储模块
- ✅ 完成通知管理器框架

## 🚀 下一步计划

1. 实现行情数据采集器
2. 实现监控引擎（市值、价格监控）
3. 完善通知功能（自动推送）
4. 开发Web API接口
5. 开发前端界面

---

**Happy Monitoring! 🎉**
