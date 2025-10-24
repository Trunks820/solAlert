"""
测试 Fourmeme 内盘 Webhook 推送
获取真实交易数据并模拟 Alchemy Webhook 格式推送到服务器
"""
import sys
import os
import requests
import json
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
}

# 测试交易
TX_HASH = '0xdda88af885c4a516b63259769f4927cfb59d3e54e3fd2fb377b7221a94b974a7'

# Webhook 服务器地址（根据实际情况修改）
WEBHOOK_URL = 'http://kakarot8.fun:8001/webhook/alchemy/bsc'
# 如果是本地测试，使用: http://localhost:8001/webhook/alchemy/bsc


def fetch_transaction_data(tx_hash: str):
    """获取交易完整数据"""
    print(f"🔍 获取交易数据: {tx_hash}")
    
    collector = BSCBlockCollector(PUBLIC_BSC_RPC)
    
    # 获取交易回执
    receipt = collector.get_transaction_receipt(tx_hash)
    if not receipt:
        print("❌ 获取交易回执失败")
        return None
    
    # 获取交易数据
    tx = collector.rpc_call("eth_getTransactionByHash", [tx_hash])
    if not tx:
        print("❌ 获取交易数据失败")
        return None
    
    # 获取区块数据
    block_number = receipt.get('blockNumber', '0x0')
    block = collector.get_block(int(block_number, 16))
    if not block:
        print("❌ 获取区块数据失败")
        return None
    
    print(f"✅ 交易数据获取成功")
    print(f"   区块号: {int(block_number, 16)}")
    print(f"   Logs: {len(receipt.get('logs', []))} 个")
    print(f"   tx.value: {int(tx.get('value', '0x0'), 16) / 1e18:.6f} BNB")
    
    return {
        'transaction': tx,
        'receipt': receipt,
        'block': block
    }


def convert_to_alchemy_webhook(data: dict):
    """
    将 BSC 交易数据转换为 Alchemy Webhook 格式
    
    参考 Alchemy GraphQL 格式:
    {
      block {
        hash, number, timestamp,
        logs(filter:{topics: ["0xd78ad95..."]}) {
          topics, data, index,
          account{address},
          transaction{hash, from{address}, to{address}}
        }
      }
    }
    """
    receipt = data['receipt']
    block = data['block']
    tx = data['transaction']
    
    # 转换 logs 为 Alchemy 格式
    alchemy_logs = []
    for log in receipt.get('logs', []):
        alchemy_log = {
            'topics': log.get('topics', []),
            'data': log.get('data', '0x'),
            'index': log.get('logIndex', 0),
            'account': {
                'address': log.get('address', '')
            },
            'transaction': {
                'hash': tx.get('hash', ''),
                'from': {
                    'address': tx.get('from', '')
                },
                'to': {
                    'address': tx.get('to', '')
                }
            }
        }
        alchemy_logs.append(alchemy_log)
    
    # 构造 Alchemy Webhook 格式
    webhook_data = {
        'webhookId': 'test-fourmeme-internal',
        'id': 'test-event-id',
        'createdAt': '2024-01-01T00:00:00.000Z',
        'type': 'GRAPHQL',
        'event': {
            'data': {
                'block': {
                    'hash': block.get('hash', ''),
                    'number': block.get('number', '0x0'),
                    'timestamp': block.get('timestamp', '0x0'),
                    'logs': alchemy_logs
                }
            },
            'sequenceNumber': '1000000'
        }
    }
    
    return webhook_data


def send_webhook(webhook_data: dict, url: str):
    """发送 Webhook 到服务器"""
    print(f"\n📤 推送 Webhook 到: {url}")
    print(f"   区块: {webhook_data['event']['data']['block']['number']}")
    print(f"   Logs: {len(webhook_data['event']['data']['block']['logs'])} 个")
    
    # 创建 session 并禁用代理
    session = requests.Session()
    session.trust_env = False  # 🔥 禁用系统代理
    
    try:
        response = session.post(
            url,
            json=webhook_data,
            headers={'Content-Type': 'application/json'},
            timeout=30,
            proxies={'http': None, 'https': None}  # 🔥 明确禁用代理
        )
        
        if response.status_code == 200:
            print(f"✅ Webhook 推送成功!")
            print(f"   状态码: {response.status_code}")
            try:
                result = response.json()
                print(f"   响应: {json.dumps(result, indent=2)}")
            except:
                print(f"   响应: {response.text[:200]}")
        else:
            print(f"❌ Webhook 推送失败")
            print(f"   状态码: {response.status_code}")
            print(f"   响应: {response.text[:500]}")
        
        return response
    
    except requests.exceptions.ConnectionError as e:
        print(f"❌ 连接失败: 无法连接到 {url}")
        print(f"   请确认服务器地址正确且服务正在运行")
        print(f"   错误: {e}")
        return None
    
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return None


def main():
    print("=" * 80)
    print("🧪 Fourmeme 内盘 Webhook 测试")
    print("=" * 80)
    
    # 1. 获取交易数据
    data = fetch_transaction_data(TX_HASH)
    if not data:
        return
    
    # 2. 转换为 Alchemy Webhook 格式
    print("\n🔄 转换为 Alchemy Webhook 格式...")
    webhook_data = convert_to_alchemy_webhook(data)
    print("✅ 格式转换完成")
    
    # 3. 保存到本地（可选）
    output_file = 'fourmeme_webhook_test.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(webhook_data, f, indent=2, ensure_ascii=False)
    print(f"💾 Webhook 数据已保存到: {output_file}")
    
    # 4. 推送到服务器
    print("\n" + "=" * 80)
    print("📡 开始推送测试")
    print("=" * 80)
    
    response = send_webhook(webhook_data, WEBHOOK_URL)
    
    if response and response.status_code == 200:
        print("\n✅ 测试成功！")
        print("\n💡 现在查看服务器日志:")
        print("   docker-compose logs -f solalert | grep -i 'fourmeme\\|内盘\\|processor'")
        print("\n   应该能看到:")
        print("   └─ 🟡 Fourmeme 内盘: X 笔")
        print("   └─ [1] 🟡内盘 0xXXXX... | $XXX.XX USDT")
    else:
        print("\n❌ 测试失败")
        print("\n💡 请检查:")
        print("   1. 服务器地址是否正确")
        print("   2. 服务是否正在运行")
        print("   3. 防火墙是否开放端口 8001")
    
    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()

