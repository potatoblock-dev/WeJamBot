#!/usr/bin/env python3
"""多群值守机器人 —— 一个完整可用的用例。

做的事：
    值守几个群 → 常驻独立窗口（免切换）→ 全局监听 → 命中关键词
    → 查发送者 → 读上下文 → 回复

为什么这么写：
    * **全局监听**贵在便宜：只读会话列表预览，覆盖所有会话，
      且**不打开任何窗口** —— 不动界面、不清你的未读。
    * **独立窗口**贵在省时：对常驻会话读/发不再有切换开销。
      实测 0.9~1.8 秒 vs 需要切换时 4.8~7.7 秒。
      开窗口一次性约 6 秒，所以只常驻**真正高频**的那几个。
    * **写入串行**：X11 只有一个键盘焦点，多窗口不能并发发送
      （服务端已经串行化了，调用方不用管）。

    python examples/bot_multi_group.py                 # 只观察，不回复
    python examples/bot_multi_group.py --live           # 真的回复
    python examples/bot_multi_group.py --seconds 120    # 值守时长
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wejam import WeChat  # noqa: E402

# ⚠️ 只放你允许机器人替你说话的会话。加真实群 = 真的替你发言。
WATCH_GROUPS = ["1145", "土豆方块纯生存服"]

# 命中这些词才响应
KEYWORDS = ("机器人", "@bot", "在吗")

# 回复前缀，便于识别机器人自己的消息（服务端也会用 from_self 标注，这只是可读性）
PREFIX = "[bot] "


def setup(wx):
    """把值守会话常驻到独立窗口。返回实际常驻成功的列表。"""
    resident = []
    for g in WATCH_GROUPS:
        if g in wx.windows():
            resident.append(g)
            continue
        t0 = time.time()
        if wx.open_window(g):
            print(f"  常驻 {g[:20]:22} ({(time.time()-t0)*1000:.0f}ms)")
            resident.append(g)
        else:
            print(f"  ⚠️ {g!r} 常驻失败，跳过（会话名要对得上）")
    return resident


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真的回复")
    ap.add_argument("--seconds", type=int, default=60, help="值守时长")
    ap.add_argument("--any", action="store_true",
                    help="任意消息都算命中（观察用，不筛关键词）")
    ap.add_argument("--target", default="127.0.0.1:7700")
    args = ap.parse_args()

    wx = WeChat(args.target)
    print(f"多群值守机器人   →   {args.target}   "
          f"{'（live：会回复）' if args.live else '（只观察）'}")

    print("\n① 常驻会话（一次性成本，之后长期省时）")
    resident = setup(wx)
    if not resident:
        sys.exit("没有可值守的会话，改一下 WATCH_GROUPS")
    print(f"   常驻窗口: {wx.windows()}")

    print(f"\n② 全局监听 {args.seconds} 秒（不打开任何窗口，不动界面）")
    rule = "任意消息" if args.any else f"关键词 {KEYWORDS}"
    print(f"   命中规则: {rule}   值守: {resident}\n")

    deadline = time.time() + args.seconds
    seen = replied = 0

    for chat, msg in wx.watch(seconds=args.seconds):
        if time.time() >= deadline:
            break
        if msg.from_self:                 # 自己发的（服务端已溯源）
            continue
        if chat not in resident:          # 不在值守范围
            continue
        if not args.any and not any(k in msg.text for k in KEYWORDS):
            continue

        seen += 1
        who = f"{msg.sender} " if msg.sender else ""
        print(f"   命中 [{chat}] {who}{msg.text[:32]!r}")

        # ③ 先读上下文（会话已常驻，无切换开销）
        t0 = time.time()
        recent = wx.messages(chat=chat, limit=8)
        t_read = (time.time() - t0) * 1000

        # ④ 在完整列表里匹配出这条消息，拿**它自己的 index**
        #    —— 监听事件里的 index 来自会话列表预览，对消息列表没有意义。
        target = None
        for m in reversed(recent):
            if m.kind == 1 and m.text.strip() == msg.text.strip():
                target = m
                break

        sender, t_sender, why = "", 0.0, ""
        if target is not None:
            t0 = time.time()
            r = wx._c.message_sender(chat, target.index)
            t_sender = (time.time() - t0) * 1000
            if r.ok:
                sender = r.name
            else:
                why = r.detail          # 例如「该会话开了独立窗口…」

        print(f"        上下文: {len(recent)} 条 ({t_read:.0f}ms)"
              f"   匹配到 index={target.index if target else None}"
              f"   发送者: {sender!r} ({t_sender:.0f}ms)")
        if why:
            print(f"        （查询发送者失败：{why[:50]}）")
        for m in recent[-3:]:
            mine = " ←自己" if m.from_self else ""
            print(f"          [{m.content_type:7}] {m.text[:38]!r}{mine}")

        # ⑤ 回复
        if args.live:
            reply = f"{PREFIX}{sender or '收到'}，看到了"
            t0 = time.time()
            ok = wx.send(reply, chat=chat)
            print(f"        回复: {'✅' if ok else '❌'} {(time.time()-t0)*1000:.0f}ms")
            replied += 1
        else:
            print(f"        [dry-run] 将回复: {PREFIX}{sender or '收到'}，看到了")

    print(f"\n③ 结束：命中 {seen} 条，"
          f"{'回复 ' + str(replied) + ' 条' if args.live else '未回复（加 --live）'}")
    print(f"   常驻窗口保留: {wx.windows()}（下次直接用，不必再等开窗）")
    print(f"   关闭: python -m wejam.cli window close <会话>")
    wx.close()


if __name__ == "__main__":
    main()
