#!/usr/bin/env python3
"""把 docs/quickstart.md 里的示例逐段真跑一遍，确保文档没有撒谎。

这是**活体集成检查**：需要容器里的微信已登录。

用法: python tests/check_docs_examples.py
"""
import os
import sys

# 让脚本无论从哪个目录、无论是否 pip install 过都能 import wejam
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import traceback

try:
    from wejam import WeChat  # noqa: E402
except ModuleNotFoundError as exc:
    # 报错里只给**仓库内相对路径**，机器相关的绝对路径对使用者没有意义
    rel = os.path.relpath(os.path.abspath(__file__), ROOT)
    sys.exit(
        f"❌ 缺少依赖 {exc.name!r}\n"
        f"   这是活体检查，请用项目虚拟环境从仓库根目录运行：\n"
        f"     make test          # 或\n"
        f"     .venv/bin/python {rel}"
    )

FAILED = []


def section(name, fn):
    try:
        fn()
        print(f"  ✅ {name}")
    except Exception as e:
        FAILED.append(name)
        print(f"  ❌ {name}: {type(e).__name__}: {e}")
        traceback.print_exc()


wx = WeChat()

print("验证 docs/quickstart.md 的示例：")

# 获取微信实例 / 状态
section("获取微信实例 + status()", lambda: wx.status())


# 发送消息
def _send():
    ok = wx.send("文档示例验证 ✅", chat="文件传输助手")
    assert ok, "send 返回 False"
section("send()", _send)


# 获取当前聊天窗口消息
def _messages():
    msgs = wx.messages()
    assert isinstance(msgs, list)
    for m in msgs[:3]:
        print('==' * 10)
        print(m.text)
section("messages()", _messages)


# 会话列表
def _chats():
    cs = wx.chats()
    assert cs, "会话列表为空"
    for c in cs[:2]:
        print("       ", c)
section("chats()", _chats)


# 读取指定会话
section("messages(chat=...)", lambda: wx.messages(chat="文件传输助手", limit=3))


# 监听（全局，短时）
def _watch():
    got = 0
    for chat, msg in wx.watch(seconds=3):
        got += 1
        print(f"       [{chat}] {msg.sender}{msg.text}")
    print(f"       （3 秒内收到 {got} 条）")
section("watch() 全局", _watch)


# 未读徽标
def _badges():
    b = wx.badges()
    print(f"       {b['unread_sessions']} 个会话有未读，共 {b['unread_messages']} 条；"
          f"导航微信红点={b['nav'].get('微信')}")
section("badges()", _badges)


# 登录状态
section("login_state()", lambda: print("       ", wx.login_state()))


# 逃生舱：节点树
def _tree():
    t = wx.tree()
    assert len(t.nodes) > 0
    print(f"       {len(t.nodes)} 个节点")
section("tree()", _tree)


# 逃生舱：点击（用一个安全目标，导航项）
def _click():
    ok, detail = wx.click_node("push button", "通讯录")
    assert ok, detail
    print("       ", detail)
section("click_node()", _click)


# gRPC 客户端直用
def _client():
    from wejam import Client
    with Client("127.0.0.1:7700") as c:
        names = [ch.name for ch in c.list_chats().chats]
        print(f"       {len(names)} 个会话")
section("Client 直用", _client)


wx.close()
print()
if FAILED:
    print(f"❌ {len(FAILED)} 个示例失败: {FAILED}")
    sys.exit(1)
print("✅ 文档中所有示例均实际通过")
