"""WeJamBot agent：容器内的 gRPC 服务。

它持有登录状态机（见 login.py），对外只暴露契约里的能力与状态。
客户端不需要知道容器里发生了什么。
"""
import hashlib
import os
import subprocess
import sys
import time
from concurrent import futures

import grpc

_HERE = os.path.dirname(os.path.abspath(__file__))
# 契约代码是 wejam 包的子包（wejam/v1），镜像里放在 /opt/wejam/wejam/v1。
# 把 /opt/wejam 放进 sys.path 即可 `from wejam.v1 import ...`。
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..")))

from wejam.v1 import wejam_pb2, wejam_pb2_grpc  # noqa: E402

from . import accessibility, badges, chat, geometry, login, message_actions, selflog, wechat_info, windows  # noqa: E402
from . import input as inputmod  # noqa: E402

PORT = int(os.environ.get("WEJAM_PORT", "7700"))
PHASE_TO_PROTO = {
    login.PHASE_UNSPECIFIED: wejam_pb2.LOGIN_PHASE_UNSPECIFIED,
    login.PHASE_STARTING: wejam_pb2.LOGIN_PHASE_STARTING,
    login.PHASE_QR_READY: wejam_pb2.LOGIN_PHASE_QR_READY,
    login.PHASE_ONE_CLICK_READY: wejam_pb2.LOGIN_PHASE_ONE_CLICK_READY,
    login.PHASE_WAIT_PHONE: wejam_pb2.LOGIN_PHASE_WAIT_PHONE,
    login.PHASE_LOGGED_IN: wejam_pb2.LOGIN_PHASE_LOGGED_IN,
    login.PHASE_UNKNOWN: wejam_pb2.LOGIN_PHASE_UNKNOWN,
}


class WeJamServicer(wejam_pb2_grpc.WeJamServicer):
    def __init__(self, machine: login.LoginMachine):
        self.machine = machine

    # ---------- 状态 ----------
    def GetStatus(self, request, context):
        snap = self.machine.snapshot()
        version, source = wechat_info.detect()
        return wejam_pb2.Status(
            ready=snap["phase"] not in (login.PHASE_STARTING,),
            wechat_version=version,
            login_phase=PHASE_TO_PROTO.get(snap["phase"], wejam_pb2.LOGIN_PHASE_UNSPECIFIED),
            uptime_ms=self.machine.uptime_ms(),
        )

    # ---------- 登录 ----------
    def _state_msg(self, snap=None):
        snap = snap or self.machine.snapshot()
        return wejam_pb2.LoginState(
            phase=PHASE_TO_PROTO.get(snap["phase"], wejam_pb2.LOGIN_PHASE_UNSPECIFIED),
            detail=snap["detail"],
            since_unix_ms=snap["since_unix_ms"],
            markers=snap["markers"],
        )

    def GetLoginState(self, request, context):
        return self._state_msg()

    def WatchLogin(self, request, context):
        q = self.machine.subscribe()
        try:
            yield self._state_msg()
            while context.is_active():
                try:
                    snap = q.popleft()
                except IndexError:
                    time.sleep(0.3)
                    continue
                yield self._state_msg(snap)
        finally:
            self.machine.unsubscribe(q)

    def GetLoginQRCode(self, request, context):
        qr = self.machine.qr()
        if not qr["payload"]:
            context.abort(grpc.StatusCode.NOT_FOUND,
                          "当前没有可用的二维码（可能不在二维码状态）")
        return wejam_pb2.LoginQRCode(
            payload=qr["payload"],
            revision=qr["revision"] or "",
            fetched_unix_ms=qr["fetched_unix_ms"],
        )

    def SubmitLogin(self, request, context):
        ok, detail = self.machine.submit_login()
        if not ok:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, detail)
        return self._state_msg()

    # ---------- 会话与消息 ----------
    def ListChats(self, request, context):
        return wejam_pb2.ListChatsResponse(chats=[
            wejam_pb2.Chat(**c) for c in chat.list_chats()
        ])

    def ListMessages(self, request, context):
        try:
            name, msgs = chat.list_messages(request.chat, request.limit)
        except RuntimeError as e:
            # 例如「打开会话失败」——必须如实上报，不能返回别的会话的消息
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        info = chat.chat_header_info(name)
        return wejam_pb2.ListMessagesResponse(
            chat=name,
            chat_type=info["chat_type"],
            member_count=info["member_count"],
            messages=[wejam_pb2.Message(
                id=m["id"], chat=m["chat"], sender=m["sender"], text=m["text"],
                time_text=m["time_text"],
                kind=getattr(wejam_pb2, f"MESSAGE_KIND_{m['kind']}"),
                index=m["index"],
                from_self=selflog.is_own(m["chat"], m["text"]),
                content_type=m.get("content_type", "text"),
            ) for m in msgs],
        )

    def WatchMessages(self, request, context):
        """增量推送。

        两种模式：
          * 指定 chat  -> 单会话精细监听，能拿完整正文；
                          代价是服务端要保持该会话打开（会清掉它的未读）。
          * 不指定     -> **全局监听**：轮询会话列表的预览变化，覆盖所有会话，
                          完全不打开任何会话（不动界面、不清未读）；
                          代价是只能给预览文本（可能被截断），preview_only=true。
        """
        if not request.chat:
            yield from self._watch_global(context)
        else:
            yield from self._watch_single(request.chat, context)

    # ---------- 全局监听：只读会话列表，不碰界面 ----------
    @staticmethod
    def _is_new_message(prev, cur):
        """判断会话预览的变化是否意味着「来了新消息」。

        注意区分「用户读了（未读减少）」与「来了新的」——前者不该报。
        """
        if cur["last_message"] != prev["last_message"]:
            return True
        if cur["last_time"] != prev["last_time"]:
            return True
        return cur["unread_count"] > prev["unread_count"]

    @staticmethod
    def _preview_to_message(c):
        """把会话列表的一行预览变成一条 Message（preview_only）。"""
        text = c["last_message"]
        sender = ""
        # 群聊预览形如 "崔欣宇 7.31: 正文"，尝试拆出发送者
        if ": " in text:
            head, rest = text.split(": ", 1)
            if 0 < len(head) <= 24 and rest.strip():
                sender, text = head.strip(), rest.strip()
        mid = hashlib.sha1(
            f"{c['name']}|{c['last_message']}|{c['last_time']}|{c['unread_count']}".encode()
        ).hexdigest()[:16]
        return wejam_pb2.Message(
            id=mid, chat=c["name"], sender=sender, text=text,
            time_text=c["last_time"], kind=wejam_pb2.MESSAGE_KIND_TEXT,
            preview_only=True,
            from_self=selflog.is_own(c["name"], c["last_message"]),
        )

    def _watch_global(self, context):
        prev = {}
        while context.is_active():
            try:
                chats = chat.list_chats()
            except Exception:
                time.sleep(1.5)
                continue

            cur = {c["name"]: c for c in chats}
            if prev:
                changed = [c for name, c in cur.items()
                           if name in prev and self._is_new_message(prev[name], c)]
                # 按会话逐个推送：带上该会话未读数，客户端据此判断漏没漏
                for c in changed:
                    yield wejam_pb2.MessageEvent(
                        chat=c["name"],
                        at_unix_ms=int(time.time() * 1000),
                        added=[self._preview_to_message(c)],
                        unread=c["unread_count"],
                    )
            # 首轮只建基线；新出现的会话也从下一轮起纳入基线
            prev = cur
            time.sleep(1.0)

    def _watch_single(self, target, context):
        """单会话精细监听。

        关键点：会话切换（或首次变得可见）时必须重建基线，否则一打开会话
        就会把整段历史当成新消息推给订阅者 —— 实测中确实踩到过。
        """
        seen = set()
        last_chat = None
        settle_until = 0.0
        while context.is_active():
            try:
                name, msgs = chat.list_messages(target, 0)
            except Exception:
                time.sleep(1.0)
                continue

            texts = [(m["chat"], m["id"]) for m in msgs if m["kind"] == "TEXT"]
            if name != last_chat:
                # 首次可见 / 换了会话：进入稳定期，只建基线，不推送历史
                last_chat = name
                seen = set(texts)
                settle_until = time.time() + 3.0
                time.sleep(1.5)
                continue

            if time.time() < settle_until:
                # 刚打开会话时列表仍在渲染，晚出现的消息不能算「新」。
                # 持续把当前可见的并入基线，等渲染稳定。
                seen |= set(texts)
                time.sleep(1.5)
                continue

            added = [m for m in msgs
                     if m["kind"] == "TEXT" and (m["chat"], m["id"]) not in seen]
            if added:
                seen.update((m["chat"], m["id"]) for m in added)
                yield wejam_pb2.MessageEvent(
                    chat=target,
                    at_unix_ms=int(time.time() * 1000),
                    added=[wejam_pb2.Message(
                        id=m["id"], chat=m["chat"], sender=m["sender"],
                        text=m["text"], time_text=m["time_text"],
                        kind=getattr(wejam_pb2, f"MESSAGE_KIND_{m['kind']}"),
                        index=m["index"],
                        from_self=selflog.is_own(m["chat"], m["text"])) for m in added],
                )
            time.sleep(1.5)

    def SendText(self, request, context):
        ok, detail = chat.send_text(request.chat, request.text)
        return wejam_pb2.SendResult(ok=ok, detail=detail)

    def InvokeMessage(self, request, context):
        ok, detail, text = message_actions.invoke(request.chat, request.index, request.action)
        return wejam_pb2.InvokeMessageResult(ok=ok, detail=detail, text=text)

    def GetMessageSender(self, request, context):
        ok, name, fields, detail = message_actions.sender_of(request.chat, request.index)
        return wejam_pb2.MessageSender(ok=ok, name=name, fields=fields, detail=detail)

    def ListWindows(self, request, context):
        return wejam_pb2.ListWindowsResponse(chats=windows.list_open())

    def ArrangeWindows(self, request, context):
        windows.arrange()
        return wejam_pb2.ListWindowsResponse(chats=windows.list_open())

    def OpenWindow(self, request, context):
        ok, detail = windows.open(request.chat)
        return wejam_pb2.WindowResult(ok=ok, detail=detail)

    def CloseWindow(self, request, context):
        ok, detail = windows.close(request.chat)
        return wejam_pb2.WindowResult(ok=ok, detail=detail)

    def GetBadges(self, request, context):
        items, unread_chats, unread_msgs = badges.scan_badges()
        return wejam_pb2.GetBadgesResponse(
            badges=[wejam_pb2.NavBadge(label=b["label"], has_badge=b["has_badge"],
                                       red_pixels=b["red_pixels"],
                                       area_pixels=b["area_pixels"]) for b in items],
            unread_chats=unread_chats,
            unread_messages=unread_msgs,
        )

    # ---------- 逃生舱 ----------
    def DumpA11yTree(self, request, context):
        only_visible = request.only_visible if request.HasField("only_visible") else True
        depth = request.max_depth if request.HasField("max_depth") else 45
        nodes = accessibility.walk_all(
            only_visible=only_visible,
            max_depth=depth or 45,
        )
        return wejam_pb2.DumpA11yTreeResponse(nodes=[
            wejam_pb2.A11yNode(depth=n["depth"], role=n["role"], name=n["name"],
                               text=n["text"], states=n["states"])
            for n in nodes
        ])

    def InvokeNode(self, request, context):
        rect = geometry.node_physical_rect(request.role, request.name)
        if rect is None:
            return wejam_pb2.InvokeNodeResponse(ok=False, detail="未找到匹配的可见节点")
        x, y, w, h = rect
        inputmod.click_at(x + w // 2, y + h // 2)
        return wejam_pb2.InvokeNodeResponse(
            ok=True, detail=f"已点击 {request.role}/{request.name} @ ({x + w // 2},{y + h // 2})")


def serve():
    machine = login.LoginMachine(
        poll_interval=float(os.environ.get("WEJAM_POLL", "1.0")),
        auto_login=os.environ.get("WEJAM_AUTO_LOGIN", "1") != "0",
    )
    machine.start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    wejam_pb2_grpc.add_WeJamServicer_to_server(WeJamServicer(machine), server)
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    print(f"[agent] gRPC listening on :{PORT}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        machine.stop()
        server.stop(0)


if __name__ == "__main__":
    serve()
