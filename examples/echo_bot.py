#!/usr/bin/env python3
"""全局监听 + 自动回复的最小机器人。

演示三件事：
  1. 用**全局监听**（不打开任何会话）发现所有会话里的新消息；
  2. 靠服务端的 `from_self` 标注避免自问自答死循环 —— **不需要任何前缀约定**；
  3. 断流自动重连，并把未读数打出来（轮询可能漏掉同一周期内的多条）。

    python examples/echo_bot.py

⚠️ 关于 ALLOW：**加进来的会话会被自动回复给真人**。
   默认只放「文件传输助手」（发给自己），先在这个安全目标上跑通再加真实会话。
"""
import time

from wejam import Client

# 只在这些会话里自动回复。⚠️ 加真实会话 = 真的替你在群里说话，谨慎。
ALLOW = {"文件传输助手", "1145"}

RUN_SECONDS = 0        # 0 = 一直跑；正数 = 跑这么多秒后退出


def main():
    print(f"监听中… 只在 {ALLOW} 内自动回复（Ctrl-C 退出）")
    deadline = time.time() + RUN_SECONDS if RUN_SECONDS else float("inf")

    while time.time() < deadline:
        try:
            with Client() as c:
                for ev in c.watch_messages():          # 不指定 chat = 全局模式
                    if time.time() >= deadline:
                        return
                    for m in ev.added:
                        who = f"{m.sender}: " if m.sender else ""
                        mine = "  [自己发的]" if m.from_self else ""
                        unread = f"  未读{ev.unread}" if ev.unread else ""
                        print(f"[{ev.chat}] {who}{m.text}{mine}{unread}")

                        # 关键：服务端已做溯源。自己发的消息直接跳过，
                        # 因此「监听 → 回复 → 回复又被监听到」不会成环，
                        # 也不需要给对外消息加 [bot] 之类的前缀。
                        if m.from_self:
                            continue
                        if ev.chat in ALLOW:
                            r = c.send_text(ev.chat, f"收到「{m.text}」")
                            print(f"          → {'已回复' if r.ok else '回复失败: ' + r.detail}")
        except Exception as e:                          # 服务重启/网络抖动都要能扛
            print(f"连接中断（{type(e).__name__}），2 秒后重连")
            time.sleep(2)


if __name__ == "__main__":
    main()
