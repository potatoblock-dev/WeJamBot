#!/usr/bin/env python3
"""并发压力测试：多个线程同时发消息，验证 UI 锁是否真的防住了键码交错。

判据：每条发出的消息在回读时必须**逐字完整**，没有任何交叉污染。
未加锁时，多个 type_text 交错会让消息变成乱码。
"""
import concurrent.futures as cf

import os
import sys

# 让脚本无论从哪个目录、无论是否 pip install 过都能 import wejam
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from wejam import Client  # noqa: E402
except ModuleNotFoundError as exc:
    # 报错里只给**仓库内相对路径**，机器相关的绝对路径对使用者没有意义
    rel = os.path.relpath(os.path.abspath(__file__), ROOT)
    sys.exit(
        f"❌ 缺少依赖 {exc.name!r}\n"
        f"   这是活体检查，请用项目虚拟环境从仓库根目录运行：\n"
        f"     make test          # 或\n"
        f"     .venv/bin/python {rel}"
    )

CHAT = "文件传输助手"
MSGS = [
    "并发测试-A-①：这条应当逐字完整，不被其它线程插入任何字符。",
    "并发测试-B-②：这条也应当逐字完整，ABCDEFG 与 1234567 都要在。",
    "并发测试-C-③：混排测试 english中文123！@# 结束。",
    "并发测试-D-④：最后一条，用于确认四条互不污染。",
]


def send(text):
    with Client() as c:
        r = c.send_text(CHAT, text)
        return text, r.ok, r.detail


print(f"并发发送 {len(MSGS)} 条 ...")
with cf.ThreadPoolExecutor(max_workers=len(MSGS)) as ex:
    results = list(ex.map(send, MSGS))

for text, ok, detail in results:
    print(f"  {'✅' if ok else '❌'} {detail}")

print("\n回读校验 ...")
with Client() as c:
    resp = c.list_messages(chat=CHAT, limit=60)
    got = [m.text for m in resp.messages if m.kind == 1]

ok_all = True
for text in MSGS:
    hit = any(text == g for g in got)
    if not hit:
        ok_all = False
        near = [g for g in got if text[:8] in g]
        print(f"  ❌ 未逐字命中: {text!r}")
        if near:
            print(f"     实际读到: {near[0]!r}")

print("\n结果:", "✅ 全部逐字完整，无交错" if ok_all else "❌ 存在污染")
sys.exit(0 if ok_all else 1)
