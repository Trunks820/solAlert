"""
测试Jupiter API原始响应
"""
import requests
import json

# 测试Token（测试多个）
test_tokens = [
    ("DEAR", "3vreYfM6AxbEKdisdpuGpkPpxmtXn6Ema9unhxympump"),
    ("SOL", "So11111111111111111111111111111111111111112"),  # Wrapped SOL
    ("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),  # USDC
]

# 是否使用代理（先尝试不用代理）
USE_PROXY = False  # 改为False试试直连

if USE_PROXY:
    proxies = {
        'http': 'socks5://127.0.0.1:1081',
        'https': 'socks5://127.0.0.1:1081'
    }
else:
    proxies = None

print("="*80)
print(f"🔍 测试Jupiter API（直连，测试多个Token）")
print("="*80)
print()

for name, ca in test_tokens:
    print("="*80)
    print(f"🔍 测试Token: {name}")
    print(f"CA: {ca}")
    print("="*80)

    # 测试API
    url = f"https://lite-api.jup.ag/tokens/v2/search"
    params = {"query": ca}

    print(f"\n📡 请求URL: {url}")
    print(f"📋 参数: {params}")
    if USE_PROXY:
        print(f"🔧 代理: socks5://127.0.0.1:1081")
    else:
        print(f"🔧 代理: 无（直连）")
    print("\n发送请求...")

    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=15)
        
        print(f"✅ 状态码: {response.status_code}")
        print(f"📦 响应大小: {len(response.text)} bytes")
        print("\n" + "="*80)
        print("📄 原始响应:")
        print("="*80)
        
        # 格式化打印JSON
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        
        print("\n" + "="*80)
        print("🔍 数据分析:")
        print("="*80)
        
        if isinstance(data, list):
            print(f"✅ 返回数组，长度: {len(data)}")
            if len(data) > 0:
                first = data[0]
                print(f"\n第一个元素的字段:")
                for key in first.keys():
                    print(f"  - {key}")
                
                if 'stats5m' in first:
                    print(f"\n✅ 包含 stats5m 字段")
                    stats = first['stats5m']
                    print(f"   - priceChange: {stats.get('priceChange')}")
                    print(f"   - holderChange: {stats.get('holderChange')}")
                    print(f"   - volumeChange: {stats.get('volumeChange')}")
                else:
                    print(f"\n❌ 不包含 stats5m 字段")
            else:
                print(f"⚠️ 数组为空")
        else:
            print(f"⚠️ 返回类型: {type(data)}")
            
    except Exception as e:
        print(f"❌ 请求失败: {e}")
    
    print("\n" + "="*80)
    print()  # 空行分隔

