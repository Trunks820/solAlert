#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BSC PancakeSwap V2 完整监控
监控 USDT/WBNB 购买其他代币的交易（完整版）
"""
import json, time, websocket, requests
from decimal import Decimal
from functools import lru_cache

websocket.enableTrace(False)

# Chainstack WebSocket 和 HTTP RPC
WS_URL = "wss://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e"
RPC_URL = "https://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e"

# 基准币地址
USDT = "0x55d398326f99059ff775485246999027b3197955".lower()
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c".lower()

# Fourmeme Proxy（内盘）
FOURMEME_PROXY = [
    "0x5c952063c7fc8610ffdb798152d69f0b9550762b".lower(),  # 主 AMAP/Create
    "0xf251f83e40a78868fcfa3fa4599dad6494e46034".lower()   # Try Buy
]

# PancakeV2 Swap Topic
TOPIC_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

# 去重集合
seen_txs = set()

# RPC 请求 ID
rpc_id = 1000


def rpc_call(method, params):
    """发送 HTTP RPC 请求"""
    global rpc_id
    rpc_id += 1
    
    try:
        resp = requests.post(
            RPC_URL,
            json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
            timeout=5
        )
        result = resp.json().get("result")
        return result
    except Exception as e:
        print(f"RPC 错误: {e}")
        return None


@lru_cache(maxsize=10000)
def get_pair_tokens(pair_address):
    """获取交易对的 token0 和 token1"""
    try:
        # token0()
        token0_data = rpc_call("eth_call", [{
            "to": pair_address,
            "data": "0x0dfe1681"  # token0()
        }, "latest"])
        
        # token1()
        token1_data = rpc_call("eth_call", [{
            "to": pair_address,
            "data": "0xd21220a7"  # token1()
        }, "latest"])
        
        if token0_data and token1_data:
            token0 = ("0x" + token0_data[-40:]).lower()
            token1 = ("0x" + token1_data[-40:]).lower()
            return token0, token1
    except:
        pass
    
    return None, None


@lru_cache(maxsize=10000)
def get_decimals(token_address):
    """获取代币精度"""
    # 已知代币
    if token_address == USDT or token_address == WBNB:
        return 18
    
    try:
        result = rpc_call("eth_call", [{
            "to": token_address,
            "data": "0x313ce567"  # decimals()
        }, "latest"])
        
        if result:
            return int(result, 16)
    except:
        pass
    
    return 18  # 默认


@lru_cache(maxsize=100)
def get_token_symbol(token_address):
    """获取代币符号"""
    if token_address == USDT:
        return "USDT"
    if token_address == WBNB:
        return "WBNB"
    
    try:
        result = rpc_call("eth_call", [{
            "to": token_address,
            "data": "0x95d89b41"  # symbol()
        }, "latest"])
        
        if result and result != "0x":
            # 解析字符串
            try:
                hex_str = result[2:]
                # 跳过前 64 字符（offset）
                if len(hex_str) > 128:
                    length = int(hex_str[64:128], 16)
                    symbol_hex = hex_str[128:128+length*2]
                    symbol = bytes.fromhex(symbol_hex).decode('utf-8', errors='ignore')
                    return symbol if symbol else token_address[:8]
            except:
                pass
    except:
        pass
    
    return token_address[:8]  # 返回地址前8位


def parse_swap_data(data_hex):
    """解析 Swap 事件的 data 字段"""
    if not data_hex or not data_hex.startswith("0x"):
        return None
    
    data_hex = data_hex[2:]
    if len(data_hex) < 256:
        return None
    
    try:
        return {
            "amount0In": int(data_hex[0:64], 16),
            "amount1In": int(data_hex[64:128], 16),
            "amount0Out": int(data_hex[128:192], 16),
            "amount1Out": int(data_hex[192:256], 16),
        }
    except:
        return None


def format_amount(amount_wei, decimals):
    """格式化数量"""
    if amount_wei == 0:
        return "0"
    
    amount = Decimal(amount_wei) / (Decimal(10) ** Decimal(decimals))
    
    # 智能格式化
    if amount >= 1000000:
        return f"{amount / 1000000:.2f}M"
    elif amount >= 1000:
        return f"{amount / 1000:.2f}K"
    elif amount >= 1:
        return f"{amount:.4f}"
    else:
        return f"{amount:.8f}"


def handle_swap_event(log):
    """处理 PancakeSwap V2 Swap 事件"""
    tx_hash = log.get("transactionHash")
    
    # 去重
    if tx_hash in seen_txs:
        return
    seen_txs.add(tx_hash)
    if len(seen_txs) > 10000:
        seen_txs.clear()
    
    pair_address = log.get("address", "").lower()
    swap_data = parse_swap_data(log.get("data"))
    
    if not swap_data:
        return
    
    # 获取 token0 和 token1
    token0, token1 = get_pair_tokens(pair_address)
    if not token0 or not token1:
        return
    
    # 只关注涉及 USDT 或 WBNB 的交易对
    if token0 not in (USDT, WBNB) and token1 not in (USDT, WBNB):
        return
    
    # 跳过 USDT-WBNB 对（互换）
    if {token0, token1} == {USDT, WBNB}:
        return
    
    amount0_in = swap_data["amount0In"]
    amount1_in = swap_data["amount1In"]
    amount0_out = swap_data["amount0Out"]
    amount1_out = swap_data["amount1Out"]
    
    # 判断交易方向
    quote_token = None  # USDT 或 WBNB
    base_token = None   # 目标代币
    quote_amount = 0
    base_amount = 0
    
    if amount0_in > 0 and amount1_out > 0:
        # 用 token0 买 token1
        if token0 in (USDT, WBNB):
            quote_token = token0
            base_token = token1
            quote_amount = amount0_in
            base_amount = amount1_out
    elif amount1_in > 0 and amount0_out > 0:
        # 用 token1 买 token0
        if token1 in (USDT, WBNB):
            quote_token = token1
            base_token = token0
            quote_amount = amount1_in
            base_amount = amount0_out
    
    # 只关注用 USDT/WBNB 买其他币的情况
    if not quote_token or not base_token:
        return
    
    if quote_token not in (USDT, WBNB):
        return
    
    # 获取精度和符号
    quote_decimals = get_decimals(quote_token)
    base_decimals = get_decimals(base_token)
    
    quote_symbol = get_token_symbol(quote_token)
    base_symbol = get_token_symbol(base_token)
    
    # 格式化数量
    quote_formatted = format_amount(quote_amount, quote_decimals)
    base_formatted = format_amount(base_amount, base_decimals)
    
    # 计算 USD 价值（粗略）
    quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
    if quote_token == WBNB:
        usd_value = float(quote_value) * 600  # WBNB 价格约 $600
    else:
        usd_value = float(quote_value)
    
    # 只显示 >= $10 的交易
    if usd_value < 10:
        return
    
    # 打印交易信息
    print(f"\n{'='*80}")
    print(f"💰 {quote_symbol} 买入 {base_symbol}")
    print(f"交易哈希: {tx_hash}")
    print(f"交易对: {pair_address}")
    print(f"付出: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})")
    print(f"得到: {base_formatted} {base_symbol}")
    print(f"目标代币: {base_token}")
    print(f"BSCScan: https://bscscan.com/tx/{tx_hash}")
    print(f"{'='*80}\n")


def handle_proxy_event(log):
    """处理 Fourmeme Proxy 事件（内盘）"""
    tx_hash = log.get("transactionHash")
    
    # 去重
    if tx_hash in seen_txs:
        return
    seen_txs.add(tx_hash)
    
    # 获取交易回执（包含所有 logs）
    try:
        receipt = rpc_call("eth_getTransactionReceipt", [tx_hash])
        if not receipt:
            return
        
        logs = receipt.get("logs", [])
        
        # 解析所有 Transfer 事件
        transfers = []
        TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        
        for log_item in logs:
            topics = log_item.get("topics", [])
            if not topics or topics[0] != TRANSFER_TOPIC:
                continue
            if len(topics) != 3:
                continue
            
            token_addr = log_item.get("address", "").lower()
            from_addr = ("0x" + topics[1][-40:]).lower()
            to_addr = ("0x" + topics[2][-40:]).lower()
            
            # 解析 value
            data = log_item.get("data", "0x")
            try:
                value = int(data, 16) if data != "0x" else 0
            except:
                value = 0
            
            transfers.append({
                "token": token_addr,
                "from": from_addr,
                "to": to_addr,
                "value": value
            })
        
        # 找出 USDT/WBNB 流入 Proxy 的数量
        usdt_in = sum(t["value"] for t in transfers 
                      if t["token"] == USDT and t["to"] == FOURMEME_PROXY)
        wbnb_in = sum(t["value"] for t in transfers 
                      if t["token"] == WBNB and t["to"] == FOURMEME_PROXY)
        
        if usdt_in == 0 and wbnb_in == 0:
            return  # 不是买入交易
        
        # 确定付出的基准币
        quote_token = None
        quote_amount = 0
        if usdt_in > 0:
            quote_token = USDT
            quote_amount = usdt_in
        elif wbnb_in > 0:
            quote_token = WBNB
            quote_amount = wbnb_in
        
        # 找出 Proxy 流出的目标代币（排除 USDT/WBNB）
        target_tokens = {}
        for t in transfers:
            if (t["from"] == FOURMEME_PROXY and 
                t["token"] not in (USDT, WBNB)):
                target_tokens[t["token"]] = target_tokens.get(t["token"], 0) + t["value"]
        
        if not target_tokens:
            return  # 没有目标代币
        
        # 取数量最多的代币
        target_token = max(target_tokens.items(), key=lambda x: x[1])[0]
        target_amount = target_tokens[target_token]
        
        # 获取代币信息
        quote_symbol = get_token_symbol(quote_token)
        target_symbol = get_token_symbol(target_token)
        
        quote_decimals = get_decimals(quote_token)
        target_decimals = get_decimals(target_token)
        
        # 格式化数量
        quote_formatted = format_amount(quote_amount, quote_decimals)
        target_formatted = format_amount(target_amount, target_decimals)
        
        # 计算 USD 价值
        quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
        if quote_token == WBNB:
            usd_value = float(quote_value) * 600
        else:
            usd_value = float(quote_value)
        
        # 只显示 >= $10 的交易
        if usd_value < 10:
            return
        
        # 打印交易信息
        print(f"\n{'='*80}")
        print(f"🏪 【内盘】{quote_symbol} 买入 {target_symbol}")
        print(f"交易哈希: {tx_hash}")
        print(f"付出: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})")
        print(f"得到: {target_formatted} {target_symbol}")
        print(f"目标代币: {target_token}")
        print(f"BSCScan: https://bscscan.com/tx/{tx_hash}")
        print(f"{'='*80}\n")
        
    except Exception as e:
        print(f"处理内盘交易出错: {e}")


def on_open(ws):
    print("✅ WebSocket 连接成功！")
    print(f"节点: {WS_URL[:50]}...")
    print("-" * 80)
    
    # 订阅 Fourmeme Proxy
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": ["logs", {"address": FOURMEME_PROXY}]
    }))
    
    # 订阅 PancakeV2 Swap
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "eth_subscribe",
        "params": ["logs", {"topics": [TOPIC_V2_SWAP]}]
    }))
    
    print("✅ 已订阅事件监听")
    print("⏳ 等待链上交易（仅显示 ≥$10 的交易）...\n")


def on_message(ws, msg):
    try:
        data = json.loads(msg)
    except:
        return
    
    # 订阅确认
    if "id" in data and "result" in data:
        return
    
    # 日志推送
    if data.get("method") == "eth_subscription":
        result = data.get("params", {}).get("result", {})
        addr = (result.get("address") or "").lower()
        topics = result.get("topics") or []
        topic0 = topics[0].lower() if topics else ""
        
        # 处理 Proxy 事件
        if addr == FOURMEME_PROXY:
            handle_proxy_event(result)
        
        # 处理 Swap 事件
        elif topic0 == TOPIC_V2_SWAP:
            handle_swap_event(result)


def on_error(ws, err):
    if err:
        print(f"❌ WebSocket 错误: {err}")


def on_close(ws, code, reason):
    print(f"⚠️ 连接关闭，3秒后重连...")


def main():
    """主循环"""
    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(
                ping_interval=25,
                ping_timeout=10,
                origin="https://chainstack.com"
            )
        except KeyboardInterrupt:
            print("\n👋 程序已停止")
            break
        except Exception as e:
            print(f"❌ 异常: {e}")
        
        time.sleep(3)


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🚀 BSC 链上交易监控 - 完整版")
    print("监控: USDT/WBNB 购买其他代币")
    print("过滤: USDT⇄WBNB 互换")
    print("="*80 + "\n")
    main()

