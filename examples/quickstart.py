#!/usr/bin/env python3
"""WeJamBot 快速上手：走一遍全部接口。

运行前先起容器（bash docker/run.sh），并确认已登录（python -m wejam.cli login）。
    python examples/quickstart.py
"""
from wejam import Client, PHASE_NAME

TARGET = "文件传输助手"   # 发给自己，不会打扰任何人


def main():
    with Client("127.0.0.1:7700") as c:
        # ---------- 1. 服务与登录状态 ----------
        print("=" * 60)
        print("1. 服务状态")
        st = c.status()
        print(f"   微信版本   : {st.wechat_version}")
        print(f"   登录阶段   : {PHASE_NAME.get(st.login_phase, st.login_phase)}")
        print(f"   服务已运行 : {st.uptime_ms / 1000:.0f}s")

        # ---------- 2. 会话列表 ----------
        print("=" * 60)
        print("2. 会话列表")
        for ch in c.list_chats().chats[:5]:
            flags = []
            if ch.pinned:
                flags.append("置顶")
            if ch.muted:
                flags.append("免打扰")
            if ch.unread_count:
                flags.append(f"未读{ch.unread_count}")
            tag = f"  [{' '.join(flags)}]" if flags else ""
            print(f"   {ch.name[:20]:22} {ch.last_message[:28]:30} {ch.last_time:8}{tag}")

        # ---------- 3. 读取消息 ----------
        print("=" * 60)
        print(f"3. 读取「{TARGET}」最近 5 条")
        resp = c.list_messages(chat=TARGET, limit=5)
        print(f"   （服务端自动打开了该会话，当前会话 = {resp.chat}）")
        for m in resp.messages:
            kind = {1: "正文", 2: "时间", 3: "系统"}.get(m.kind, "?")
            sender = f"{m.sender}: " if m.sender else ""
            print(f"   [{m.index:2}] {kind} {sender}{m.text[:44]}")

        # ---------- 4. 发送消息 ----------
        print("=" * 60)
        print("4. 发送一条消息（中文 + emoji，不经过输入法）")
        r = c.send_text(TARGET, "来自 quickstart 的问候 👋 中文与 emoji 都能发")
        print(f"   {'✅' if r.ok else '❌'} {r.detail}")

        # ---------- 5. 未读徽标 ----------
        print("=" * 60)
        print("5. 未读徽标")
        b = c.get_badges()
        nav = "  ".join(f"{x.label}{'🔴' if x.has_badge else '·'}" for x in b.badges[:6])
        print(f"   导航: {nav}")
        print(f"   有未读的会话: {b.unread_chats} 个，未读消息共 {b.unread_messages} 条")

        # ---------- 6. 逃生舱 ----------
        print("=" * 60)
        print("6. 逃生舱：直接查无障碍节点（这里只统计）")
        tree = c.dump_tree(only_visible=True, max_depth=40)
        roles = {}
        for n in tree.nodes:
            roles[n.role] = roles.get(n.role, 0) + 1
        top = sorted(roles.items(), key=lambda kv: -kv[1])[:5]
        print("   可见节点角色分布: " + ", ".join(f"{r}×{n}" for r, n in top))

    print("=" * 60)
    print("完成。全局监听用: python -m wejam.cli watch")
    print("增量订阅单个会话用: python -m wejam.cli watch --chat 文件传输助手")


if __name__ == "__main__":
    main()
