#!/bin/bash
# BSC监控系统启动脚本

echo "========================================"
echo "启动 BSC WebSocket 监控系统"
echo "========================================"
echo

# 切换到项目目录
cd /workspace || exit 1

# 创建日志目录
mkdir -p logs

# 检查是否已经在运行
if pgrep -f "start_bsc_websocket_monitor.py" > /dev/null; then
    echo "⚠️  BSC监控系统已经在运行"
    echo
    echo "进程信息:"
    ps aux | grep "start_bsc_websocket_monitor.py" | grep -v grep
    echo
    echo "如需重启，请先停止现有进程:"
    echo "  pkill -f start_bsc_websocket_monitor.py"
    exit 1
fi

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 未安装"
    exit 1
fi

echo "✅ Python3: $(python3 --version)"
echo

# 检查依赖
echo "检查依赖包..."
python3 -c "import websocket; import requests; import redis" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  缺少依赖包，正在安装..."
    pip3 install -r requirements.txt
fi

echo "✅ 依赖包已安装"
echo

# 提供启动选项
echo "选择启动方式:"
echo "  1) 前台运行（Ctrl+C停止）"
echo "  2) 后台运行（nohup）"
echo "  3) Screen会话（推荐）"
echo "  4) 仅测试配置"
echo
read -p "请选择 (1-4): " choice

case $choice in
    1)
        echo
        echo "🚀 前台启动 BSC 监控..."
        echo "   按 Ctrl+C 停止"
        echo
        python3 start_bsc_websocket_monitor.py
        ;;
    2)
        echo
        echo "🚀 后台启动 BSC 监控..."
        nohup python3 start_bsc_websocket_monitor.py > logs/bsc_websocket.log 2>&1 &
        PID=$!
        echo "   进程ID: $PID"
        echo "   日志文件: logs/bsc_websocket.log"
        echo
        echo "查看日志:"
        echo "  tail -f logs/bsc_websocket.log"
        echo
        echo "停止进程:"
        echo "  kill $PID"
        ;;
    3)
        if ! command -v screen &> /dev/null; then
            echo "❌ Screen 未安装"
            echo "   安装命令: sudo apt-get install screen"
            exit 1
        fi
        
        echo
        echo "🚀 创建 Screen 会话: bsc_monitor"
        echo
        screen -dmS bsc_monitor bash -c 'cd /workspace && python3 start_bsc_websocket_monitor.py'
        sleep 2
        
        echo "✅ Screen 会话已创建"
        echo
        echo "查看会话:"
        echo "  screen -r bsc_monitor"
        echo
        echo "分离会话: Ctrl+A, D"
        echo "列出所有会话: screen -ls"
        echo "停止会话: screen -X -S bsc_monitor quit"
        ;;
    4)
        echo
        echo "🧪 测试配置..."
        python3 -c "
from src.solalert.monitor.bsc_websocket_monitor import BSCWebSocketMonitor
from src.solalert.core.config import validate_config

# 验证配置
try:
    validate_config()
    print('✅ 配置验证通过')
except Exception as e:
    print(f'❌ 配置验证失败: {e}')
    exit(1)

# 测试Redis连接
try:
    from src.solalert.core.redis_client import get_redis
    redis_client = get_redis()
    redis_client.client.ping()
    print('✅ Redis 连接正常')
except Exception as e:
    print(f'❌ Redis 连接失败: {e}')
    exit(1)

# 测试数据库连接
try:
    from src.solalert.core.database import test_database_connection
    if test_database_connection():
        print('✅ 数据库连接正常')
    else:
        print('❌ 数据库连接失败')
        exit(1)
except Exception as e:
    print(f'❌ 数据库测试失败: {e}')
    exit(1)

print()
print('✅ 所有配置测试通过，可以启动监控系统')
"
        ;;
    *)
        echo "❌ 无效选项"
        exit 1
        ;;
esac

echo
echo "========================================"
