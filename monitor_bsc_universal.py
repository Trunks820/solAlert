#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BSC 通用内盘交易监控
不依赖预知的 Proxy 地址，通过交易模式自动识别
"""
import json, time, websocket, requests
from decimal import Decimal
from functools import lru_cache
from collections import defaultdict

websocket.enableTrace(False)

# ==================== 配置区域 ====================

# Chainstack WebSocket 和 HTTP RPC
WS_URL = "wss://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e"
RPC_URL = "https://bsc-mainnet.core.chainstack.com/f8232bc60aa7c6a22d5803ab5f15200e"

# 基准币
USDT = "0x55d398326f99059ff775485246999027b3197955".lower()
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c".lower()

# PancakeSwap V2 Swap Topic
TOPIC_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

# ERC20 Transfer Topic
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# 最小金额过滤（USD）
MIN_USD_VALUE = 100.0

# 已知 PancakeSwap Router（排除外盘）
KNOWN_DEX_ROUTERS = [
    "0x10ed43c718714eb63d5aa57b78b54704e256024e",  # PancakeSwap Router V2
    "0x05ff2b0db69458a0750badebc4f9e13add608c7f",  # PancakeSwap Router V1
]

# ==================================================

# 去重和缓存
seen_txs = set()
rpc_id = 1000
proxy_cache = {}  # 记录发现的 Proxy
proxy_stats = defaultdict(int)  # Proxy 统计


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
        return None


@lru_cache(maxsize=10000)
def get_decimals(token_address):
    """获取代币精度"""
    if token_address in (USDT, WBNB):
        return 18
    
    try:
        result = rpc_call("eth_call", [{
            "to": token_address,
            "data": "0x313ce567"  # decimals()
        }, "latest"])
        
        if result and result != "0x":
            return int(result, 16)
    except:
        pass
    
    return 18


@lru_cache(maxsize=1000)
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
            try:
                hex_str = result[2:]
                if len(hex_str) > 128:
                    length = int(hex_str[64:128], 16)
                    symbol_hex = hex_str[128:128+length*2]
                    symbol = bytes.fromhex(symbol_hex).decode('utf-8', errors='ignore')
                    return symbol if symbol else token_address[:8]
            except:
                pass
    except:
        pass
    
    return token_address[:8]


def format_amount(amount_wei, decimals):
    """格式化数量"""
    if amount_wei == 0:
        return "0"
    
    amount = Decimal(amount_wei) / (Decimal(10) ** Decimal(decimals))
    
    if amount >= 1000000:
        return f"{amount / 1000000:.2f}M"
    elif amount >= 1000:
        return f"{amount / 1000:.2f}K"
    elif amount >= 1:
        return f"{amount:.4f}"
    else:
        return f"{amount:.8f}"


def is_contract(address):
    """判断地址是否是合约"""
    try:
        code = rpc_call("eth_getCode", [address, "latest"])
        return code and code != "0x"
    except:
        return False


def analyze_transfer_pattern(tx_hash):
    """
    分析交易的 Transfer 模式，识别内盘买入
    
    内盘买入特征：
    1. USDT/WBNB → 某个中间合约（Proxy）
    2. Proxy → tx.from 的目标代币转出
    3. Proxy 不是已知的 DEX Router
    """
    try:
        # 获取交易回执
        receipt = rpc_call("eth_getTransactionReceipt", [tx_hash])
        if not receipt:
            return None
        
        # 获取交易详情（获取 from 地址）
        tx_detail = rpc_call("eth_getTransactionByHash", [tx_hash])
        if not tx_detail:
            return None
        
        tx_from = tx_detail.get("from", "").lower()
        logs = receipt.get("logs", [])
        
        # 解析所有 Transfer 事件
        transfers = []
        for log in logs:
            topics = log.get("topics", [])
            if not topics or topics[0] != TOPIC_TRANSFER or len(topics) != 3:
                continue
            
            token_addr = log.get("address", "").lower()
            from_addr = ("0x" + topics[1][-40:]).lower()
            to_addr = ("0x" + topics[2][-40:]).lower()
            
            # 解析 value
            data = log.get("data", "0x")
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
        
        if not transfers:
            return None
        
        # 查找模式：USDT/WBNB → Proxy
        base_token_inflows = []  # 基准币流入的地址（可能的 Proxy）
        
        for t in transfers:
            if t["token"] in (USDT, WBNB) and t["value"] > 0:
                # USDT/WBNB 转移到某个地址
                base_token_inflows.append({
                    "proxy": t["to"],
                    "token": t["token"],
                    "value": t["value"],
                    "from": t["from"]
                })
        
        if not base_token_inflows:
            return None
        
        # 对每个可能的 Proxy，检查是否有目标代币流向 tx.from
        results = []
        
        for inflow in base_token_inflows:
            proxy = inflow["proxy"]
            
            # 排除已知的 DEX Router（这些是外盘）
            if proxy in KNOWN_DEX_ROUTERS:
                continue
            
            # 排除 tx.from 自己（自己转给自己不算）
            if proxy == tx_from:
                continue
            
            # 检查是否有 Proxy → tx.from 的代币转出
            target_tokens = []
            for t in transfers:
                if (t["from"] == proxy and 
                    t["to"] == tx_from and 
                    t["token"] not in (USDT, WBNB) and
                    t["value"] > 0):
                    target_tokens.append({
                        "token": t["token"],
                        "value": t["value"]
                    })
            
            # 如果找到目标代币，说明这是内盘买入
            if target_tokens:
                # 取数量最大的代币作为目标
                target = max(target_tokens, key=lambda x: x["value"])
                
                results.append({
                    "tx_hash": tx_hash,
                    "buyer": tx_from,
                    "proxy": proxy,
                    "quote_token": inflow["token"],
                    "quote_value": inflow["value"],
                    "target_token": target["token"],
                    "target_value": target["value"],
                })
        
        return results if results else None
        
    except Exception as e:
        print(f"分析交易出错 {tx_hash}: {e}")
        return None


def handle_transfer_event(log):
    """处理 Transfer 事件，触发交易分析"""
    tx_hash = log.get("transactionHash")
    
    # 去重
    if tx_hash in seen_txs:
        return
    seen_txs.add(tx_hash)
    if len(seen_txs) > 10000:
        seen_txs.clear()
    
    # 只处理 USDT/WBNB 的转账
    token_addr = log.get("address", "").lower()
    if token_addr not in (USDT, WBNB):
        return
    
    # 分析交易模式
    results = analyze_transfer_pattern(tx_hash)
    
    if not results:
        return
    
    # 输出内盘买入信息
    for result in results:
        proxy = result["proxy"]
        
        # 记录发现的 Proxy
        if proxy not in proxy_cache:
            proxy_cache[proxy] = {
                "first_seen": time.time(),
                "is_contract": is_contract(proxy)
            }
            print(f"\n🔍 发现新 Proxy: {proxy}")
        
        proxy_stats[proxy] += 1
        
        # 获取代币信息
        quote_symbol = get_token_symbol(result["quote_token"])
        target_symbol = get_token_symbol(result["target_token"])
        
        quote_decimals = get_decimals(result["quote_token"])
        target_decimals = get_decimals(result["target_token"])
        
        # 格式化数量
        quote_formatted = format_amount(result["quote_value"], quote_decimals)
        target_formatted = format_amount(result["target_value"], target_decimals)
        
        # 计算 USD 价值
        quote_value_decimal = Decimal(result["quote_value"]) / (Decimal(10) ** Decimal(quote_decimals))
        usd_value = float(quote_value_decimal) * (600 if result["quote_token"] == WBNB else 1)
        
        # 过滤小额交易
        if usd_value < MIN_USD_VALUE:
            return
        
        # 输出交易信息
        print(f"\n{'='*80}")
        print(f"🏪 【内盘】{quote_symbol} 买入 {target_symbol}")
        print(f"交易哈希: {tx_hash}")
        print(f"买家: {result['buyer']}")
        print(f"Proxy: {proxy} (累计: {proxy_stats[proxy]} 笔)")
        print(f"付出: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})")
        print(f"得到: {target_formatted} {target_symbol}")
        print(f"目标代币: {result['target_token']}")
        print(f"BSCScan: https://bscscan.com/tx/{tx_hash}")
        print(f"{'='*80}\n")


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


@lru_cache(maxsize=10000)
def get_pair_tokens(pair_address):
    """获取交易对的 token0 和 token1"""
    try:
        token0_data = rpc_call("eth_call", [{
            "to": pair_address,
            "data": "0x0dfe1681"  # token0()
        }, "latest"])
        
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


def handle_swap_event(log):
    """处理 PancakeSwap V2 Swap 事件（外盘）"""
    tx_hash = log.get("transactionHash")
    
    if tx_hash in seen_txs:
        return
    seen_txs.add(tx_hash)
    
    pair_address = log.get("address", "").lower()
    swap_data = parse_swap_data(log.get("data"))
    
    if not swap_data:
        return
    
    token0, token1 = get_pair_tokens(pair_address)
    if not token0 or not token1:
        return
    
    if token0 not in (USDT, WBNB) and token1 not in (USDT, WBNB):
        return
    
    if {token0, token1} == {USDT, WBNB}:
        return
    
    amount0_in = swap_data["amount0In"]
    amount1_in = swap_data["amount1In"]
    amount0_out = swap_data["amount0Out"]
    amount1_out = swap_data["amount1Out"]
    
    quote_token = None
    base_token = None
    quote_amount = 0
    base_amount = 0
    
    if amount0_in > 0 and amount1_out > 0:
        if token0 in (USDT, WBNB):
            quote_token = token0
            base_token = token1
            quote_amount = amount0_in
            base_amount = amount1_out
    elif amount1_in > 0 and amount0_out > 0:
        if token1 in (USDT, WBNB):
            quote_token = token1
            base_token = token0
            quote_amount = amount1_in
            base_amount = amount0_out
    
    if not quote_token or not base_token or quote_token not in (USDT, WBNB):
        return
    
    quote_decimals = get_decimals(quote_token)
    base_decimals = get_decimals(base_token)
    
    quote_symbol = get_token_symbol(quote_token)
    base_symbol = get_token_symbol(base_token)
    
    quote_formatted = format_amount(quote_amount, quote_decimals)
    base_formatted = format_amount(base_amount, base_decimals)
    
    quote_value = Decimal(quote_amount) / (Decimal(10) ** Decimal(quote_decimals))
    usd_value = float(quote_value) * (600 if quote_token == WBNB else 1)
    
    if usd_value < MIN_USD_VALUE:
        return
    
    print(f"\n{'='*80}")
    print(f"💰 【外盘】{quote_symbol} 买入 {base_symbol}")
    print(f"交易哈希: {tx_hash}")
    print(f"交易对: {pair_address}")
    print(f"付出: {quote_formatted} {quote_symbol} (≈${usd_value:.2f})")
    print(f"得到: {base_formatted} {base_symbol}")
    print(f"目标代币: {base_token}")
    print(f"BSCScan: https://bscscan.com/tx/{tx_hash}")
    print(f"{'='*80}\n")


def on_open(ws):
    print("✅ WebSocket 连接成功！")
    print(f"节点: {WS_URL[:50]}...")
    print("-" * 80)
    
    # 订阅 USDT Transfer
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": ["logs", {
            "address": USDT,
            "topics": [TOPIC_TRANSFER]
        }]
    }))
    print(f"✅ 订阅 USDT Transfer 事件")
    
    # 订阅 WBNB Transfer
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "eth_subscribe",
        "params": ["logs", {
            "address": WBNB,
            "topics": [TOPIC_TRANSFER]
        }]
    }))
    print(f"✅ 订阅 WBNB Transfer 事件")
    
    # 订阅 PancakeV2 Swap（外盘对比）
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "eth_subscribe",
        "params": ["logs", {"topics": [TOPIC_V2_SWAP]}]
    }))
    print(f"✅ 订阅 PancakeV2 Swap 事件（外盘）")
    
    print(f"\n⏳ 智能监控启动...")
    print(f"   • 自动识别内盘交易（无需预知 Proxy）")
    print(f"   • 同时监控外盘 PancakeSwap")
    print(f"   • 最小金额: ${MIN_USD_VALUE}")
    print()


def on_message(ws, msg):
    try:
        data = json.loads(msg)
    except:
        return
    
    if "id" in data and "result" in data:
        return
    
    if data.get("method") == "eth_subscription":
        result = data.get("params", {}).get("result", {})
        topics = result.get("topics") or []
        topic0 = topics[0].lower() if topics else ""
        
        # Transfer 事件
        if topic0 == TOPIC_TRANSFER:
            handle_transfer_event(result)
        
        # Swap 事件（外盘）
        elif topic0 == TOPIC_V2_SWAP:
            handle_swap_event(result)


def on_error(ws, err):
    if err:
        print(f"❌ WebSocket 错误: {err}")


def on_close(ws, code, reason):
    print(f"⚠️ 连接关闭，3秒后重连...")
    
    # 输出 Proxy 统计
    if proxy_stats:
        print("\n📊 本次会话发现的 Proxy 统计:")
        for proxy, count in sorted(proxy_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"   {proxy}: {count} 笔交易")
        print()


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
            
            # 输出最终统计
            if proxy_cache:
                print("\n" + "="*80)
                print("📊 发现的 Proxy 地址汇总:")
                print("="*80)
                for proxy, info in proxy_cache.items():
                    count = proxy_stats.get(proxy, 0)
                    is_contract = "✓" if info["is_contract"] else "✗"
                    print(f"{proxy} - {count} 笔 - 合约:{is_contract}")
                
                print("\n💡 将这些地址添加到固定监控列表:")
                print("FOURMEME_PROXIES = [")
                for proxy in proxy_cache.keys():
                    print(f'    "{proxy}",')
                print("]")
            break
        except Exception as e:
            print(f"❌ 异常: {e}")
        
        time.sleep(3)


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🚀 BSC 通用内盘交易监控")
    print("="*80)
    print("特性:")
    print("  • 智能识别内盘交易模式")
    print("  • 无需预知 Proxy 地址")
    print("  • 自动发现和记录 Proxy")
    print("  • 同时监控外盘交易对比")
    print("="*80 + "\n")
    main()

