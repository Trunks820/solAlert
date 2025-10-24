"""
Fourmeme 终极方案：内盘+外盘统一处理
按照用户指导实现：分流 + tx.value + Transfer 对账
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.solalert.collectors.bsc_collector import BSCBlockCollector

# 使用公共 BSC RPC
PUBLIC_BSC_RPC = {
    'rpc_endpoints': [
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.binance.org",
    ],
    'confirmations': 12,
    'poll_interval': 0.8,
    'pancake_v2_factory': '0xca143ce32fe78f1f7019d7d551a6402fc5350c73',
    'usdt_address': '0x55d398326f99059ff775485246999027b3197955',
    'wbnb_address': '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
    'usdt_wbnb_pair': '0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae',
    'reserve_fresh_seconds': 15,
}

# 地址常量
FOURMEME_PROXY = '0x5c952063c7fc8610ffdb798152d69f0b9550762b'.lower()
PANCAKE_V2_FACTORY = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73'.lower()
USDT = '0x55d398326f99059ff775485246999027b3197955'.lower()
WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'.lower()

# ERC20 Transfer 事件 topic
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


def parse_all_transfers(logs):
    """解析所有 Transfer 事件"""
    transfers = []
    for log in logs:
        topics = log.get('topics', [])
        if topics and topics[0].lower() == TRANSFER_TOPIC:
            if len(topics) != 3:
                continue
            
            from_addr = "0x" + topics[1][-40:]
            to_addr = "0x" + topics[2][-40:]
            token = log.get('address', '').lower()
            data = log.get('data', '0x')
            
            try:
                value = int(data[2:], 16) if len(data) > 2 else 0
            except:
                value = 0
            
            transfers.append({
                'from': from_addr.lower(),
                'to': to_addr.lower(),
                'value': value,
                'token': token
            })
    
    return transfers


def is_fourmeme_internal(receipt):
    """
    识别是否是 Fourmeme 内盘交易
    只要 logs 里出现过 Fourmeme Proxy 地址，就认为是内盘
    """
    for log in receipt.get("logs", []):
        if log.get("address", "").lower() == FOURMEME_PROXY:
            return True
    return False


def get_base_amount_for_fourmeme(tx, transfers):
    """
    获取 Fourmeme 内盘的基准币金额
    
    Returns:
        (base_symbol, base_amount_wei)
    """
    # 1) 原生 BNB（tx.value）
    tx_value_hex = tx.get("value", "0x0")
    if isinstance(tx_value_hex, str):
        tx_value = int(tx_value_hex, 16) if tx_value_hex.startswith('0x') else int(tx_value_hex)
    else:
        tx_value = int(tx_value_hex)
    
    if tx_value > 0:
        return "BNB", tx_value
    
    # 2) USDT
    usdt_in = sum(t['value'] for t in transfers
                  if t['token'] == USDT and t['to'] == FOURMEME_PROXY)
    if usdt_in > 0:
        return "USDT", usdt_in
    
    # 3) WBNB
    wbnb_in = sum(t['value'] for t in transfers
                  if t['token'] == WBNB and t['to'] == FOURMEME_PROXY)
    if wbnb_in > 0:
        return "WBNB", wbnb_in
    
    return None, 0


def pick_target_token_fourmeme(transfers, tx_from):
    """
    找出目标代币（Fourmeme 内盘）
    
    Args:
        transfers: 所有 Transfer 事件
        tx_from: 交易发起人地址
    
    Returns:
        目标代币地址，如果找不到返回 None
    """
    # 找 to == tx_from 的非基准币转账（代币流向用户 = 买入）
    to_user = [t for t in transfers 
               if t['to'] == tx_from.lower() 
               and t['token'] not in (USDT, WBNB)]
    
    if to_user:
        # 取金额最大的
        return max(to_user, key=lambda x: x['value'])
    
    # 找 from == tx_from 的非基准币转账（代币流出用户 = 卖出）
    from_user = [t for t in transfers 
                 if t['from'] == tx_from.lower() 
                 and t['token'] not in (USDT, WBNB)]
    
    if from_user:
        return max(from_user, key=lambda x: x['value'])
    
    # 如果都找不到，取所有非基准币中金额最大的
    non_base = [t for t in transfers if t['token'] not in (USDT, WBNB)]
    if non_base:
        return max(non_base, key=lambda x: x['value'])
    
    return None


def analyze_fourmeme_internal(tx, receipt, transfers, collector):
    """分析 Fourmeme 内盘交易"""
    print("\n🟡 ========== Fourmeme 内盘模式 ==========")
    
    tx_from = tx.get('from', '').lower()
    tx_to = tx.get('to', '').lower()
    
    print(f"   交易发起人: {tx_from}")
    print(f"   交易目标: {tx_to}")
    
    # 1. 获取基准币金额
    base_sym, base_amt = get_base_amount_for_fourmeme(tx, transfers)
    
    if not base_sym:
        print("   ❌ 未找到基准币支付")
        return
    
    # 转换为可读金额
    if base_sym == "BNB":
        base_decimals = 18
    else:
        base_decimals = collector.get_decimals(USDT if base_sym == "USDT" else WBNB)
    
    base_amount = base_amt / (10 ** base_decimals)
    
    print(f"   💰 基准币: {base_sym}")
    print(f"   💵 金额: {base_amount:,.4f} {base_sym}")
    
    # 2. 找出目标代币
    target = pick_target_token_fourmeme(transfers, tx_from)
    
    if not target:
        print("   ⚠️  未找到目标代币")
        return
    
    # 获取目标代币信息
    target_token_addr = target['token']
    target_symbol = collector.get_token_symbol(target_token_addr)
    target_decimals = collector.get_decimals(target_token_addr)
    target_amount = target['value'] / (10 ** target_decimals)
    
    # 判断方向
    if target['to'] == tx_from:
        direction = "🟢 买入"
    elif target['from'] == tx_from:
        direction = "🔴 卖出"
    else:
        direction = "⚪ 未知"
    
    print(f"   🎯 目标代币: {target_symbol} ({target_token_addr[:10]}...)")
    print(f"   📊 数量: {target_amount:,.4f} {target_symbol}")
    print(f"   📍 方向: {direction}")
    
    # 计算单价（如果是买入）
    if direction == "🟢 买入" and target_amount > 0:
        unit_price = base_amount / target_amount
        print(f"   💎 单价: {unit_price:.8f} {base_sym}/{target_symbol}")


def analyze_transaction_final(tx_hash: str):
    """终极交易分析（内盘+外盘）"""
    print("=" * 80)
    print(f"🔍 终极分析: {tx_hash}")
    print("=" * 80)
    
    collector = BSCBlockCollector(PUBLIC_BSC_RPC)
    
    # 获取交易和回执
    print("\n【步骤 1】获取交易数据...")
    tx = collector.rpc_call("eth_getTransactionByHash", [tx_hash])
    receipt = collector.get_transaction_receipt(tx_hash)
    
    if not tx or not receipt:
        print("❌ 获取交易数据失败")
        return
    
    print(f"✅ 交易数据已获取")
    print(f"   From: {tx.get('from', 'N/A')}")
    print(f"   To: {tx.get('to', 'N/A')}")
    print(f"   Value: {int(tx.get('value', '0x0'), 16) / 1e18:.6f} BNB")
    print(f"   Logs: {len(receipt.get('logs', []))} 个")
    
    # 解析所有 Transfer
    print("\n【步骤 2】解析 Transfer 事件...")
    transfers = parse_all_transfers(receipt.get('logs', []))
    print(f"✅ 找到 {len(transfers)} 个 Transfer 事件")
    
    # 分流：内盘 vs 外盘
    print("\n【步骤 3】识别交易类型...")
    if is_fourmeme_internal(receipt):
        print("🟡 识别为: Fourmeme 内盘")
        analyze_fourmeme_internal(tx, receipt, transfers, collector)
    else:
        print("🟢 识别为: 外盘（Pancake 等 DEX）")
        print("   （外盘分析逻辑待实现）")
    
    print("\n" + "=" * 80)
    print("✅ 分析完成")
    print("=" * 80)


if __name__ == '__main__':
    # 测试 Fourmeme 内盘买入交易
    TX_FOURMEME_BUY = '0x6c1462db54e82e3bbb93dbb5b7586a8ff0853e89d082a21970271170376b27c3'
    
    analyze_transaction_final(TX_FOURMEME_BUY)

