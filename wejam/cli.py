"""WeJam CLI：宿主侧命令行入口。

呈现层完全在客户端：服务只回二维码「内容」，渲染由这里决定。
"""
import argparse
import sys
import time

from .client import (
    PHASE_LOGGED_IN,
    PHASE_NAME,
    PHASE_ONE_CLICK_READY,
    PHASE_QR_READY,
    Client,
)


def _render_qr(payload, path):
    try:
        import qrcode
    except ImportError:
        return None
    img = qrcode.make(payload)
    img = img.resize((max(img.size) * 8,) * 2)
    img.save(path)
    return path


def cmd_status(args):
    with Client(args.target) as c:
        s = c.status()
        print(f"ready          : {s.ready}")
        print(f"wechat_version : {s.wechat_version or '(未知)'}")
        print(f"login_phase    : {PHASE_NAME.get(s.login_phase, s.login_phase)}")
        print(f"uptime_ms      : {s.uptime_ms}")
    return 0


def cmd_login(args):
    """订阅登录状态；二维码内容变化时由客户端重新渲染。

    服务重启会让流断开，因此这里带重连 —— 客户端不该因为一次服务抖动就退出。
    """
    import grpc

    last_rev = None
    t0 = time.time()
    backoff = 1.0

    while True:
        if args.timeout and time.time() - t0 > args.timeout:
            print("超时")
            return 3
        try:
            with Client(args.target) as c:
                backoff = 1.0
                for st in c.watch_login(timeout=args.timeout or None):
                    phase = PHASE_NAME.get(st.phase, st.phase)
                    print(f"[{int(time.time()-t0):3d}s] {phase:16} {st.detail}")

                    if st.phase == PHASE_QR_READY:
                        try:
                            qr = c.qr_code()
                        except Exception as e:
                            print(f"          取二维码失败: {e}")
                            continue
                        if qr.revision and qr.revision != last_rev:
                            last_rev = qr.revision
                            out = _render_qr(qr.payload, args.qr_out)
                            print(f"          🔗 {qr.payload}")
                            if out:
                                print(f"          🖼  已渲染: {out}")

                    if st.phase == PHASE_ONE_CLICK_READY and args.auto_submit:
                        try:
                            c.submit_login()
                            print("          🖱  已提交登录")
                        except Exception as e:
                            print(f"          提交失败: {e}")

                    if st.phase == PHASE_LOGGED_IN:
                        print("✅ 已登录")
                        return 0
        except grpc.RpcError as e:
            code = e.code() if hasattr(e, "code") else "?"
            print(f"[{int(time.time()-t0):3d}s] ⚠️  连接中断（{code}），{backoff:.0f}s 后重连")
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
            last_rev = None  # 重连后重新取一次码，确保客户端手上是最新的


def cmd_qr(args):
    """按需拉取**当前**二维码并渲染。

    这是「拉」模式：客户端不依赖之前渲染过的那张图，任何时刻都能取到
    服务端此刻手上的码。二维码会周期性刷新，扫之前拉一次最稳妥。
    """
    with Client(args.target) as c:
        st = c.login_state()
        phase = PHASE_NAME.get(st.phase, st.phase)
        if st.phase != PHASE_QR_READY:
            print(f"当前不是二维码状态：{phase}  ({st.detail})")
            return 1
        qr = c.qr_code()
        age_s = (time.time() * 1000 - qr.fetched_unix_ms) / 1000 if qr.fetched_unix_ms else -1
        out = None if args.no_render else _render_qr(qr.payload, args.qr_out)
        print(f"phase      : {phase}")
        print(f"revision   : {qr.revision}")
        print(f"内容年龄   : {age_s:.1f}s（服务端最后一次观察到该内容的时间）")
        print(f"payload    : {qr.payload}")
        if out:
            print(f"已渲染     : {out}")
    return 0


def cmd_chats(args):
    with Client(args.target) as c:
        resp = c.list_chats()
        for ch in resp.chats:
            flags = []
            if ch.pinned:
                flags.append("置顶")
            if ch.muted:
                flags.append("免打扰")
            if ch.unread_count:
                flags.append(f"未读{ch.unread_count}")
            tail = f"  [{' '.join(flags)}]" if flags else ""
            preview = ch.last_message[:40]
            print(f"{ch.name:24} {preview:42} {ch.last_time:8}{tail}")
    return 0


def cmd_messages(args):
    with Client(args.target) as c:
        resp = c.list_messages(chat=args.chat or "", limit=args.limit)
        print(f"# 会话: {resp.chat}")
        for m in resp.messages:
            tag = {1: "TEXT", 2: "TIME", 3: "SYS "}.get(m.kind, "?   ")
            sender = f"{m.sender}: " if m.sender else ""
            print(f"[{m.index:3d}] {tag} {sender}{m.text}")
    return 0


def cmd_send(args):
    with Client(args.target) as c:
        r = c.send_text(args.chat, args.text)
        print(("✅ " if r.ok else "❌ ") + r.detail)
        return 0 if r.ok else 1


def cmd_watch(args):
    """服务端 diff 后推增量，客户端只打印。"""
    import grpc

    t0 = time.time()
    while True:
        try:
            with Client(args.target) as c:
                for ev in c.watch_messages(args.chat or "", timeout=args.timeout or None):
                    stamp = time.strftime("%H:%M:%S", time.localtime(ev.at_unix_ms / 1000))
                    for m in ev.added:
                        sender = f"{m.sender}: " if m.sender else ""
                        tag = "预览" if m.preview_only else "完整"
                        extra = f"  (未读 {ev.unread})" if ev.unread else ""
                        print(f"[{stamp}] [{tag}] {ev.chat or m.chat}  {sender}{m.text}{extra}")
        except grpc.RpcError as e:
            print(f"⚠️  连接中断（{e.code() if hasattr(e,'code') else '?'}），重连中")
            time.sleep(2)
            if args.timeout and time.time() - t0 > args.timeout:
                return 3


def cmd_badges(args):
    """未读徽标：导航图标上的「小红点」是绘制装饰，走无障碍定位 + 像素判定。"""
    with Client(args.target) as c:
        r = c.get_badges()
        print("导航徽标（位置来自无障碍树，颜色判定非 OCR）：")
        for b in r.badges:
            mark = "🔴 有" if b.has_badge else "⚪ 无"
            print(f"  {mark}  {b.label:12} 红像素 {b.red_pixels}/{b.area_pixels}")
        print()
        print(f"有未读的会话数   : {r.unread_chats}   ← 结构化推导，非 OCR")
        print(f"未读消息总数     : {r.unread_messages}")
    return 0


def cmd_copy(args):
    """复制某条消息并打印正文（走右键菜单 + 剪贴板）。"""
    with Client(args.target) as c:
        r = c.invoke_message(args.chat or "", args.index, "复制")
        if r.ok:
            print(r.text)
            return 0
        print("失败:", r.detail)
        return 1


def cmd_action(args):
    """对某条消息执行右键菜单动作。"""
    with Client(args.target) as c:
        r = c.invoke_message(args.chat or "", args.index, args.action)
        print(("✅ " if r.ok else "❌ ") + r.detail)
        return 0 if r.ok else 1


def cmd_sender(args):
    """取某条消息的发送者（群昵称）。"""
    with Client(args.target) as c:
        r = c.message_sender(args.chat or "", args.index)
        if r.ok:
            print(r.name)
            if r.fields:
                print("  资料卡: " + " | ".join(r.fields))
            return 0
        print("失败:", r.detail)
        return 1


def cmd_tree(args):
    with Client(args.target) as c:
        resp = c.dump_tree(only_visible=not args.all, max_depth=args.depth)
        for n in resp.nodes:
            txt = n.text or n.name
            if not txt.strip():
                continue
            print(f"{'  ' * n.depth}[{n.role}] {txt[:70]!r}")
    return 0


def cmd_invoke(args):
    with Client(args.target) as c:
        r = c.invoke_node(args.role, args.name)
        print(("✅ " if r.ok else "❌ ") + r.detail)
        return 0 if r.ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wejam", description="WeJamBot 客户端")
    ap.add_argument("--target", default=None, help="gRPC 地址，默认 127.0.0.1:7700")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="服务与微信状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("login", help="订阅并自动推进登录（客户端负责渲染二维码）")
    p.add_argument("--qr-out", default="wejam-qr.png")
    p.add_argument("--timeout", type=int, default=0)
    p.add_argument("--auto-submit", action="store_true", default=True)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("qr", help="按需拉取当前二维码并渲染（拉模式，最稳）")
    p.add_argument("--qr-out", default="wejam-qr.png")
    p.add_argument("--no-render", action="store_true", help="只打印内容，不渲染")
    p.set_defaults(func=cmd_qr)

    p = sub.add_parser("chats", help="列出会话")
    p.set_defaults(func=cmd_chats)

    p = sub.add_parser("messages", help="读取当前/指定会话的消息")
    p.add_argument("--chat", default="")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_messages)

    p = sub.add_parser("send", help="发送文本到指定会话")
    p.add_argument("chat")
    p.add_argument("text")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("watch", help="增量订阅；不指定 --chat 即全局监听（不动界面）")
    p.add_argument("--chat", default="")
    p.add_argument("--timeout", type=int, default=0)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("copy", help="复制某条消息并打印正文")
    p.add_argument("index", type=int)
    p.add_argument("--chat", default="")
    p.set_defaults(func=cmd_copy)

    p = sub.add_parser("action", help="对某条消息执行右键菜单动作")
    p.add_argument("index", type=int)
    p.add_argument("action")
    p.add_argument("--chat", default="")
    p.set_defaults(func=cmd_action)

    p = sub.add_parser("sender", help="取某条消息的发送者（群昵称）")
    p.add_argument("index", type=int)
    p.add_argument("--chat", default="")
    p.set_defaults(func=cmd_sender)

    p = sub.add_parser("badges", help="未读徽标（小红点）")
    p.set_defaults(func=cmd_badges)

    p = sub.add_parser("tree", help="[逃生舱] dump 无障碍节点树")
    p.add_argument("--all", action="store_true", help="含不可见节点")
    p.add_argument("--depth", type=int, default=45)
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("invoke", help="[逃生舱] 按角色/名字点击节点")
    p.add_argument("role")
    p.add_argument("name")
    p.set_defaults(func=cmd_invoke)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
