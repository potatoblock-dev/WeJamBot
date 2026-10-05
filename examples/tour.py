#!/usr/bin/env python3
"""WeJamBot 能力巡览：把每一项能力都真跑一遍。

    python examples/tour.py                # 只读能力全跑，发送为 dry-run
    python examples/tour.py --live --chat 文件传输助手

设计原则贯穿全篇（每节末尾标了它体现了哪条）：
    * 不用 OCR —— 无障碍树里就有结构化文本
    * 零静态坐标 —— 位置由运行时映射导出
    * 服务持有状态 —— 客户端只消费契约
    * 验证而非假设 —— 发送后回读、渲染稳定才读
    * 静默失败是最贵的错误 —— 宁可报错也不给错数据
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from wejam import WeChat
except ModuleNotFoundError as exc:  # pragma: no cover
    sys.exit(f"❌ 缺少依赖 {exc.name!r}，请用 .venv/bin/python 运行")

W = 66


def section(n, title):
    print()
    print("═" * W)
    print(f"  {n}. {title}")
    print("═" * W)


def note(*lines):
    for ln in lines:
        print(f"     💡 {ln}")


def timed(fn):
    t0 = time.time()
    out = fn()
    return out, (time.time() - t0) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真的发送消息（默认 dry-run）")
    ap.add_argument("--chat", default="文件传输助手", help="发送与读取的目标会话")
    ap.add_argument("--target", default="127.0.0.1:7700")
    args = ap.parse_args()

    wx = WeChat(args.target)
    print(f"WeJamBot 能力巡览   →   {args.target}")

    # 目标会话可能不存在（比如被「不显示」掉了）——换成会话列表里第一个可读的。
    # 这里刻意**不吞掉错误**：找不到就明说，而不是随便读一个会话冒充它。
    available = [c.name for c in wx.chats()]
    if args.chat not in available:
        fallback = next((n for n in available if n != "公众号"), None)
        print(f"⚠️  目标会话 {args.chat!r} 不在会话列表里（可能在微信里被「不显示」了）")
        if fallback:
            print(f"    改用 {fallback!r} 继续演示")
            args.chat = fallback
        else:
            sys.exit("❌ 没有可用的会话")

    # ---------------------------------------------------------------- 1
    section(1, "服务与登录状态")
    st, ms = timed(wx.status)
    print(f"     微信版本   : {st['version']}")
    print(f"     登录阶段   : {st['login']}")
    print(f"     服务已运行 : {st['uptime_ms'] / 1000:.0f}s")
    print(f"     （耗时 {ms:.0f}ms）")
    note("版本号只从 dpkg/进程参数读，不去点「设置 → 关于微信」——",
         "自主导航界面换来一个字符串，不值得冒误触的风险。")

    # ---------------------------------------------------------------- 2
    section(2, "会话列表")
    chats, ms = timed(wx.chats)
    print(f"     共 {len(chats)} 个会话（耗时 {ms:.0f}ms）")
    for c in chats[:6]:
        tags = []
        if c.pinned:
            tags.append("置顶")
        if c.muted:
            tags.append("免打扰")
        if c.unread:
            tags.append(f"未读{c.unread}")
        tag = f"  [{' '.join(tags)}]" if tags else ""
        print(f"     {c.name[:20]:22} {c.last_message[:24]:26} {c.time:>6}{tag}")
    note("会话列表只在「微信」页签下存在。切到通讯录后会读不到 ——",
         "服务端会自动切回来，而不是静默返回空列表。")

    # ---------------------------------------------------------------- 3
    section(3, f"读取消息 + 内容类型（{args.chat}）")
    msgs, ms = timed(lambda: wx.messages(chat=args.chat, limit=12))
    print(f"     {len(msgs)} 条（耗时 {ms:.0f}ms）")
    kinds = {}
    for m in msgs:
        kinds[m.content_type] = kinds.get(m.content_type, 0) + 1
    print(f"     内容类型分布: {kinds}")
    for m in [x for x in msgs if x.kind == 1][-4:]:
        mine = "  ← 自己发的" if m.from_self else ""
        print(f"     [{m.content_type:8}] {m.text[:38]!r}{mine}")
    note("非文本消息（图片/文件/链接/表情…）微信渲染成带标记的占位文本，",
         "据此分类；纯占位做全等匹配，避免把用户真打出「图片」误判。",
         "from_self 由服务端溯源，客户端不需要加 [bot] 前缀。")

    # ---------------------------------------------------------------- 4
    section(4, "会话信息：类型 + 群成员数")
    info, ms = timed(lambda: wx.chat_info(chat=args.chat))
    print(f"     {info}   （耗时 {ms:.0f}ms）")
    for g in ("1145", "土豆方块PotatoblockMC"):
        try:
            gi = wx.chat_info(chat=g)
            if gi.get("chat_type") == "group":
                print(f"     {g:22} group, {gi['member_count']} 人")
        except Exception:
            pass
    note("群成员数就在聊天头部的「名称(14)」标签里，一直都有，只是没去取。")

    # ---------------------------------------------------------------- 5
    section(5, "群聊消息是谁发的")
    group = None
    for c in chats:
        try:
            if wx.chat_info(chat=c.name).get("chat_type") == "group":
                group = c.name
                break
        except Exception:
            continue
    if group:
        gm = [m for m in wx.messages(chat=group, limit=6) if m.kind == 1]
        for m in gm[-2:]:
            t0 = time.time()
            who = wx.sender_of(m.index, chat=group)
            print(f"     {m.text[:20]!r:24} → {who!r}   ({(time.time()-t0)*1000:.0f}ms)")
        note("消息列表项在无障碍树里没有发送者节点，界面上那个昵称是画出来的。",
             "做法：点发送者头像 → 读弹出的资料卡（卡片节点是暴露的）。",
             "代价约 1.5~2 秒/条，所以是按需查询，不适合整屏批量。")

    # ---------------------------------------------------------------- 6
    section(6, "发送消息")
    text = "WeJamBot 巡览：中文、标点，以及 emoji 🚀"
    if args.live:
        ok, ms = timed(lambda: wx.send(text, chat=args.chat))
        print(f"     wx.send({text[:16]!r}..., chat={args.chat!r})")
        print(f"     → {'✅ 发送成功' if ok else '❌ 发送失败'}   （耗时 {ms:.0f}ms）")
        back = wx.messages(chat=args.chat, limit=6)
        hit = any(text == m.text for m in back)
        print(f"     回读确认: {'✅ 最后几条里找到了它' if hit else '⚠️ 没在最后几条里找到'}")
        msgs = back   # 第 7 节就拿刚才这条做复制演示
    else:
        print(f"     [dry-run] 将调用 wx.send({text[:16]!r}..., chat={args.chat!r})")
        print("     （加 --live 才会真的发。默认不发，避免给真实会话发测试消息）")
    note("中文走键码重映射注入，不经过任何输入法 ——",
         "宿主输入法会劫持合成按键，实测把字母变成拼音乱码。",
         "发送后回读输入框确认已清空，不假设「打了回车就是发出去了」。")

    # ---------------------------------------------------------------- 7
    section(7, "消息级右键动作")
    # 挑一条**正文**消息：表情/图片等的菜单里没有「复制」，拿它们演示会看不出效果。
    # 当前会话没有就换一个会话找 —— 让演示不依赖某个会话恰好有文本消息。
    tgt, probe_chat = [m for m in msgs if m.content_type == "text"], args.chat
    if not tgt and args.live:
        # --live 模式下我们刚发过一条正文，正好用它演示复制
        fresh = [m for m in wx.messages(chat=args.chat, limit=6)
                 if m.content_type == "text"]
        if fresh:
            tgt, probe_chat = fresh, args.chat
    if not tgt:
        for cand in available[:6]:
            if cand == args.chat:
                continue
            try:
                cm = wx.messages(chat=cand, limit=12)
            except Exception:
                continue
            ct = [m for m in cm if m.content_type == "text"]
            if ct:
                tgt, probe_chat = ct, cand
                print(f"     （{args.chat} 里没有正文消息，改用 {cand} 演示）")
                break
    if tgt:
        m = tgt[-1]
        t0 = time.time()
        copied = wx.copy_message(m.index, chat=probe_chat)
        print(f"     复制第 {m.index} 条 → {copied[:34]!r}   ({(time.time()-t0)*1000:.0f}ms)")
        print(f"     与直接读到的正文一致: {'✅' if copied.strip() == m.text.strip() else '❌'}")
        note("右键必须带指针微移才会弹菜单（纯 press+release 无论按多久都不弹）；",
             "位置必须落在气泡上 —— 无障碍给的是整行，行中央往往是空白。")
    else:
        print("     当前可见消息里没有正文消息，跳过复制演示")
        print("     （表情/图片消息的菜单里没有「复制」，这是微信的行为，不是缺陷）")
    print("     可用的菜单动作: 复制 / 转发… / 收藏 / 引用 / 提醒 / 翻译 / 多选")
    idx = tgt[-1].index if tgt else (msgs[-1].index if msgs else 0)
    ok, detail = wx.message_action(idx, "删除", chat=probe_chat)
    print(f"     尝试「删除」 → {'❌ 竟然执行了' if ok else '✅ 被拒绝: ' + detail}")
    note("删除/撤回不可逆，服务端默认拒绝。")

    # ---------------------------------------------------------------- 8
    section(8, "未读徽标（小红点）")
    b, ms = timed(wx.badges)
    nav = "  ".join(f"{k}{'🔴' if v else '·'}" for k, v in list(b["nav"].items())[:6])
    print(f"     导航: {nav}")
    print(f"     有未读的会话 {b['unread_sessions']} 个，未读消息共 {b['unread_messages']} 条"
          f"   （耗时 {ms:.0f}ms）")
    note("徽标是**绘制装饰**，不在无障碍树里（有徽标与没徽标节点结构完全相同）。",
         "所以：位置取自无障碍节点，是否变红由像素颜色判定。不是 OCR，不依赖分辨率。",
         "徽标里的**数字**读不出来，但会话级未读数是精确的结构化字段。")

    # ---------------------------------------------------------------- 9
    section(9, "监听新消息")
    t0 = time.time()
    n = sum(1 for _ in wx.watch(seconds=3))
    print(f"     全局监听 3 秒 → 收到 {n} 条   （实际 {time.time()-t0:.1f}s）")
    t0 = time.time()
    n = sum(1 for _ in wx.watch(chat=args.chat, seconds=3))
    print(f"     单会话监听 3 秒 → 收到 {n} 条   （实际 {time.time()-t0:.1f}s）")
    note("全局监听只读会话列表的预览，覆盖所有会话且**不打开任何窗口** ——",
         "不动界面、不清你的未读。代价是只能拿预览（可能被截断）。",
         "单会话监听能拿完整正文，但会保持该会话打开。")

    # --------------------------------------------------------------- 10
    section(10, "逃生舱")
    tree, ms = timed(lambda: wx.tree(only_visible=True, max_depth=40))
    roles = {}
    for node in tree.nodes:
        roles[node.role] = roles.get(node.role, 0) + 1
    top = sorted(roles.items(), key=lambda kv: -kv[1])[:5]
    print(f"     可见节点 {len(tree.nodes)} 个（耗时 {ms:.0f}ms）")
    print("     角色分布: " + ", ".join(f"{r}×{n}" for r, n in top))
    print("     按角色/名字点击: wx.click_node('push button', '微信')")
    note("契约没覆盖的能力用逃生舱。如果某个常用操作只能靠它，",
         "那多半是契约该补一个业务语义接口了。")

    # --------------------------------------------------------------- 11
    section(11, "安全机制")
    print("     危险按钮黑名单 : 退出/注销/清空/删除/撤回/解散 一律拒绝点击")
    print("     弹层防护       : 点击会被投递给最顶层窗口，所以操作前")
    print("                      确认界面处于「只有主窗口」的已知状态")
    print("     模态框失败即报错: 界面被挡住时放弃操作，而不是继续往里输入")
    print("     Escape 兜底    : Escape 在微信主窗口上是「退出登录」开关，")
    print("                      按完立刻检查并点「取消」")
    note("这几条都不是设计出来的，是踩出来的 —— 包括误触退出登录、",
         "点击打到被遮挡窗口、把另一个会话的消息贴上目标会话名返回。")

    wx.close()
    print()
    print("═" * W)
    print("  巡览结束。文档: docs/quickstart.md   接口: proto/wejam/v1/wejam.proto")
    print("═" * W)


if __name__ == "__main__":
    main()
