"""
Alchemy Webhook 接收服务
接收 Alchemy 推送的区块链交易数据

优化策略：
- 立即返回 200（不阻塞 Alchemy）
- 后台异步处理（asyncio.create_task）
- 并发控制（Semaphore 限制）
"""
import logging
import json
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
import uvicorn

logger = logging.getLogger(__name__)

# === 后台任务管理 ===
background_tasks = set()  # 保存任务引用，防止被 GC
processing_semaphore = asyncio.Semaphore(20)  # 限制并发处理数（2核4G 服务器）

app = FastAPI(
    title="Alchemy Webhook Service",
    description="接收 Alchemy 推送的 BSC 交易数据",
    version="1.0.0"
)


class AlchemyWebhookHandler:
    """Alchemy Webhook 处理器"""
    
    def __init__(self):
        self.received_count = 0
        self.last_block = None
        
        # BSC 监控器和处理器
        self.bsc_monitor = None
        self.processor = None
    
    def set_monitor(self, monitor):
        """设置 BSC 监控器"""
        self.bsc_monitor = monitor
        
        # 初始化处理器
        if monitor and hasattr(monitor, 'collector'):
            from .webhook_processor import AlchemyWebhookProcessor
            self.processor = AlchemyWebhookProcessor(monitor.collector)
            logger.info("✅ Webhook 处理器已初始化")
    
    async def handle_webhook_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理 Alchemy webhook 数据
        
        Args:
            data: Alchemy 推送的数据
            
        Returns:
            处理结果
        """
        self.received_count += 1
        
        try:
            # 1. 提取基本信息
            event = data.get('event', {})
            block_data = event.get('data', {}).get('block', {})
            
            # 2. 解析区块信息（支持十进制和十六进制）
            block_number = block_data.get('number', 0)
            block_hash = block_data.get('hash', '')
            timestamp = block_data.get('timestamp', 0)
            
            # 转换格式
            if isinstance(block_number, str):
                block_number = int(block_number, 16) if block_number.startswith('0x') else int(block_number)
            if isinstance(timestamp, str):
                timestamp = int(timestamp, 16) if timestamp.startswith('0x') else int(timestamp)
            
            self.last_block = block_number
            
            # 3. 解析 logs（Swap 事件）
            logs = block_data.get('logs', [])
            swap_events = []
            
            # 简化日志：单行显示关键信息
            logger.info(f"📦 Webhook #{self.received_count} | 区块 #{block_number} | Logs: {len(logs)}")
            
            # 4. 解析每个 log（Swap 事件）
            for log in logs:
                try:
                    topics = log.get('topics', [])
                    data_hex = log.get('data', '0x')
                    pair_address = log.get('address', '').lower()
                    log_index = log.get('index', log.get('logIndex', 0))  # 适配两种格式
                    tx_info = log.get('transaction', {})
                    tx_hash = tx_info.get('hash', '')
                    
                    # 检查是否是 Swap 事件
                    if not topics or len(topics) == 0:
                        continue
                    
                    topic0 = topics[0].lower()
                    
                    # V2 Swap: 0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822
                    is_v2_swap = topic0 == '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'
                    
                    if is_v2_swap:
                        # 解析 V2 Swap data: amount0In, amount1In, amount0Out, amount1Out
                        data_clean = data_hex[2:] if data_hex.startswith('0x') else data_hex
                        if len(data_clean) >= 256:
                            amount0_in = int(data_clean[0:64], 16)
                            amount1_in = int(data_clean[64:128], 16)
                            amount0_out = int(data_clean[128:192], 16)
                            amount1_out = int(data_clean[192:256], 16)
                            
                            swap_event = {
                                'type': 'v2',
                                'pair_address': pair_address,
                                'tx_hash': tx_hash,
                                'log_index': log_index,
                                'block_number': block_number,
                                'timestamp': timestamp,
                                'amount0_in': amount0_in,
                                'amount1_in': amount1_in,
                                'amount0_out': amount0_out,
                                'amount1_out': amount1_out
                            }
                            swap_events.append(swap_event)
                            
                            # 详细日志移至 DEBUG 级别
                            logger.debug(f"   Swap #{len(swap_events)}: {pair_address[:10]}... | {tx_hash[:10]}...")
                
                except Exception as e:
                    logger.error(f"解析 log 失败: {e}")
                    continue
            
            # 5. 使用处理器转换数据并对接 BSC Monitor
            filtered_events = []
            if self.processor:
                try:
                    # 处理器会过滤出目标交易对并计算USDT价值
                    filtered_events = self.processor.process_webhook_data(data)
                    
                    if filtered_events:
                        logger.info(f"✅ 目标交易: {len(filtered_events)} 个 → 进入金额/平台/指标过滤...")
                        
                        # 转发给 BSC Monitor 进行后续处理
                        if self.bsc_monitor:
                            await self.bsc_monitor.handle_block_events(filtered_events)
                        else:
                            logger.warning("⚠️  BSC Monitor 未初始化")
                    else:
                        logger.debug("⏭️  无目标交易")
                
                except Exception as e:
                    logger.error(f"❌ 数据处理失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning("⚠️  Processor 未初始化，跳过过滤")
            
            return {
                'status': 'success',
                'received_count': self.received_count,
                'block_number': block_number,
                'processed_swaps': len(swap_events),
                'filtered_swaps': len(filtered_events)
            }
        
        except Exception as e:
            logger.error(f"❌ 处理 webhook 数据失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            return {
                'status': 'error',
                'error': str(e)
            }


# 全局处理器实例
webhook_handler = AlchemyWebhookHandler()


@app.get("/")
async def root():
    """健康检查"""
    return {
        "service": "Alchemy Webhook Service",
        "status": "running",
        "received_count": webhook_handler.received_count,
        "last_block": webhook_handler.last_block,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/")
async def root_webhook(request: Request):
    """
    根路径 webhook 接收（兼容直接配置根URL的情况）
    重定向到标准的 webhook 处理函数
    """
    logger.info("⚠️  收到根路径 POST 请求，建议配置完整路径: /webhook/alchemy/bsc")
    return await receive_alchemy_webhook(request)


@app.get("/health")
async def health():
    """健康检查接口"""
    return {"status": "ok"}


async def process_webhook_background(data: dict):
    """
    后台处理 webhook 数据
    不阻塞主请求，失败也不影响响应
    """
    async with processing_semaphore:  # 控制并发
        try:
            # 保存文件
            save_to_file = os.getenv('WEBHOOK_SAVE_JSON', 'true').lower() == 'true'
            if save_to_file:
                filename = "webhook_data_all.jsonl"
                try:
                    import time
                    with open(filename, 'a', encoding='utf-8') as f:
                        data_with_meta = {
                            'received_at': time.time(),
                            'received_at_str': datetime.now().isoformat(),
                            'data': data
                        }
                        f.write(json.dumps(data_with_meta, ensure_ascii=False) + '\n')
                    print(f"💾 数据已追加到: {filename}")
                except Exception as e:
                    logger.error(f"保存文件失败: {e}")
            
            # 处理 webhook 数据（耗时操作）
            result = await webhook_handler.handle_webhook_data(data)
            logger.debug(f"✅ 后台处理完成")
            
        except Exception as e:
            logger.error(f"❌ 后台处理失败: {e}")
            import traceback
            logger.error(traceback.format_exc())


@app.post("/webhook/alchemy/bsc")
async def receive_alchemy_webhook(request: Request):
    """
    接收 Alchemy webhook 推送 - 优化版（立即返回）
    
    策略：
    1. 快速接收数据
    2. 立即返回 200（<10ms）
    3. 后台异步处理（不阻塞）
    """
    try:
        # 1. 快速接收数据
        data = await request.json()
        
        # 2. 立即返回 200（关键！让 Alchemy 知道收到了）
        response_data = {
            "status": "received",
            "timestamp": datetime.now().isoformat()
        }
        
        # 3. 创建后台任务（不等待完成）
        task = asyncio.create_task(process_webhook_background(data))
        
        # 保存任务引用，防止被 GC
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)  # 完成后自动移除
        
        # 4. 立即返回
        return JSONResponse(
            status_code=200,
            content=response_data
        )
    
    except ClientDisconnect:
        # Alchemy 提前关闭连接，正常情况
        logger.debug("⚠️  客户端提前断开连接")
        return JSONResponse(
            status_code=200,
            content={"status": "received"}
        )
    
    except json.JSONDecodeError as e:
        # JSON 解析失败也返回 200，避免 Alchemy 重试
        logger.error(f"❌ JSON 解析失败: {e}")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": "invalid_json"}
        )
    
    except Exception as e:
        # 任何错误都返回 200，避免积压重试
        logger.error(f"❌ Webhook 接收失败: {e}")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": str(e)}
        )


@app.post("/webhook/alchemy/bsc/test")
async def test_webhook(request: Request):
    """
    测试接口，用于手动测试 webhook
    
    使用方法:
    curl -X POST http://localhost:8001/webhook/alchemy/bsc/test \
         -H "Content-Type: application/json" \
         -d '{"test": "data"}'
    """
    data = await request.json()
    
    logger.info("🧪 收到测试请求")
    logger.info(json.dumps(data, indent=2, ensure_ascii=False))
    
    return {
        "status": "test_success",
        "received_data": data,
        "timestamp": datetime.now().isoformat()
    }


async def start_webhook_server_async(host: str = "0.0.0.0", port: int = 8001):
    """
    启动 webhook 服务器（异步版本，可与其他服务并发运行）
    
    Args:
        host: 监听地址
        port: 监听端口
    """
    import asyncio
    
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        # === 高并发优化 ===
        workers=1,                 # 单进程（2核4G 够用，省内存）
        backlog=4096,              # 队列大小（匹配系统 somaxconn）
        timeout_keep_alive=75,     # 保持连接（默认值）
        limit_concurrency=100,     # 限制并发连接数（防止过载）
        limit_max_requests=10000,  # 10k 请求后重启 worker（防止内存泄漏）
        # === 性能优化 ===
        log_level="warning",       # 只记录警告和错误
        access_log=False,          # 关闭访问日志
        lifespan="on"
    )
    server = uvicorn.Server(config)
    
    try:
        await server.serve()
    except asyncio.CancelledError:
        logger.info("⏹️  Webhook 服务已停止")
        raise


def start_webhook_server(host: str = "0.0.0.0", port: int = 8001):
    """
    启动 webhook 服务器（阻塞式，用于单独启动）
    
    Args:
        host: 监听地址
        port: 监听端口
    """
    import signal
    import sys
    
    # Windows Ctrl+C 处理
    def signal_handler(sig, frame):
        logger.info("\n⏹️  收到停止信号，正在关闭服务...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动信息已在 start.py 显示，这里不重复
    try:
        # 禁用 uvicorn 的访问日志，只保留业务日志
        uvicorn.run(
            app,
            host=host,
            port=port,
            # === 高并发优化 ===
            workers=1,                 # 单进程（2核4G 够用，省内存）
            backlog=4096,              # 队列大小（匹配系统 somaxconn）
            timeout_keep_alive=75,     # 保持连接（默认值）
            limit_concurrency=100,     # 限制并发连接数（防止过载）
            limit_max_requests=10000,  # 10k 请求后重启 worker（防止内存泄漏）
            # === 性能优化 ===
            log_level="warning",       # 只记录警告和错误
            access_log=False,          # 关闭访问日志
            lifespan="on"              # 启用生命周期事件
        )
    except KeyboardInterrupt:
        logger.info("\n⏹️  服务已停止")
        sys.exit(0)


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 启动服务
    start_webhook_server()

