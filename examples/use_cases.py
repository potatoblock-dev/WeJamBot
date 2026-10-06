#!/usr/bin/env python3
"""WeJamBot 用例集：每个用例都是可直接抄的完整写法。

    python examples/use_cases.py            # 只跑只读用例
    python examples/use_cases.py --live     # 含真实发送（请先确认目标会话）

为什么要用「独立窗口」：
    主窗口一次只渲染一个会话。要在别的会话里读或发，得先切换 ——
    实测切换要 **3~7 秒**（重新渲染 + 等稳定），而会话已经在眼前时只要 **0.7~1 秒**。
    把常用会话常驻独立窗口，之后每次操作都省下这几秒。

✅ 独立窗口**能同时开多个**（实测 3 个并存）。
   常驻几个常用会话，之后对它们的读/发都不再有切换开销。
   代价：每开一个要一次性约 6 秒，之后长期受益。

⚠️ 写操作**必须串行**：X11 只有一个键盘焦点。
   实测用 XSendEvent 想绕过焦点直接投递给目标窗口也不行
   （应用按自己内部的焦点状态路由，无视事件地址）。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wejam import WeChat  # noqa: E402

# 按需改成你自己的会话
WATCH = "1145"                 # 要盯着的会话
REPLY_TO = "1145"              # 允许自动回复的会话（白名单，别乱加）
KEYWORDS = ("机器人", "bot")


def hr(title):
    print()
    print(f"──── {title} " + "─" * max(0, 56 - len(title)))


def timed(fn):
    t0 = time.time()
    out = fn()
    return out, (time.time() - t0) * 1000


# ---------------------------------------------------------------------------
def case1_resident(wx, live):
    """用例 1：常驻会话，之后每次读/发都免切换。

    适合：机器人固定服务几个会话（客服群、通知群）。
    """
    hr("用例 1 · 常驻会话免切换")

    if WATCH not in wx.windows():
        ok, dt = timed(lambda: wx.open_window(WATCH))
        print(f"  开独立窗口: {'✅' if ok else '❌'}  （一次性 {dt:.0f}ms，之后就一直省）")
    print(f"  当前独立窗口: {wx.windows()}")

    for i in range(3):
        ms, dt = timed(lambda: wx.messages(chat=WATCH, limit=8))
        print(f"  第 {i+1} 次读「{WATCH}」: {dt:6.0f}ms  {len(ms)} 条   ← 没有切换开销")

    # 对照：换一个没有独立窗口的会话
    others = [c.name for c in wx.chats() if c.name not in wx.windows()]
    if others:
        other = others[0]
        _, dt = timed(lambda: wx.messages(chat=other, limit=8))
        print(f"  对照读「{other}」(需切换): {dt:6.0f}ms")


# ---------------------------------------------------------------------------
def case2_two_chats(wx, live):
    """用例 2：多个常驻窗口，读它们全部免切换。

    适合：机器人固定服务多个群/多个通知源。
    开窗口有一次性成本（约 6 秒/个），所以只常驻**真正高频**的那几个。
    """
    hr("用例 2 · 多会话常驻，全部免切换")

    resident = [WATCH] + [c.name for c in wx.chats()
                          if c.name != WATCH][:2]

    for n in resident:
        if n not in wx.windows():
            t0 = time.time(); wx.open_window(n)
            print(f"  常驻 {n[:18]:20} （一次性 {(time.time()-t0)*1000:.0f}ms）")
    print(f"  常驻窗口: {wx.windows()}")

    print("  —— 读它们全部无切换开销 ——")
    for name in wx.windows():
        ms, dt = timed(lambda n=name: wx.messages(chat=n, limit=5))
        print(f"  {name[:18]:20} {dt:6.0f}ms  {len(ms)} 条")

    print("  —— 对照：无常驻窗口的会话 ——")
    for c in wx.chats():
        if c.name in wx.windows():
            continue
        ms, dt = timed(lambda n=c.name: wx.messages(chat=n, limit=5))
        print(f"  {c.name[:18]:20} {dt:6.0f}ms  {len(ms)} 条   ← 每次都要切换")
        break


# ---------------------------------------------------------------------------
def case3_watch_then_deep_read(wx, live):
    """用例 3：全局监听（便宜）→ 命中才深入读（贵）。

    这是最实用的组合：
      * 全局监听只读会话列表预览，覆盖所有会话，**不打开任何窗口**
      * 只有需要看完整正文时，才去读那个会话
    """
    hr("用例 3 · 全局监听 → 按需深入")

    print("  监听 8 秒（不打开任何会话，不动界面）…")
    hits = []
    for chat, msg in wx.watch(seconds=8):
        who = f"{msg.sender}: " if msg.sender else ""
        print(f"    [{chat}] {who}{msg.text[:34]}  未读{0}")
        hits.append((chat, msg))

    if not hits:
        print("    （8 秒内没有新消息）")
        return

    print(f"  命中 {len(hits)} 条 → 只对第一条深入读完整正文：")
    chat = hits[0][0]
    ms, dt = timed(lambda: wx.messages(chat=chat, limit=5))
    print(f"    {chat} 读完整正文 {dt:6.0f}ms")
    for m in ms[-3:]:
        print(f"      [{m.content_type:7}] {m.text[:40]!r}")


# ---------------------------------------------------------------------------
def case4_reply_bot(wx, live):
    """用例 4：最小自动回复机器人（白名单 + 关键词 + 免前缀防循环）。

    安全点：
      * 只在白名单会话里回复
      * `msg.from_self` 由服务端溯源，不需要给消息加 [bot] 前缀
      * 目标会话常驻独立窗口，回复不用切换
    """
    hr("用例 4 · 自动回复机器人")

    if REPLY_TO not in wx.windows():
        wx.open_window(REPLY_TO)

    print(f"  白名单: {REPLY_TO}   关键词: {KEYWORDS}")
    print("  监听 8 秒…")
    replied = 0
    for chat, msg in wx.watch(seconds=8):
        if msg.from_self:            # 自己发的，跳过（服务端已溯源）
            continue
        if chat != REPLY_TO:         # 不在白名单，不回
            continue
        if not any(k in msg.text for k in KEYWORDS):
            continue
        who = wx.sender_of(msg.index, chat=chat) if msg.preview else ""
        print(f"    命中: {who or '?'} 说 {msg.text[:30]!r}")
        if live:
            ok, dt = timed(lambda: wx.send(f"收到「{msg.text[:20]}」", chat=chat))
            print(f"    回复: {'✅' if ok else '❌'}  {dt:.0f}ms")
            replied += 1

    if not replied:
        print("  （没有命中；加 --live 才会真的发）")


# ---------------------------------------------------------------------------
def case5_safety(wx, live):
    """用例 5：安全机制 —— 危险动作默认被拒。"""
    hr("用例 5 · 安全机制")

    ms = wx.messages(chat=WATCH, limit=8)
    tgt = [m for m in ms if m.content_type == "text"]
    idx = tgt[-1].index if tgt else (ms[-1].index if ms else 0)
    ok, detail = wx.message_action(idx, "删除", chat=WATCH)
    print(f"  删除 → {'执行了' if ok else '被拒'}: {detail}")
    print("  （「转发…」这类会弹出选择框的动作不在演示范围 —— 会留下待关闭的浮层）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="允许真实发送")
    ap.add_argument("--target", default="127.0.0.1:7700")
    args = ap.parse_args()

    wx = WeChat(args.target)
    print(f"WeJamBot 用例集   →   {args.target}   {'（live）' if args.live else '（只读）'}")

    for fn in (case1_resident, case2_two_chats, case3_watch_then_deep_read,
               case4_reply_bot, case5_safety):
        try:
            fn(wx, args.live)
        except Exception as exc:
            print(f"  用例失败: {type(exc).__name__}: {exc}")

    hr("收尾")
    print(f"  独立窗口: {wx.windows()}")
    print("  关闭: python -m wejam.cli window close <会话>")
    wx.close()


if __name__ == "__main__":
    main()
