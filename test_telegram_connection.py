"""
测试 Telegram Bot API 连接
"""
import asyncio
from telegram import Bot
from telegram.request import HTTPXRequest
import os

# Bot Token
BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '8321549857:AAHnq64MVroI23UB-DKM5lv9gOY0vG7hi8g')
CHAT_ID = os.getenv('TG_BSC_CHANNEL', '-1002569554228')


async def test_connection():
    """测试连接"""
    print("=" * 60)
    print("🔍 测试 Telegram Bot API 连接")
    print("=" * 60)
    
    # 测试 1：直接连接（无代理）
    print("\n📡 测试 1: 直接连接 Telegram API")
    try:
        request = HTTPXRequest(
            connection_pool_size=5,
            connect_timeout=10.0,
            read_timeout=10.0,
            write_timeout=10.0,
            pool_timeout=5.0
        )
        bot = Bot(token=BOT_TOKEN, request=request)
        
        print("   尝试调用 getMe...")
        me = await bot.get_me()
        print(f"   ✅ 连接成功！Bot: {me.username}")
        
        print(f"   尝试发送测试消息到 {CHAT_ID}...")
        message = await bot.send_message(
            chat_id=CHAT_ID,
            text="🧪 <b>Telegram 连接测试</b>\n\n这是一条测试消息。",
            parse_mode="HTML"
        )
        print(f"   ✅ 消息发送成功！Message ID: {message.message_id}")
        return True
        
    except Exception as e:
        print(f"   ❌ 连接失败: {type(e).__name__}")
        print(f"   详细错误: {e}")
        return False


async def test_with_proxy():
    """测试使用代理连接"""
    print("\n📡 测试 2: 使用代理连接 (127.0.0.1:1081)")
    try:
        # 注意：python-telegram-bot 不直接支持 SOCKS5
        # 需要通过环境变量或 httpx 配置
        import httpx
        
        # 创建带代理的 httpx client
        proxies = {
            "http://": "socks5://127.0.0.1:1081",
            "https://": "socks5://127.0.0.1:1081"
        }
        
        # HTTPXRequest 不支持直接配置代理
        # 需要设置环境变量
        os.environ['HTTP_PROXY'] = 'socks5://127.0.0.1:1081'
        os.environ['HTTPS_PROXY'] = 'socks5://127.0.0.1:1081'
        
        request = HTTPXRequest(
            connection_pool_size=5,
            connect_timeout=10.0,
            read_timeout=10.0,
            write_timeout=10.0,
            pool_timeout=5.0
        )
        bot = Bot(token=BOT_TOKEN, request=request)
        
        print("   尝试调用 getMe (通过代理)...")
        me = await bot.get_me()
        print(f"   ✅ 连接成功！Bot: {me.username}")
        
        print(f"   尝试发送测试消息...")
        message = await bot.send_message(
            chat_id=CHAT_ID,
            text="🧪 <b>Telegram 代理连接测试</b>\n\n通过 SOCKS5 代理发送。",
            parse_mode="HTML"
        )
        print(f"   ✅ 消息发送成功！Message ID: {message.message_id}")
        return True
        
    except Exception as e:
        print(f"   ❌ 连接失败: {type(e).__name__}")
        print(f"   详细错误: {e}")
        return False
    finally:
        # 清理环境变量
        os.environ.pop('HTTP_PROXY', None)
        os.environ.pop('HTTPS_PROXY', None)


async def test_api_endpoint():
    """测试 Telegram API 端点是否可达"""
    print("\n📡 测试 3: 测试 Telegram API 端点")
    import httpx
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    
    # 测试直连
    print(f"   测试直连: {url[:50]}...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ 直连成功: {data['result']['username']}")
                return True
            else:
                print(f"   ❌ HTTP {response.status_code}")
    except Exception as e:
        print(f"   ❌ 直连失败: {type(e).__name__} - {e}")
    
    # 测试通过代理
    print(f"\n   测试代理连接 (127.0.0.1:1081)...")
    try:
        proxies = {
            "http://": "socks5://127.0.0.1:1081",
            "https://": "socks5://127.0.0.1:1081"
        }
        async with httpx.AsyncClient(proxies=proxies, timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ 代理连接成功: {data['result']['username']}")
                return True
            else:
                print(f"   ❌ HTTP {response.status_code}")
    except Exception as e:
        print(f"   ❌ 代理连接失败: {type(e).__name__} - {e}")
    
    return False


async def main():
    """主函数"""
    print(f"\n📋 配置信息:")
    print(f"   Bot Token: {BOT_TOKEN[:20]}...")
    print(f"   Chat ID: {CHAT_ID}")
    print()
    
    # 运行测试
    result1 = await test_api_endpoint()
    result2 = await test_connection()
    
    if not result1 and not result2:
        print("\n" + "=" * 60)
        print("❌ 所有测试失败！")
        print("=" * 60)
        print("\n💡 可能原因:")
        print("   1. Docker 容器无法访问 Telegram API（被墙或防火墙）")
        print("   2. 需要配置代理")
        print("\n💡 解决方案:")
        print("   1. 在 Docker Compose 中配置 HTTP_PROXY/HTTPS_PROXY")
        print("   2. 或使用 VPN/透明代理")
        print("   3. 或使用自定义的 Telegram HTTP API 后端")
    else:
        print("\n" + "=" * 60)
        print("✅ 至少一个测试成功！")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

