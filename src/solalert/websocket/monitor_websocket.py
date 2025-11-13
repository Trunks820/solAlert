"""Monitor V2 WebSocket 客户端"""

import websocket
import json
import threading
import time
from typing import Callable, Dict, Any
import logging

logger = logging.getLogger(__name__)


class MonitorWebSocketClient:
    """Monitor V2 WebSocket 客户端"""
    
    def __init__(self, url: str, consumer_id: str):
        """
        初始化WebSocket客户端
        
        Args:
            url: WebSocket URL, 如 ws://localhost:8080/websocket/monitor
            consumer_id: Consumer ID
        """
        self.url = url
        self.consumer_id = consumer_id
        self.ws = None
        self.connected = False
        self.registered = False
        self.heartbeat_thread = None
        self.stop_event = threading.Event()
        
        # 消息回调
        self.callbacks = {
            'on_connected': None,
            'on_batch_reload': None,
            'on_error': None
        }
        
        # 批次状态缓存（用于心跳上报）
        self.batch_states = {}
    
    def connect(self):
        """连接WebSocket"""
        logger.info(f"正在连接 Monitor WebSocket: {self.url}")
        
        # 创建WebSocket连接
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        
        # 在新线程中运行（非阻塞）
        wst = threading.Thread(target=self.ws.run_forever, daemon=True)
        wst.start()
        
        # 等待连接建立（最多5秒）
        for _ in range(50):
            if self.connected:
                break
            time.sleep(0.1)
        
        if not self.connected:
            logger.error("WebSocket连接超时")
            return False
        
        return True
    
    def _on_open(self, ws):
        """连接建立回调"""
        logger.info("✅ Monitor WebSocket 连接成功")
        self.connected = True
        
        # 注册Consumer
        self._register()
        
        # 启动心跳线程
        self._start_heartbeat()
        
        # 触发回调
        if self.callbacks['on_connected']:
            try:
                self.callbacks['on_connected']()
            except Exception as e:
                logger.error(f"on_connected 回调执行失败: {e}")
    
    def _on_message(self, ws, message):
        """接收消息回调"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            
            logger.debug(f"📩 收到消息: {msg_type}")
            
            if msg_type == 'connected':
                session_id = data.get('sessionId') or data.get('session_id')
                logger.info(f"连接确认: session_id={session_id}")
            
            elif msg_type == 'registered':
                logger.info(f"✅ Consumer注册成功: {self.consumer_id}")
                self.registered = True
            
            elif msg_type == 'batch_reload':
                self._handle_batch_reload(data)
            
            elif msg_type == 'pong':
                logger.debug("💓 Pong响应")
            
            elif msg_type == 'error':
                error_msg = data.get('message')
                logger.error(f"❌ 服务端错误: {error_msg}")
                if self.callbacks['on_error']:
                    try:
                        self.callbacks['on_error'](data)
                    except Exception as e:
                        logger.error(f"on_error 回调执行失败: {e}")
            
            else:
                logger.warning(f"未知消息类型: {msg_type}")
        
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
    
    def _on_error(self, ws, error):
        """错误回调"""
        logger.error(f"❌ WebSocket错误: {error}")
        if self.callbacks['on_error']:
            try:
                self.callbacks['on_error']({"error": str(error)})
            except Exception as e:
                logger.error(f"on_error 回调执行失败: {e}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """连接关闭回调"""
        logger.info(f"WebSocket连接关闭: code={close_status_code}, msg={close_msg}")
        self.connected = False
        self.registered = False
        self._stop_heartbeat()
    
    def _register(self):
        """注册Consumer"""
        self.send({
            "type": "register",
            "consumer_id": self.consumer_id,
            "consumerId": self.consumer_id  # 兼容两种格式
        })
    
    def _start_heartbeat(self):
        """启动心跳线程"""
        self.stop_event.clear()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        logger.info("💓 心跳线程已启动")
    
    def _stop_heartbeat(self):
        """停止心跳线程"""
        self.stop_event.set()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2)
        logger.info("💔 心跳线程已停止")
    
    def _heartbeat_loop(self):
        """心跳循环（30秒间隔）"""
        while not self.stop_event.is_set():
            if self.connected and self.registered:
                self.send_heartbeat()
            
            # 每30秒发送一次
            self.stop_event.wait(30)
    
    def send_heartbeat(self):
        """发送心跳"""
        batches = []
        for batch_id, state in self.batch_states.items():
            batches.append({
                "batch_id": batch_id,
                "batchId": batch_id,  # 兼容两种格式
                "status": state.get('status', 'running'),
                "progress": state.get('progress', 0)
            })
        
        self.send({
            "type": "heartbeat",
            "consumer_id": self.consumer_id,
            "consumerId": self.consumer_id,  # 兼容两种格式
            "batches": batches,
            "timestamp": int(time.time() * 1000)
        })
        logger.debug(f"💓 发送心跳: {len(batches)} 批次")
    
    def update_batch_status(self, batch_id: int, status: str, message: str = None, progress: int = None):
        """
        更新批次状态
        
        Args:
            batch_id: 批次ID（monitor_batch_v2.id，全局唯一）
            status: 状态 (running/completed/error)
            message: 状态消息
            progress: 进度（0-100）
        """
        # 更新缓存
        self.batch_states[batch_id] = {
            'status': status,
            'progress': progress if progress is not None else (100 if status == 'completed' else 0)
        }
        
        # 发送状态更新
        self.send({
            "type": "batch_status",
            "batch_id": batch_id,
            "batchId": batch_id,  # 兼容两种格式
            "status": status,
            "message": message or f"批次{status}",
            "timestamp": int(time.time() * 1000)
        })
        logger.info(f"📦 批次状态更新: batch_id={batch_id}, status={status}")
    
    def _handle_batch_reload(self, data):
        """处理批次重载通知"""
        task_id = data.get('task_id') or data.get('taskId')
        epoch = data.get('epoch')
        message = data.get('message')
        
        logger.info(f"🔄 收到批次重载通知: task_id={task_id}, epoch={epoch}")
        logger.info(f"   消息: {message}")
        
        # 触发回调
        if self.callbacks['on_batch_reload']:
            try:
                should_reload = self.callbacks['on_batch_reload'](task_id, epoch)
                action = "reloading" if should_reload else "skipping"
            except Exception as e:
                logger.error(f"on_batch_reload 回调执行失败: {e}")
                action = "skipping"
        else:
            action = "skipping"  # 默认跳过
        
        # 发送确认
        self.send({
            "type": "batch_reload_ack",
            "task_id": task_id,
            "taskId": task_id,  # 兼容两种格式
            "consumer_id": self.consumer_id,
            "consumerId": self.consumer_id,  # 兼容两种格式
            "action": action
        })
        logger.info(f"✅ 批次重载确认: action={action}")
    
    def send(self, message: dict):
        """发送消息"""
        if self.ws and self.connected:
            try:
                self.ws.send(json.dumps(message))
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
        else:
            logger.warning("WebSocket未连接，无法发送消息")
    
    def close(self):
        """关闭连接"""
        logger.info("正在关闭 Monitor WebSocket...")
        self._stop_heartbeat()
        if self.ws:
            self.ws.close()
        self.connected = False
        self.registered = False
        logger.info("Monitor WebSocket 已关闭")
    
    # 回调注册方法
    def on_connected(self, callback: Callable):
        """注册连接成功回调"""
        self.callbacks['on_connected'] = callback
        return self
    
    def on_batch_reload(self, callback: Callable[[int, int], bool]):
        """
        注册批次重载回调
        
        Args:
            callback: 回调函数，接收 (task_id, epoch)，返回 True 表示重新加载
        """
        self.callbacks['on_batch_reload'] = callback
        return self
    
    def on_error(self, callback: Callable):
        """注册错误回调"""
        self.callbacks['on_error'] = callback
        return self

