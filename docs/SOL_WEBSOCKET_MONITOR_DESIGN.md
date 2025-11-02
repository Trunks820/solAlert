# SOL Token WebSocket 监控系统 - 技术方案（方案B）

> 版本：v1.4  
> 日期：2025-11-02  
> 状态：✅ 开发完成 - 生产环境运行中

---

## 📋 目录

1. [系统概述](#系统概述)
2. [数据统计](#数据统计)
3. [数据库设计](#数据库设计)
4. [核心流程](#核心流程)
5. [代码架构](#代码架构)
6. [配置说明](#配置说明)
7. [监控告警](#监控告警)
8. [部署方案](#部署方案)
9. [后续优化](#后续优化)

---

## 系统概述

### 背景
基于 DBotX WebSocket API 实现 Solana Token 的实时监控，根据模板配置对满足条件的 Token 进行告警推送。

### 核心特性
- ✅ 支持模板化配置（按市值区间 + Twitter类型）
- ✅ 多连接并发订阅（3-4个WebSocket连接）
- ✅ 智能分配订阅池
- ✅ 持久化管理（数据库存储）
- ✅ 动态调整订阅
- ✅ 完善的监控和日志

### 技术栈
- **语言**: Python 3.10+
- **WebSocket**: websockets 库
- **数据库**: MySQL 8.0+
- **API**: DBotX Data API
- **消息推送**: Telegram Bot API

---

## 数据统计

### 当前数据量
基于 `2025-10-31` 实际初始化结果：

| 配置 | 市值区间 | CA数量 | 批次数 | 优先级 |
|------|---------|--------|--------|--------|
| 配置4 | ≥ $10M | 107个 | 2批 | 最高 |
| 配置3 | $1M-$10M | 540个 | 6批 | 高 |
| 配置2 | $500K-$1M | 607个 | 7批 | 中 |
| 配置1 | $300K-$500K | 645个 | 7批 | 低 |
| **总计** | **≥ $300K** | **1,899个** | **20批** | - |

**实际数据统计**：
- ✅ 已入库CA数量：**1,899个**（获取到pair地址）
- ❌ 失败CA数量：4个（确实没有交易池）
- 📦 批次分配：20个批次（batch_id: 1-20）
- 🔢 每批CA数量：约95个/批（最后一批37个）

**筛选条件**：
- 所有配置要求 `has_twitter='profile'`（Twitter必须是profile类型）
- Profile类型Twitter总数：54,361 个
- 代币来源：`source IN ('pump', 'bonk')`
- 市值要求：≥ $300,000

**API限制**：
- 每批最多订阅 99 个CA（DBotX API限制）
- 实际每批约95个CA，留有余量

---

## 数据库设计

### 1. sol_ws_batch_pool（批次池表）✅ 已创建

**用途**：存储预处理好的20个批次的CA数据，每批次最多99个CA

```sql
CREATE TABLE `sol_ws_batch_pool` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  `batch_id` INT NOT NULL COMMENT '批次ID(1-20)',
  `ca` VARCHAR(100) NOT NULL COMMENT 'Token CA地址',
  `token_symbol` VARCHAR(50) COMMENT 'Token符号',
  `token_name` VARCHAR(200) COMMENT 'Token名称',
  `pair_address` VARCHAR(100) COMMENT 'Pair地址',
  `market_cap` DECIMAL(20,2) NOT NULL COMMENT '历史最高市值',
  `twitter_url` VARCHAR(500) COMMENT 'Twitter URL',
  `template_id` INT NOT NULL COMMENT '模板配置ID',
  `template_name` VARCHAR(100) COMMENT '模板名称',
  `time_interval` VARCHAR(10) NOT NULL COMMENT '检查间隔(1m/5m/1h)',
  `events_config` TEXT COMMENT '监控指标配置（JSON）',
  `trigger_logic` VARCHAR(10) DEFAULT 'any' COMMENT '触发逻辑(any/all)',
  `priority` INT NOT NULL COMMENT '优先级(市值排序，越大越高)',
  `sort_order` INT NOT NULL COMMENT '批次内排序',
  `is_active` TINYINT DEFAULT 1 COMMENT '是否激活',
  `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  
  UNIQUE KEY `uk_ca` (`ca`),
  KEY `idx_batch` (`batch_id`, `is_active`),
  KEY `idx_batch_sort` (`batch_id`, `sort_order`),
  KEY `idx_priority` (`priority` DESC),
  KEY `idx_pair` (`pair_address`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL WebSocket批次池（预处理好的20个批次）';
```

**字段说明**：
- `batch_id`: 批次编号（1-20），每个批次对应一个WebSocket订阅请求
- `priority`: 基于历史最高市值的优先级，值越大越优先
- `sort_order`: 批次内的排序号（0-98）
- `events_config`: 从模板配置复制过来，格式如：
  ```json
  {
    "priceChange": {"enabled": true, "risePercent": 10},
    "volume": {"enabled": true, "threshold": 30000}
  }
  ```

**分批策略**：
- 按历史最高市值降序排序所有CA
- 每99个CA一批
- 预计1903个CA分为20批（最后一批约37个）

---

### 2. sol_ws_alert_log（告警记录表）✅ 已创建

**用途**：记录所有SOL WebSocket监控产生的告警

```sql
CREATE TABLE `sol_ws_alert_log` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
  `batch_id` INT NOT NULL COMMENT '批次ID',
  `ca` VARCHAR(100) NOT NULL COMMENT 'Token CA地址',
  `token_symbol` VARCHAR(50) COMMENT 'Token符号',
  `token_name` VARCHAR(200) COMMENT 'Token名称',
  `pair_address` VARCHAR(100) COMMENT 'Pair地址',
  
  -- 模板信息
  `template_id` INT COMMENT '模板配置ID',
  `template_name` VARCHAR(100) COMMENT '模板名称',
  
  -- 触发数据
  `price` DECIMAL(30,15) COMMENT '触发时价格',
  `market_cap` DECIMAL(20,2) COMMENT '触发时市值',
  `price_change` DECIMAL(10,4) COMMENT '价格变化百分比',
  `price_change_1m` DECIMAL(10,4) COMMENT '1分钟价格变化',
  `price_change_5m` DECIMAL(10,4) COMMENT '5分钟价格变化',
  `price_change_1h` DECIMAL(10,4) COMMENT '1小时价格变化',
  `volume_1h` DECIMAL(20,2) COMMENT '1小时总交易量',
  `buy_volume_1h` DECIMAL(20,2) COMMENT '1小时买入量',
  `sell_volume_1h` DECIMAL(20,2) COMMENT '1小时卖出量',
  `txs_1h` INT COMMENT '1小时交易次数',
  `buy_txs_1h` INT COMMENT '1小时买入次数',
  `sell_txs_1h` INT COMMENT '1小时卖出次数',
  `top10_percent` DECIMAL(5,2) COMMENT 'TOP10持仓比例',
  
  -- 触发原因
  `trigger_reasons` TEXT COMMENT '触发原因（JSON数组）',
  `trigger_time_interval` VARCHAR(10) COMMENT '触发的时间间隔(1m/5m/1h)',
  `trigger_logic` VARCHAR(10) COMMENT '触发逻辑(any/all)',
  
  -- 推送信息
  `alert_message` TEXT COMMENT '告警消息内容',
  `telegram_sent` TINYINT DEFAULT 0 COMMENT '是否已推送Telegram(1=是,0=否)',
  `telegram_success` TINYINT DEFAULT 0 COMMENT 'Telegram推送是否成功(1=成功,0=失败)',
  `telegram_message_id` VARCHAR(100) COMMENT 'Telegram消息ID',
  `telegram_error` TEXT COMMENT 'Telegram推送错误信息',
  `wechat_sent` TINYINT DEFAULT 0 COMMENT '是否已推送微信(1=是,0=否)',
  `wechat_success` TINYINT DEFAULT 0 COMMENT '微信推送是否成功(1=成功,0=失败)',
  `wechat_message_id` VARCHAR(100) COMMENT '微信消息ID',
  `wechat_error` TEXT COMMENT '微信推送错误信息',
  
  -- 统计信息
  `response_time_ms` INT COMMENT '处理响应时间（毫秒）',
  `alert_time` DATETIME NOT NULL COMMENT '告警时间',
  `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  
  KEY `idx_batch` (`batch_id`),
  KEY `idx_ca` (`ca`),
  KEY `idx_alert_time` (`alert_time`),
  KEY `idx_template` (`template_id`),
  KEY `idx_batch_time` (`batch_id`, `alert_time`),
  KEY `idx_ca_time` (`ca`, `alert_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL WebSocket告警记录';
```

**用途说明**：
- 记录每次告警的详细数据
- 支持Telegram和微信双渠道推送
- 可回溯每个批次的告警频率
- 支持后续数据分析和优化

---

### 3. sol_ws_batch_stats（批次统计表）✅ 已创建

**用途**：统计每个批次的运行状态和告警情况

```sql
CREATE TABLE `sol_ws_batch_stats` (
  `batch_id` INT PRIMARY KEY COMMENT '批次ID(1-20)',
  `ca_count` INT DEFAULT 0 COMMENT 'CA数量',
  `subscribed_count` INT DEFAULT 0 COMMENT '已订阅数量',
  `alert_count` INT DEFAULT 0 COMMENT '累计告警次数',
  `last_alert_time` DATETIME COMMENT '最后告警时间',
  `total_messages` BIGINT DEFAULT 0 COMMENT '收到消息总数',
  `avg_response_time_ms` INT COMMENT '平均响应时间',
  `error_count` INT DEFAULT 0 COMMENT '错误次数',
  `last_error` TEXT COMMENT '最后错误信息',
  `start_time` DATETIME COMMENT '启动时间',
  `update_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL WebSocket批次统计';
```

**用途说明**：
- 实时监控每个批次的状态
- 分析告警频率和响应时间
- 为后续优化提供数据支持

---

### 4. 使用现有表

#### quick_monitor_template（模板配置表）
**用途**：监控配置模板（已存在）

字段说明：
- `min_market_cap`: 最小市值
- `has_twitter`: 'profile' 表示需要profile类型Twitter
- `events_config`: JSON格式的监控指标
- `time_interval`: 监控间隔（1m/5m/1h）
- `trigger_logic`: any/all

#### token_launch_history（代币历史表）
**用途**：存储代币的历史数据（已存在）

关键字段：
- `ca`: Token地址
- `token_symbol`: 代币符号
- `token_name`: 代币名称
- `highest_market_cap`: 历史最高市值
- `twitter_url`: Twitter链接
- `source`: 来源（pump/bonk）

#### twitter_account_manage（Twitter账号管理表）
**用途**：管理Twitter账号信息（已存在）

关键字段：
- `twitter_url`: Twitter链接
- `twitter_type`: 类型（profile/空）

---

## 核心流程

### 1. 初始化流程（预处理批次）

**说明**：此流程通过脚本预先执行，生成批次池数据

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 读取模板配置 (quick_monitor_template)                      │
│    - 获取所有 del_flag=0 的配置                               │
│    - 解析市值区间、Twitter要求、监控指标                        │
│    - 支持多个模板，按 min_market_cap 划分区间                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. 获取Profile Twitter列表 (twitter_account_manage)          │
│    - 查询所有 twitter_type='profile' 的账号                   │
│    - 构建 Twitter URL 集合（用于后续匹配）                     │
│    - 预计约 54,361 个 profile 账号                           │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. 查询并匹配CA (token_launch_history)                       │
│    - 查询 source IN ('pump', 'bonk')                         │
│    - 过滤 highest_market_cap >= 300000                       │
│    - 匹配 twitter_url 在 profile 列表中                       │
│    - 按市值区间匹配到对应模板                                   │
│    - 预计约 1,903 个 CA                                      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. 获取Pair地址 (调用DBotX API)                              │
│    - 并发调用 search_pairs API                               │
│    - 获取每个CA的主交易对地址                                  │
│    - 批量处理（100个一批，控制并发）                            │
│    - 失败的CA记录日志，后续手动补充                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. 按市值排序并分批                                           │
│    - 按 highest_market_cap 降序排序                          │
│    - 分配优先级（priority = 市值排名）                         │
│    - 每99个CA一批，生成 batch_id (1-20)                       │
│    - 记录批次内排序 (sort_order: 0-98)                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. 写入批次池 (sol_ws_batch_pool)                            │
│    - 插入所有CA数据到批次池表                                  │
│    - 包含：CA、pair、模板配置、监控指标                         │
│    - 初始化 is_active=1                                      │
│    - 初始化批次统计表 (sol_ws_batch_stats)                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. 完成初始化                                                 │
│    - 生成初始化报告（各批次CA数量、模板分布等）                  │
│    - 准备就绪，可启动监控程序                                   │
└─────────────────────────────────────────────────────────────┘
```

**执行命令**：
```bash
# 完整初始化（清空重建）
python scripts/initialize_sol_ws_batches.py

# 增量模式（只处理新CA）
python scripts/initialize_sol_ws_batches.py --incremental
```

**初始化策略**：
- **完整模式**（默认）：清空所有数据，重新初始化全部CA
- **增量模式**（`--incremental`）：保留现有数据，只处理新增CA
- API限流控制：50个CA/批，批次间延迟3秒
- 失败CA自动保存到 `logs/failed_pairs.json`

**实际执行结果**（2025-10-31）：
```
✅ 已入库：1,899个CA
❌ 失败：4个CA（无交易池）
⏱️ 耗时：约6-8分钟
📦 批次：20个
```

**失败CA重试**：
```bash
# 重试失败的CA（从 logs/failed_pairs.json 读取）
python scripts/retry_failed_pairs.py
```
- 自动读取失败列表
- 重试获取pair地址
- 成功的自动补充到数据库
- 降低并发避免限流（50个/批）

---

### 2. 运行时监控流程

**说明**：此流程在主程序中执行，从批次池读取数据并建立监控

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 从批次池加载配置 (sol_ws_batch_pool)                       │
│    - 读取所有 is_active=1 的记录                              │
│    - 按 batch_id 分组                                        │
│    - 预计20个批次                                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. 建立WebSocket连接                                         │
│    - 连接到 wss://api-data-v1.dbotx.com/data/ws/             │
│    - 使用 x-api-key 认证                                     │
│    - 启动心跳保活（每30秒ping一次）                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. 发送批次订阅请求                                           │
│    - 按 batch_id 顺序发送                                    │
│    - 每批最多99个CA的pair地址                                 │
│    - pairsInfoInterval 根据批次内CA的配置确定                  │
│    - 批次间延迟0.5秒（避免过快）                               │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. 接收实时数据                                               │
│    - 监听WebSocket消息                                       │
│    - 解析 pairsInfo 数据                                     │
│    - 提取价格、交易量、持仓等信息                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. 检查告警条件                                               │
│    - 根据 pair_address 查找对应的配置                         │
│    - 根据 events_config 检查各项指标                          │
│    - 根据 trigger_logic (any/all) 判断是否触发                │
│    - 检查 Redis 冷却期（默认3分钟）                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. 发送告警                                                   │
│    - 构造告警消息                                             │
│    - 推送到 Telegram                                         │
│    - 推送到微信                                               │
│    - 记录到 sol_ws_alert_log                                 │
│    - 设置 Redis 冷却期                                       │
│    - 更新 sol_ws_batch_stats                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. 异常处理                                                   │
│    - 连接断开时自动重连                                        │
│    - 更新错误计数到 sol_ws_batch_stats                        │
│    - 记录错误日志                                             │
│    - 发送系统告警                                             │
└─────────────────────────────────────────────────────────────┘
```

**订阅请求格式**：
```python
# 每个批次的订阅请求
{
    "method": "subscribe",
    "type": "pairsInfo",
    "args": {
        "pairsInfoInterval": "1m",  # 从批次内CA的time_interval获取（需统一）
        "pairs": [
            {
                "pair": "pair_address_1",
                "token": "ca_address_1"  # 可选，用于获取持有者数量
            },
            {
                "pair": "pair_address_2",
                "token": "ca_address_2"
            }
            # ... 最多99个
        ]
    }
}
```

**批次示例**：
```
Batch 1: 99个CA（市值最高）
Batch 2: 99个CA
...
Batch 19: 99个CA
Batch 20: 37个CA（最后一批）

总计: 1,903个CA
```

---

### 3. 数据处理流程

```
WebSocket 收到消息
    ↓
解析消息（JSON）
    ↓
提取 pair_address（字段: p）
    ↓
从内存缓存查询对应的CA配置
（批次池数据在启动时加载到内存）
    ↓
解析数据字段：
  - p: pair地址
  - pc1m/pc5m/pc1h: 1分钟/5分钟/1小时价格变化（小数形式，0.1=10%）
  - bv1m/bv5m/bv1h: 买入交易量（USD）
  - sv1m/sv5m/sv1h: 卖出交易量（USD）
  - bt1m/bt5m/bt1h: 买入次数
  - st1m/st5m/st1h: 卖出次数
  - t10: TOP10持仓比例（小数形式，0.5=50%）
  - tp: 当前价格
  - mp: 市值
    ↓
根据 events_config 检查条件：
  - priceChange: 价格涨跌幅（根据time_interval选择pc1m/pc5m/pc1h）
  - volume: 交易量（根据time_interval计算bv+sv）
    ↓
判断 trigger_logic：
  - any: 满足任一条件即触发
  - all: 满足所有启用的条件才触发
    ↓
检查冷却期（Redis）：
  - Key: sol:ws:cooldown:{ca}
  - TTL: 180秒（3分钟）
  - 冷却期内跳过告警
    ↓
构造告警消息
    ↓
推送Telegram + 微信
    ↓
记录到 sol_ws_alert_log：
  - 触发数据（价格、市值、交易量等）
  - 触发原因（JSON数组）
  - 推送状态（成功/失败）
    ↓
更新 sol_ws_batch_stats：
  - alert_count += 1
  - last_alert_time = NOW()
  - total_messages += 1
    ↓
设置冷却期（Redis）
```

---

### 4. 告警消息模板

```
🔔 SOL Token 告警

💰 Token: {symbol} ({name})
🔗 CA: {ca}
🔗 Pair: {pair_address}

📊 实时数据
💵 当前价格: ${price}
💎 市值: ${market_cap}
📈 价格变化: {price_change}% (1小时)

💹 交易数据
💸 1小时交易量: ${volume} (买 ${buy_volume} + 卖 ${sell_volume})
📊 1小时交易次数: {total_txs} (买 {buy_txs} + 卖 {sell_txs})
👥 TOP10持仓: {top10_percent}%

✨ 触发原因
• {reason_1}
• {reason_2}

🔗 DBotX: https://www.dbotx.com/sol/{ca}
🔗 Birdeye: https://birdeye.so/token/{ca}

⏰ {timestamp}
```

---

## 代码架构

### 目录结构

```
src/solalert/
├── monitor/
│   ├── sol_websocket_manager.py      # WebSocket管理器（主入口）
│   ├── sol_websocket_connection.py   # 单个WebSocket连接类
│   ├── sol_subscription_pool.py      # 订阅池管理
│   └── sol_alert_checker.py          # 告警条件检查器
├── api/
│   └── dbotx_api.py                   # DBotX API封装（已存在）
├── core/
│   ├── database.py                    # 数据库操作（已存在）
│   └── redis_client.py                # Redis客户端（已存在）
└── notifiers/
    └── telegram.py                     # Telegram推送（已存在）
```

---

### 核心类设计

#### 1. SolWebSocketManager（主管理器）

```python
class SolWebSocketManager:
    """
    SOL WebSocket 监控管理器
    负责整体协调和管理
    """
    
    def __init__(self, db, redis_client, config):
        self.db = db
        self.redis = redis_client
        self.config = config
        self.connections = []
        self.pool_manager = SolSubscriptionPool(db, redis_client)
    
    async def initialize(self):
        """初始化：加载配置、分配订阅池"""
        # 1. 加载模板配置
        templates = self.load_templates()
        
        # 2. 获取profile Twitter
        profile_urls = self.get_profile_twitters()
        
        # 3. 查询并匹配CA
        cas = self.match_cas_with_templates(templates, profile_urls)
        
        # 4. 获取pair地址
        cas_with_pairs = await self.fetch_pair_addresses(cas)
        
        # 5. 分配到订阅池
        await self.pool_manager.allocate_subscriptions(cas_with_pairs)
        
        # 6. 创建WebSocket连接
        self.create_connections()
    
    async def start(self):
        """启动所有连接"""
        tasks = [conn.start() for conn in self.connections]
        await asyncio.gather(*tasks)
    
    async def stop(self):
        """停止所有连接"""
        tasks = [conn.stop() for conn in self.connections]
        await asyncio.gather(*tasks)
```

#### 2. SolWebSocketConnection（单连接）

```python
class SolWebSocketConnection:
    """
    单个 WebSocket 连接
    """
    
    def __init__(self, connection_id, db, redis, config):
        self.connection_id = connection_id
        self.db = db
        self.redis = redis
        self.config = config
        self.ws = None
        self.subscriptions = []
        self.alert_checker = SolAlertChecker(db, redis, config)
    
    async def start(self):
        """启动连接"""
        # 1. 从数据库加载订阅列表
        self.load_subscriptions()
        
        # 2. 连接WebSocket
        await self.connect()
        
        # 3. 发送订阅请求
        await self.send_subscriptions()
        
        # 4. 进入接收循环
        await self.receive_loop()
    
    async def connect(self):
        """连接WebSocket"""
        headers = {'x-api-key': self.config.api_key}
        self.ws = await websockets.connect(
            'wss://api-data-v1.dbotx.com/data/ws/',
            extra_headers=headers
        )
        # 更新状态到数据库
        self.update_status('running')
    
    async def send_subscriptions(self):
        """发送订阅请求（分批）"""
        # 每99个一批
        for i in range(0, len(self.subscriptions), 99):
            batch = self.subscriptions[i:i+99]
            message = self.build_subscription_message(batch)
            await self.ws.send(json.dumps(message))
            await asyncio.sleep(0.1)  # 避免过快
    
    async def receive_loop(self):
        """接收消息循环"""
        while True:
            try:
                message = await self.ws.recv()
                await self.handle_message(message)
            except Exception as e:
                logger.error(f"Connection {self.connection_id} error: {e}")
                await self.reconnect()
    
    async def handle_message(self, message):
        """处理收到的消息"""
        data = json.loads(message)
        
        if data.get('type') == 'pairsInfo':
            results = data.get('result', [])
            for item in results:
                await self.process_pair_data(item)
    
    async def process_pair_data(self, data):
        """处理单个交易对数据"""
        pair_address = data.get('p')
        
        # 从数据库查找订阅配置
        sub = self.find_subscription(pair_address)
        if not sub:
            return
        
        # 检查告警条件
        should_alert, reasons = await self.alert_checker.check(data, sub)
        
        if should_alert:
            await self.send_alert(sub, data, reasons)
```

#### 3. SolSubscriptionPool（订阅池管理）

```python
class SolSubscriptionPool:
    """
    订阅池管理
    """
    
    def __init__(self, db, redis):
        self.db = db
        self.redis = redis
    
    async def allocate_subscriptions(self, cas_with_config, num_connections=3):
        """
        分配订阅到各个连接
        
        策略：
        1. 按优先级（市值）排序
        2. 平均分配到各个连接
        3. 同一个模板的CA尽量分配到同一个连接
        """
        # 按优先级排序（市值从高到低）
        sorted_cas = sorted(cas_with_config, 
                           key=lambda x: x['market_cap'], 
                           reverse=True)
        
        # 轮询分配
        for i, ca_config in enumerate(sorted_cas):
            connection_id = (i % num_connections) + 1
            
            # 插入到数据库
            self.db.execute_update("""
                INSERT INTO ws_subscription_pool 
                (connection_id, ca, token_symbol, token_name, pair_address, 
                 template_id, template_name, market_cap, twitter_url, 
                 time_interval, events_config, trigger_logic, priority)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                connection_id = VALUES(connection_id),
                pair_address = VALUES(pair_address),
                template_id = VALUES(template_id),
                market_cap = VALUES(market_cap),
                update_time = NOW()
            """, (
                connection_id,
                ca_config['ca'],
                ca_config.get('token_symbol'),
                ca_config.get('token_name'),
                ca_config['pair_address'],
                ca_config['template_id'],
                ca_config['template_name'],
                ca_config['market_cap'],
                ca_config['twitter_url'],
                ca_config['time_interval'],
                json.dumps(ca_config['events_config']),
                ca_config['trigger_logic'],
                ca_config['priority']
            ))
```

#### 4. SolAlertChecker（告警检查器）

```python
class SolAlertChecker:
    """
    告警条件检查器
    """
    
    def __init__(self, db, redis, config):
        self.db = db
        self.redis = redis
        self.config = config
    
    async def check(self, data, subscription):
        """
        检查是否满足告警条件
        
        Returns:
            (should_alert, reasons): (是否告警, 触发原因列表)
        """
        events_config = json.loads(subscription['events_config'])
        trigger_logic = subscription['trigger_logic']
        
        triggered_conditions = []
        
        # 1. 检查价格变化
        if events_config.get('priceChange', {}).get('enabled'):
            rise_percent = events_config['priceChange'].get('risePercent')
            fall_percent = events_config['priceChange'].get('fallPercent')
            
            # 从数据中提取价格变化（根据time_interval选择）
            time_interval = subscription['time_interval']
            price_change = self.get_price_change(data, time_interval)
            
            if rise_percent and price_change >= rise_percent:
                triggered_conditions.append(
                    f"价格{time_interval}上涨 {price_change:+.2f}% (阈值: {rise_percent}%)"
                )
            
            if fall_percent and price_change <= -fall_percent:
                triggered_conditions.append(
                    f"价格{time_interval}下跌 {price_change:+.2f}% (阈值: -{fall_percent}%)"
                )
        
        # 2. 检查交易量
        if events_config.get('volume', {}).get('enabled'):
            threshold = events_config['volume'].get('threshold')
            
            # 从数据中提取交易量
            volume = self.get_volume(data, subscription['time_interval'])
            
            if volume >= threshold:
                triggered_conditions.append(
                    f"{subscription['time_interval']}交易量 ${volume:,.0f} (阈值: ${threshold:,.0f})"
                )
        
        # 3. 判断触发逻辑
        if trigger_logic == 'any':
            should_alert = len(triggered_conditions) > 0
        else:  # 'all'
            expected_count = sum([
                1 for k, v in events_config.items() 
                if isinstance(v, dict) and v.get('enabled')
            ])
            should_alert = len(triggered_conditions) >= expected_count
        
        # 4. 检查冷却期
        if should_alert:
            in_cooldown = await self.check_cooldown(subscription['ca'])
            if in_cooldown:
                return False, []
        
        return should_alert, triggered_conditions
    
    def get_price_change(self, data, time_interval):
        """根据时间间隔获取价格变化"""
        field_map = {
            '1m': 'pc1m',
            '5m': 'pc5m',
            '1h': 'pc1h',
            '6h': 'pc6h',
            '24h': 'pc24h'
        }
        field = field_map.get(time_interval, 'pc1h')
        return data.get(field, 0) * 100  # 转为百分比
    
    def get_volume(self, data, time_interval):
        """根据时间间隔获取交易量"""
        field_map = {
            '1m': ('bv1m', 'sv1m'),
            '5m': ('bv5m', 'sv5m'),
            '1h': ('bv1h', 'sv1h'),
            '6h': ('bv6h', 'sv6h'),
            '24h': ('bv24h', 'sv24h')
        }
        buy_field, sell_field = field_map.get(time_interval, ('bv1h', 'sv1h'))
        return data.get(buy_field, 0) + data.get(sell_field, 0)
    
    async def check_cooldown(self, ca):
        """检查是否在冷却期"""
        key = f"sol:alert:cooldown:{ca}"
        return self.redis.exists(key)
    
    async def set_cooldown(self, ca, minutes=3):
        """设置冷却期"""
        key = f"sol:alert:cooldown:{ca}"
        self.redis.set(key, "1", ex=minutes * 60)
```

---

## 配置说明

### config.yaml

```yaml
# SOL WebSocket 监控配置

websocket:
  # WebSocket URL
  url: "wss://api-data-v1.dbotx.com/data/ws/"
  
  # API Key
  api_key: "your_api_key_here"
  
  # 连接数量（3-4个）
  num_connections: 3
  
  # 重连配置
  reconnect:
    max_retries: 5
    initial_delay: 1  # 秒
    max_delay: 60     # 秒
    backoff_factor: 2
  
  # 心跳配置
  heartbeat:
    interval: 30      # 秒
    timeout: 10       # 秒

# 告警配置
alert:
  # 冷却期（分钟）
  cooldown_minutes: 3
  
  # Telegram配置
  telegram:
    bot_token: "your_bot_token"
    channel_id: "your_channel_id"

# 数据库配置
database:
  host: "localhost"
  port: 3306
  user: "root"
  password: "password"
  database: "crypto_web3"

# Redis配置
redis:
  host: "localhost"
  port: 6379
  db: 0

# 日志配置
logging:
  level: "INFO"
  file: "logs/sol_websocket.log"
  max_bytes: 104857600  # 100MB
  backup_count: 5
```

---

## 监控告警

### 1. 健康检查

**实现方式**：
- 每30秒心跳检查
- 更新 `ws_connection_status.last_heartbeat`
- 如果超过60秒没有心跳，触发告警

**监控脚本**：
```python
async def health_check():
    """健康检查"""
    while True:
        try:
            # 检查所有连接
            connections = db.execute_query("""
                SELECT connection_id, status, last_heartbeat,
                       TIMESTAMPDIFF(SECOND, last_heartbeat, NOW()) as seconds_ago
                FROM ws_connection_status
            """)
            
            for conn in connections:
                if conn['seconds_ago'] > 60 and conn['status'] == 'running':
                    logger.error(f"Connection {conn['connection_id']} 心跳超时")
                    # 发送告警
                    send_alert(f"WebSocket连接{conn['connection_id']}心跳超时")
        
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
        
        await asyncio.sleep(30)
```

### 2. 性能指标

**监控指标**：
- 每个连接的订阅数量
- 收到消息数
- 处理消息数
- 发送告警数
- 错误次数
- 运行时长

**查询SQL**：
```sql
-- 连接状态概览
SELECT 
    connection_id,
    status,
    subscribed_count,
    received_messages,
    processed_messages,
    alert_sent,
    error_count,
    TIMESTAMPDIFF(SECOND, start_time, NOW()) as uptime_seconds,
    last_heartbeat
FROM ws_connection_status
ORDER BY connection_id;

-- 订阅池统计
SELECT 
    connection_id,
    template_name,
    COUNT(*) as ca_count,
    SUM(alert_count) as total_alerts,
    SUM(error_count) as total_errors
FROM ws_subscription_pool
WHERE is_active = 1
GROUP BY connection_id, template_name
ORDER BY connection_id, template_name;
```

---

## 部署方案

### 1. 环境准备

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 创建数据库表
mysql -u root -p crypto_web3 < sql/create_ws_subscription_pool.sql
mysql -u root -p crypto_web3 < sql/create_ws_connection_status.sql

# 3. 配置文件
cp config.yaml.example config.yaml
# 编辑config.yaml，填入API key等配置

# 4. 初始化订阅池
python scripts/initialize_subscription_pool.py
```

### 2. 启动服务

```bash
# 方式1：直接启动
python -m src.solalert.monitor.sol_websocket_manager

# 方式2：使用systemd（推荐）
sudo cp deployment/sol-websocket-monitor.service /etc/systemd/system/
sudo systemctl enable sol-websocket-monitor
sudo systemctl start sol-websocket-monitor

# 查看状态
sudo systemctl status sol-websocket-monitor

# 查看日志
tail -f logs/sol_websocket.log
```

### 3. 监控

```bash
# 健康检查
curl http://localhost:8080/health

# 查看状态
curl http://localhost:8080/status

# 重新加载配置（不中断连接）
curl -X POST http://localhost:8080/reload
```

---

## 技术决策与优化

### 核心决策

#### 1. 订阅策略
**决策**：单连接订阅所有20个批次
- 统一使用 `pairsInfoInterval: "1m"`
- 单个WebSocket连接，发送20次subscribe请求
- 每次订阅最多99个pair（符合API限制）
- 批次间延迟0.5秒避免请求过快

**理由**：
- DBotX支持单连接多次订阅
- 管理简单，减少连接维护成本
- 避免多连接的负载均衡复杂度

#### 2. 数据匹配策略
**决策**：统一订阅1m间隔，根据配置动态选择时间窗口字段

**核心逻辑**：
- WebSocket订阅统一使用 `pairsInfoInterval: "1m"`
- DBotX返回所有时间窗口的数据（1m/5m/1h/6h/24h）
- 根据每个CA配置的 `time_interval`，动态选择对应字段

**字段映射表**：
```python
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

def get_field_value(data: dict, field_name: str, time_interval: str) -> float:
    """
    根据时间间隔获取对应字段的值
    
    Args:
        data: WebSocket返回的数据
        field_name: 字段名（price_change, buy_volume等）
        time_interval: 时间间隔（1m, 5m, 1h等）
    
    Returns:
        对应字段的值
    """
    field_key = FIELD_MAP.get(time_interval, {}).get(field_name)
    if not field_key:
        return 0
    return data.get(field_key, 0)

# 使用示例
time_interval = config['time_interval']  # 从配置读取：1m/5m/1h

# 动态获取对应时间窗口的数据
price_change = get_field_value(data, 'price_change', time_interval) * 100  # 转百分比
buy_volume = get_field_value(data, 'buy_volume', time_interval)
sell_volume = get_field_value(data, 'sell_volume', time_interval)
total_volume = buy_volume + sell_volume
```

**优势**：
- ✅ 灵活：未来模板改成5m/1h，代码不需要修改
- ✅ 简单：统一的订阅参数，一个映射表搞定
- ✅ 扩展性：新增时间窗口只需在映射表加一行
- ✅ 性能：字典查找O(1)，无性能损耗

#### 3. 配置缓存方案
**决策**：前期使用Redis缓存，后期考虑落地到数据库

**Redis Key设计**（遵循项目统一风格 `quick_monitor:` 前缀）：
```python
# 1. 配置缓存（Hash结构）- 每个pair一个
quick_monitor:ws:config:{pair_address}
# 内容：ca, template_id, time_interval, events_config等

# 2. CA到Pair映射（String）
quick_monitor:ws:ca_pair:{ca}
# 内容：pair_address

# 3. 批次索引（Set）
quick_monitor:ws:batch:{batch_id}
# 内容：该批次所有的pair地址

# 4. 告警冷却（String，带TTL）
quick_monitor:ws:cooldown:{ca}
# TTL: 180-210秒（3分钟+30秒随机抖动）

# 5. 配置版本号（String）
quick_monitor:ws:version
# 内容：最后更新时间戳
```

**启动流程**：
```python
1. 从Redis读取模板配置
   quick_monitor:template:sol
   
2. 从数据库读取批次池
   sol_ws_batch_pool（已有的表）
   按batch_id, template_id分组统计
   
3. 加载配置到内存缓存
   {pair_address: config_dict}
   
4. 启动WebSocket连接并订阅
```

**配置刷新**（可选，暂不实现）：
```python
# 定期检查数据库配置变化（每5分钟）
# 1. 对比 update_time
# 2. 更新变化的配置到内存缓存
# 3. 记录变更日志
```

**优势**：
- 避免每次消息都查数据库（1899个CA，1分钟间隔 = 1899次/分钟）
- 支持配置热更新（检测到变化自动刷新）
- Redis读取性能高

#### 4. 告警去重机制
**决策**：3分钟冷却期 + 30秒随机抖动（参考BSC区块监控）
```python
import random

cooldown_seconds = 180 + random.randint(0, 30)  # 180-210秒
redis.setex(f"sol:ws:cooldown:{ca}", cooldown_seconds, "1")
```

**理由**：
- 避免短时间内重复告警
- 随机抖动防止多个CA同时解除冷却造成告警风暴

#### 5. 批次优先级
**说明**：虽然初始化时部分CA的pair获取顺序有误，但整体仍按历史市值排序
- Batch 1-2：市值最高（≥ $10M，配置4）
- Batch 3-8：次高（$1M-$10M，配置3）
- Batch 9-15：中等（$500K-$1M，配置2）
- Batch 16-20：较低（$300K-$500K，配置1）

**建议**：后续可通过定期任务重新排序优化

### 性能优化

#### 1. 内存缓存配置（已采纳）
```python
class SolWebSocketConnection:
    def __init__(self):
        self.config_cache = {}  # {pair_address: config_dict}
    
    async def load_config_from_redis(self):
        """从Redis批量加载配置"""
        pairs = self.get_all_pairs()
        pipeline = self.redis.pipeline()
        for pair in pairs:
            pipeline.get(f"sol:ws:config:{pair}")
        results = pipeline.execute()
        # 解析并缓存
```

#### 2. 数据库索引优化（已采纳）
```sql
-- 优化查询性能
ALTER TABLE sol_ws_batch_pool 
ADD INDEX idx_batch_active_interval (batch_id, is_active, time_interval);

ALTER TABLE sol_ws_batch_pool
ADD INDEX idx_pair_active (pair_address, is_active);

-- 告警日志查询优化
ALTER TABLE sol_ws_alert_log
ADD INDEX idx_ca_alert_time (ca, alert_time DESC);

ALTER TABLE sol_ws_alert_log
ADD INDEX idx_template_time (template_id, alert_time DESC);
```

#### 3. 数据验证
```python
class DataValidator:
    @staticmethod
    def validate_pair_data(data: dict) -> bool:
        """验证WebSocket数据完整性"""
        required = ['p', 'tp', 'mp']  # pair, token_price, market_cap
        if not all(k in data for k in required):
            return False
        
        # 验证数值合理性
        if data.get('tp', 0) <= 0 or data.get('mp', 0) <= 0:
            return False
        
        return True
    
    @staticmethod
    def is_valid_price_change(value: float) -> bool:
        """验证价格变化范围（-1000% 到 +1000%）"""
        return -10.0 <= value <= 10.0
```

### 配置刷新机制

```python
class ConfigRefresher:
    """配置刷新器 - 定期检查数据库配置变化"""
    
    def __init__(self, db, redis):
        self.db = db
        self.redis = redis
        self.last_check = None
    
    async def refresh_loop(self):
        """每5分钟检查一次"""
        while True:
            try:
                await self.check_and_update()
                await asyncio.sleep(300)  # 5分钟
            except Exception as e:
                logger.error(f"配置刷新失败: {e}")
    
    async def check_and_update(self):
        """检查并更新配置"""
        # 1. 查询有更新的配置
        updated = self.db.execute_query("""
            SELECT * FROM sol_ws_batch_pool
            WHERE update_time > %s AND is_active = 1
        """, (self.last_check,))
        
        if not updated:
            return
        
        # 2. 更新Redis
        pipeline = self.redis.pipeline()
        for config in updated:
            key = f"sol:ws:config:{config['pair_address']}"
            pipeline.set(key, json.dumps(config))
        pipeline.execute()
        
        # 3. 记录日志
        logger.info(f"配置已更新: {len(updated)} 条")
        self.last_check = datetime.now()
```

### 后续优化（暂不实现）

#### 第二阶段
- ⏳ 动态批次调整（根据告警频率重新排序CA）
- ⏳ Web管理界面
- ⏳ 实时监控Dashboard
- ⏳ 告警规则可视化编辑

#### 第三阶段
- ⏳ 智能冷却（根据触发频率动态调整冷却期）
- ⏳ Prometheus监控指标
- ⏳ 告警聚合报告（每日TOP10统计）
- ⏳ 机器学习预测
- ⏳ 多链支持（ETH、BSC等）
- ⏳ 告警分级（紧急/重要/普通）

---

## 附录

### A. 数据字段映射

DBotX WebSocket返回的字段（缩写）：

| 字段 | 含义 | 说明 |
|------|------|------|
| `p` | pair | Pair地址 |
| `pc1m` | price change 1m | 1分钟价格变化（0.1 = 10%） |
| `pc5m` | price change 5m | 5分钟价格变化 |
| `pc1h` | price change 1h | 1小时价格变化 |
| `bv1m` | buy volume 1m | 1分钟买入交易量（USD） |
| `sv1m` | sell volume 1m | 1分钟卖出交易量（USD） |
| `bv1h` | buy volume 1h | 1小时买入交易量（USD） |
| `sv1h` | sell volume 1h | 1小时卖出交易量（USD） |
| `bt1h` | buy times 1h | 1小时买入次数 |
| `st1h` | sell times 1h | 1小时卖出次数 |
| `tp` | token price | 当前价格 |
| `mp` | market cap | 市值 |
| `t10` | top 10 | TOP10持仓比例（0.5 = 50%） |

### B. API限制

DBotX API限制：
- 数据API：6000次/分钟
- WebSocket订阅：每次最多99个pair
- 并发连接：支持多个客户端

---

**文档版本**：v1.4  
**最后更新**：2025-11-02  
**维护人员**：开发团队  
**状态**：✅ 开发完成，✅ 生产环境运行中

---

## 更新记录

### v1.4 (2025-11-02)
- ✅ 优化 DBotX API 超时配置（10s→30s）
- ✅ 增加指数退避重试策略（1s, 2s, 4s）
- ✅ 完善错误日志（添加具体失败的CA/Pair信息）
- ✅ Telegram 告警添加内联按钮（GMGN + AXIOM）
- ✅ 数据验证优化（过滤异常价格变化 >500%）
- ✅ 项目迁移文档（PROJECT_MIGRATION_GUIDE.md）

### v1.3 (2025-10-31)
- ✅ WebSocket 连接实现完成
- ✅ 告警逻辑实现完成
- ✅ 数据库索引优化
- ✅ 首次生产环境测试通过

### v1.2 (2025-10-30)
- ✅ 数据库表设计完成
- ✅ 批次池初始化完成（1,899个CA）
- ✅ 技术方案评审通过


