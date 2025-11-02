# 项目迁移指南

本文档指导如何将 solAlert 项目从一台服务器迁移到另一台服务器。

---

## 📋 目录

1. [环境要求](#环境要求)
2. [迁移前准备](#迁移前准备)
3. [数据备份](#数据备份)
4. [新服务器配置](#新服务器配置)
5. [项目迁移](#项目迁移)
6. [数据恢复](#数据恢复)
7. [服务启动](#服务启动)
8. [迁移验证](#迁移验证)
9. [常见问题](#常见问题)

---

## 🖥️ 环境要求

### 目标服务器最低配置
- **操作系统**: Linux (Ubuntu 20.04+) / Windows Server 2019+
- **CPU**: 2核+
- **内存**: 4GB+
- **硬盘**: 50GB+
- **网络**: 公网IP（如需接收Webhook）

### 软件依赖
- **Python**: 3.9+
- **MySQL**: 8.0+
- **Redis**: 6.0+
- **Node.js**: 16+ (可选，如有前端)
- **Git**: 2.0+

---

## 📦 迁移前准备

### 1. 检查当前环境

```bash
# 查看Python版本
python --version

# 查看MySQL版本
mysql --version

# 查看Redis版本
redis-server --version

# 查看当前项目路径
pwd
```

### 2. 记录配置信息

创建配置清单 `migration_checklist.txt`：

```
[ ] MySQL数据库地址: _______
[ ] MySQL用户名: _______
[ ] MySQL密码: _______
[ ] Redis地址: _______
[ ] Redis端口: _______
[ ] Redis密码: _______
[ ] Telegram Bot Token: _______
[ ] Telegram Channel ID: _______
[ ] DBotX API Key: _______
[ ] GMGN API Cookie: _______
[ ] RPC节点地址: _______
```

### 3. 检查运行中的服务

```bash
# 查看正在运行的Python进程
ps aux | grep python

# 查看监听的端口
netstat -tuln | grep LISTEN
```

---

## 💾 数据备份

### 1. 备份MySQL数据库

```bash
# 导出所有数据库（包含结构和数据）
mysqldump -u root -p --all-databases > solalert_full_backup.sql

# 或只导出solAlert数据库
mysqldump -u root -p solalert > solalert_db_backup.sql

# 备份到指定目录
mysqldump -u root -p solalert > /backup/solalert_$(date +%Y%m%d_%H%M%S).sql
```

### 2. 备份Redis数据

```bash
# 方式1：使用Redis自带备份
redis-cli SAVE
# 备份文件位置: /var/lib/redis/dump.rdb

# 复制备份文件
cp /var/lib/redis/dump.rdb /backup/redis_dump_$(date +%Y%m%d_%H%M%S).rdb

# 方式2：使用RDB导出
redis-cli --rdb /backup/redis_backup.rdb
```

### 3. 备份项目代码

```bash
# 打包整个项目（排除虚拟环境和日志）
cd /path/to/solAlert
tar -czf solalert_code_$(date +%Y%m%d_%H%M%S).tar.gz \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='logs/*.log' \
  --exclude='.git' \
  .

# 移动到备份目录
mv solalert_code_*.tar.gz /backup/
```

### 4. 备份配置文件

```bash
# 单独备份配置文件（敏感信息）
tar -czf solalert_config_$(date +%Y%m%d_%H%M%S).tar.gz \
  src/solalert/core/config.py \
  .env

# 移动到备份目录
mv solalert_config_*.tar.gz /backup/
```

### 5. 备份日志文件（可选）

```bash
# 如需保留历史日志
tar -czf solalert_logs_$(date +%Y%m%d_%H%M%S).tar.gz logs/

# 移动到备份目录
mv solalert_logs_*.tar.gz /backup/
```

### 6. 打包所有备份文件

```bash
# 创建最终备份包
cd /backup
tar -czf solalert_migration_$(date +%Y%m%d_%H%M%S).tar.gz \
  solalert_db_backup.sql \
  redis_dump_*.rdb \
  solalert_code_*.tar.gz \
  solalert_config_*.tar.gz

# 查看备份大小
ls -lh solalert_migration_*.tar.gz
```

---

## 🔧 新服务器配置

### 1. 安装系统依赖 (Ubuntu/Debian)

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装基础工具
sudo apt install -y git curl wget vim build-essential

# 安装Python 3.9+
sudo apt install -y python3.9 python3.9-dev python3-pip python3.9-venv

# 安装MySQL
sudo apt install -y mysql-server mysql-client
sudo mysql_secure_installation

# 安装Redis
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

### 2. 安装系统依赖 (CentOS/RHEL)

```bash
# 更新系统
sudo yum update -y

# 安装基础工具
sudo yum install -y git curl wget vim gcc gcc-c++ make

# 安装Python 3.9+
sudo yum install -y python39 python39-devel python39-pip

# 安装MySQL
sudo yum install -y mysql-server
sudo systemctl enable mysqld
sudo systemctl start mysqld

# 安装Redis
sudo yum install -y redis
sudo systemctl enable redis
sudo systemctl start redis
```

### 3. 安装系统依赖 (Windows Server)

```powershell
# 下载并安装Python
# https://www.python.org/downloads/

# 下载并安装MySQL
# https://dev.mysql.com/downloads/installer/

# 下载并安装Redis (Windows版)
# https://github.com/microsoftarchive/redis/releases

# 安装Git
# https://git-scm.com/download/win
```

### 4. 配置MySQL

```bash
# 登录MySQL
sudo mysql -u root -p

# 创建数据库
CREATE DATABASE solalert CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 创建用户并授权
CREATE USER 'solalert'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON solalert.* TO 'solalert'@'localhost';
FLUSH PRIVILEGES;

# 退出
EXIT;
```

### 5. 配置Redis

```bash
# 编辑Redis配置
sudo vim /etc/redis/redis.conf

# 修改以下配置：
# bind 127.0.0.1  # 如需远程访问改为 0.0.0.0
# requirepass your_redis_password  # 设置密码
# maxmemory 2gb  # 设置最大内存
# maxmemory-policy allkeys-lru  # 内存淘汰策略

# 重启Redis
sudo systemctl restart redis-server

# 测试连接
redis-cli -a your_redis_password ping
```

---

## 🚀 项目迁移

### 1. 传输备份文件到新服务器

```bash
# 方式1：使用scp
scp /backup/solalert_migration_*.tar.gz user@new_server:/tmp/

# 方式2：使用rsync
rsync -avz /backup/solalert_migration_*.tar.gz user@new_server:/tmp/

# 方式3：使用云存储（如S3、OSS）
# 上传到云存储，然后从新服务器下载
```

### 2. 在新服务器上解压

```bash
# 登录新服务器
ssh user@new_server

# 创建项目目录
sudo mkdir -p /opt/solalert
sudo chown $USER:$USER /opt/solalert
cd /opt/solalert

# 解压迁移包
tar -xzf /tmp/solalert_migration_*.tar.gz

# 解压代码
tar -xzf solalert_code_*.tar.gz

# 解压配置
tar -xzf solalert_config_*.tar.gz
```

### 3. 创建Python虚拟环境

```bash
cd /opt/solalert

# 创建虚拟环境
python3.9 -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux
# 或
.\venv\Scripts\activate  # Windows

# 升级pip
pip install --upgrade pip

# 安装依赖
pip install -r requirements.txt
```

### 4. 修改配置文件

```bash
# 编辑配置文件
vim src/solalert/core/config.py

# 或使用环境变量文件
vim .env
```

修改以下配置项：
```python
# MySQL配置
DB_CONFIG = {
    'host': 'localhost',  # 新服务器的MySQL地址
    'port': 3306,
    'user': 'solalert',
    'password': 'your_new_password',  # 新密码
    'database': 'solalert',
}

# Redis配置
REDIS_CONFIG = {
    'host': 'localhost',  # 新服务器的Redis地址
    'port': 6379,
    'password': 'your_new_redis_password',  # 新密码
    'db': 0,
}

# 其他配置（如需修改）
# - Telegram Bot Token
# - API Keys
# - RPC节点地址
```

---

## 📥 数据恢复

### 1. 恢复MySQL数据

```bash
# 导入数据库
mysql -u solalert -p solalert < solalert_db_backup.sql

# 验证导入
mysql -u solalert -p -e "USE solalert; SHOW TABLES;"

# 检查数据
mysql -u solalert -p -e "USE solalert; SELECT COUNT(*) FROM sol_ws_batch_pool;"
```

### 2. 恢复Redis数据

```bash
# 停止Redis服务
sudo systemctl stop redis-server

# 复制备份文件
sudo cp redis_dump_*.rdb /var/lib/redis/dump.rdb
sudo chown redis:redis /var/lib/redis/dump.rdb

# 启动Redis
sudo systemctl start redis-server

# 验证数据
redis-cli -a your_redis_password
> KEYS *
> QUIT
```

### 3. 恢复日志（可选）

```bash
# 如果备份了日志
tar -xzf solalert_logs_*.tar.gz -C /opt/solalert/
```

---

## ▶️ 服务启动

### 1. 测试配置

```bash
# 激活虚拟环境
cd /opt/solalert
source venv/bin/activate

# 测试数据库连接
python -c "from solalert.core.database import DatabaseManager; db = DatabaseManager(); print('✅ 数据库连接成功')"

# 测试Redis连接
python -c "from solalert.core.redis_client import RedisClient; from solalert.core.config import REDIS_CONFIG; r = RedisClient(config=REDIS_CONFIG); print('✅ Redis连接成功')"
```

### 2. 启动服务（开发模式）

```bash
# SOL WebSocket监控
python start_sol_websocket_monitor.py

# BSC WebSocket监控
python start_bsc_websocket_monitor.py

# Token监控（轮询）
python start_token_monitor.py
```

### 3. 配置系统服务（生产模式）

创建systemd服务文件：

```bash
# SOL WebSocket监控服务
sudo vim /etc/systemd/system/solalert-sol-ws.service
```

内容：
```ini
[Unit]
Description=SOL WebSocket Monitor
After=network.target mysql.service redis.service

[Service]
Type=simple
User=your_user
WorkingDirectory=/opt/solalert
Environment="PATH=/opt/solalert/venv/bin"
ExecStart=/opt/solalert/venv/bin/python start_sol_websocket_monitor.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/solalert/logs/sol_ws_monitor.log
StandardError=append:/opt/solalert/logs/sol_ws_monitor.error.log

[Install]
WantedBy=multi-user.target
```

类似地创建其他服务：
```bash
# BSC WebSocket监控服务
sudo vim /etc/systemd/system/solalert-bsc-ws.service

# Token监控服务
sudo vim /etc/systemd/system/solalert-token-monitor.service
```

启用并启动服务：
```bash
# 重载systemd配置
sudo systemctl daemon-reload

# 启用服务（开机自启）
sudo systemctl enable solalert-sol-ws
sudo systemctl enable solalert-bsc-ws
sudo systemctl enable solalert-token-monitor

# 启动服务
sudo systemctl start solalert-sol-ws
sudo systemctl start solalert-bsc-ws
sudo systemctl start solalert-token-monitor

# 查看状态
sudo systemctl status solalert-sol-ws
sudo systemctl status solalert-bsc-ws
sudo systemctl status solalert-token-monitor
```

### 4. 配置日志轮转

```bash
# 创建logrotate配置
sudo vim /etc/logrotate.d/solalert
```

内容：
```
/opt/solalert/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    missingok
    create 0644 your_user your_user
}
```

---

## ✅ 迁移验证

### 1. 功能测试清单

```bash
# 1. 数据库连接
mysql -u solalert -p -e "USE solalert; SELECT COUNT(*) as total FROM sol_ws_batch_pool;"

# 2. Redis连接
redis-cli -a your_redis_password PING

# 3. 查看服务状态
sudo systemctl status solalert-*

# 4. 查看日志
tail -f /opt/solalert/logs/sol_ws_monitor.log

# 5. 测试Telegram推送
python test_manual_alert.py

# 6. 测试WebSocket连接
python test_ws_vs_api_compare.py
```

### 2. 监控指标检查

- [ ] WebSocket连接状态正常
- [ ] 能够接收实时数据推送
- [ ] Telegram告警推送成功
- [ ] 数据库读写正常
- [ ] Redis缓存正常
- [ ] CPU使用率 < 80%
- [ ] 内存使用率 < 80%
- [ ] 磁盘使用率 < 80%

### 3. 业务验证

- [ ] 监控到活跃Token的交易
- [ ] 告警触发正常
- [ ] 告警消息格式正确
- [ ] 按钮链接可用
- [ ] 冷却期机制正常

---

## 🔥 常见问题

### Q1: MySQL连接失败 "Access denied"

**原因**: 用户权限或密码不正确

**解决**:
```sql
-- 重新授权
GRANT ALL PRIVILEGES ON solalert.* TO 'solalert'@'localhost';
FLUSH PRIVILEGES;

-- 或重置密码
ALTER USER 'solalert'@'localhost' IDENTIFIED BY 'new_password';
```

### Q2: Redis连接超时

**原因**: Redis未启动或防火墙阻止

**解决**:
```bash
# 检查Redis状态
sudo systemctl status redis-server

# 检查端口
netstat -tuln | grep 6379

# 检查防火墙
sudo ufw allow 6379  # Ubuntu
sudo firewall-cmd --add-port=6379/tcp --permanent  # CentOS
```

### Q3: Python依赖安装失败

**原因**: 缺少编译工具或系统库

**解决**:
```bash
# Ubuntu/Debian
sudo apt install -y python3-dev build-essential libssl-dev libffi-dev

# CentOS/RHEL
sudo yum install -y python3-devel gcc gcc-c++ openssl-devel
```

### Q4: WebSocket连接频繁断开

**原因**: 网络不稳定或服务器防火墙

**解决**:
```bash
# 检查网络连通性
ping api-data-v1.dbotx.com

# 检查DNS
nslookup api-data-v1.dbotx.com

# 调整重连策略（在代码中）
```

### Q5: 日志文件过大

**原因**: 日志未轮转

**解决**:
```bash
# 立即执行日志轮转
sudo logrotate -f /etc/logrotate.d/solalert

# 手动清理旧日志
find /opt/solalert/logs -name "*.log" -mtime +30 -delete
```

### Q6: 服务无法自动重启

**原因**: systemd配置错误

**解决**:
```bash
# 检查服务配置
sudo systemctl cat solalert-sol-ws

# 查看详细日志
sudo journalctl -u solalert-sol-ws -f

# 重新加载配置
sudo systemctl daemon-reload
sudo systemctl restart solalert-sol-ws
```

---

## 📝 迁移后清理

### 旧服务器清理

```bash
# 停止服务
sudo systemctl stop solalert-*
sudo systemctl disable solalert-*

# 备份确认无误后，可删除数据（谨慎操作！）
# rm -rf /path/to/old/solalert
```

### 保留备份

```bash
# 建议保留备份至少30天
# 将备份文件传输到安全位置
# - 云存储（S3、OSS等）
# - 异地服务器
# - 本地硬盘
```

---

## 🎯 迁移检查清单

- [ ] 环境依赖安装完成
- [ ] MySQL数据导入成功
- [ ] Redis数据恢复成功
- [ ] 配置文件修改正确
- [ ] 服务启动成功
- [ ] WebSocket连接正常
- [ ] 告警推送测试通过
- [ ] 日志记录正常
- [ ] 系统监控配置完成
- [ ] 备份文件已保存到安全位置
- [ ] 文档更新（记录新服务器信息）

---

## 📞 技术支持

如遇到问题，请检查：
1. 系统日志: `sudo journalctl -u solalert-* -f`
2. 应用日志: `tail -f /opt/solalert/logs/*.log`
3. MySQL日志: `/var/log/mysql/error.log`
4. Redis日志: `/var/log/redis/redis-server.log`

---

**迁移完成！** 🎉

项目已成功迁移到新服务器，请持续监控运行状态。

