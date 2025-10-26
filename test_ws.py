import json, time, websocket
from collections import deque

websocket.enableTrace(False)
WS_URL = "wss://bsc-mainnet.core.chainstack.com/dad7315aaedc9e1276bc2ac49ebd2556"
FOURMEME_PROXY = "0x5c952063c7fc8610ffdb798152d69f0b9550762b".lower()
TOPIC_V2_SWAP   = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

def on_open(ws):
    print("open, subscribing ...")
    ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_subscribe",
                        "params":["logs", {"address": FOURMEME_PROXY}]}))
    ws.send(json.dumps({"jsonrpc":"2.0","id":2,"method":"eth_subscribe",
                        "params":["logs", {"topics":[TOPIC_V2_SWAP]}]}))

def on_message(ws, msg):
    data = json.loads(msg)
    if "id" in data and "result" in data:
        print("sub ack:", data)  # 订阅成功会回 subscription id
        return
    if data.get("method") == "eth_subscription":
        res = data["params"]["result"]
        addr  = (res.get("address") or "").lower()
        topic0 = (res.get("topics") or [None])[0]
        
        # 打印完整的 Log 数据结构
        print("\n" + "="*80)
        print("📦 完整 Log 数据:")
        print(json.dumps(res, indent=2))
        print("="*80 + "\n")
        
        if addr == FOURMEME_PROXY:
            print("✅ PROXY log:", res.get("transactionHash"), res.get("logIndex"))
        elif topic0 == TOPIC_V2_SWAP:
            print("✅ V2 SWAP:", res.get("transactionHash"), res.get("logIndex"))

def on_error(ws,e): print("error:", e)
def on_close(ws,c,r): print("closed:", c, r)

ws = websocket.WebSocketApp(
    WS_URL, on_open=on_open, on_message=on_message,
    on_error=on_error, on_close=on_close,
)
ws.run_forever(ping_interval=25, ping_timeout=10, origin="https://chainstack.com")
