"""极简门面：把 gRPC 客户端包成三行就能用的样子。

    from wejam import WeChat

    wx = WeChat()
    wx.send("你好", chat="文件传输助手")
    for msg in wx.messages():
        print(msg.text)

命名约定：
    类名 PascalCase（WeChat / Msg / Session），方法与字段一律 snake_case。
    门面只做「省字」，不做新能力 —— 所有方法都是 Client 的薄包装；
    需要精细控制（逃生舱、订阅细节）时直接用 Client。
返回普通 dataclass 而不是 protobuf 对象，肉眼可打印、无依赖。
"""
import time
from dataclasses import dataclass

import grpc

from .client import Client, PHASE_NAME


@dataclass
class Msg:
    """一条消息的易读视图。"""
    chat: str = ""
    sender: str = ""
    text: str = ""
    time: str = ""
    from_self: bool = False     # 服务端溯源：这条是自己发的
    preview: bool = False       # 来自会话列表预览（可能被截断）
    kind: int = 1               # 1 正文 / 2 时间 / 3 系统
    content_type: str = "text"  # text/image/file/link/emotion/voice/location...
    index: int = 0              # 在会话中的序号，供 copy_message / message_action 使用

    @property
    def is_text(self) -> bool:
        return self.kind == 1

    def __str__(self) -> str:
        who = f"{self.sender}: " if self.sender else ""
        return f"{who}{self.text}"


@dataclass
class Session:
    """一个会话的易读视图。"""
    name: str = ""
    last_message: str = ""
    time: str = ""
    unread: int = 0
    pinned: bool = False
    muted: bool = False

    def __str__(self) -> str:
        marks = []
        if self.pinned:
            marks.append("置顶")
        if self.muted:
            marks.append("免打扰")
        if self.unread:
            marks.append(f"未读{self.unread}")
        tail = f"  [{' '.join(marks)}]" if marks else ""
        return f"{self.name}  {self.last_message}  {self.time}{tail}"


def _to_msg(m) -> Msg:
    return Msg(chat=m.chat, sender=m.sender, text=m.text, time=m.time_text,
               from_self=m.from_self, preview=m.preview_only, kind=m.kind,
               index=m.index, content_type=m.content_type or "text")


class WeChat:
    """微信实例门面。默认连本机 127.0.0.1:7700。"""

    def __init__(self, target=None, timeout=60):
        self._c = Client(target, timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._c.close()

    # ---------- 状态 ----------
    def status(self) -> dict:
        s = self._c.status()
        return {
            "version": s.wechat_version,
            "login": PHASE_NAME.get(s.login_phase, s.login_phase),
            "uptime_ms": s.uptime_ms,
        }

    def login_state(self) -> str:
        st = self._c.login_state()
        return PHASE_NAME.get(st.phase, st.phase)

    def qr_code(self) -> str:
        """二维码**内容**（字符串），渲染交给调用方。"""
        return self._c.qr_code().payload

    def submit_login(self) -> str:
        self._c.submit_login()
        return self.login_state()

    # ---------- 会话与消息 ----------
    def chats(self) -> list:
        return [Session(name=c.name, last_message=c.last_message, time=c.last_time,
                        unread=c.unread_count, pinned=c.pinned, muted=c.muted)
                for c in self._c.list_chats().chats]

    def messages(self, chat=None, limit=0) -> list:
        """取消息。chat 为空则取当前会话；给名字会先打开该会话。"""
        resp = self._c.list_messages(chat=chat or "", limit=limit)
        self.last_chat_info = {"chat": resp.chat, "chat_type": resp.chat_type,
                               "member_count": resp.member_count}
        return [_to_msg(m) for m in resp.messages]

    def chat_info(self, chat=None) -> dict:
        """会话信息：名称 / 类型 / 群成员数（群聊头部形如「名称(14)」）。"""
        self.messages(chat=chat, limit=1)
        return dict(getattr(self, "last_chat_info", {}))

    def send(self, text, chat=None) -> bool:
        """发送文本，返回是否成功（服务端会回读输入框校验）。"""
        return bool(self._c.send_text(chat or "", text).ok)

    def badges(self) -> dict:
        b = self._c.get_badges()
        return {
            "nav": {x.label: x.has_badge for x in b.badges},
            "unread_sessions": b.unread_chats,
            "unread_messages": b.unread_messages,
        }

    # ---------- 监听 ----------
    def watch(self, chat=None, seconds=30):
        """监听若干秒，产出 (会话名, Msg)。

        chat 为空 = 全局监听：只读会话列表，**不打开任何会话来动界面**，
        代价是只能拿到预览文本（msg.preview 为真）。
        """
        # 必须给流本身一个超时：否则「没有消息进来」时会永久阻塞，
        # 因为截止时间只在收到事件后才被检查 —— 实测卡死过。
        deadline = time.time() + seconds
        stream = self._c.watch_messages(chat=chat or "", timeout=seconds)
        try:
            for ev in stream:
                if time.time() >= deadline:
                    return
                for m in ev.added:
                    yield ev.chat or m.chat, _to_msg(m)
        except grpc.RpcError as e:
            # 到时结束是正常路径，不是错误
            if e.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
                raise

    def copy_message(self, index, chat=None) -> str:
        """复制第 index 条消息，返回正文（服务端从剪贴板取回）。"""
        r = self._c.invoke_message(chat or "", index, "复制")
        return r.text if r.ok else ""

    def message_action(self, index, action, chat=None) -> tuple:
        """对第 index 条消息执行右键菜单动作。"""
        r = self._c.invoke_message(chat or "", index, action)
        return bool(r.ok), r.detail

    # ---------- 逃生舱 ----------
    def tree(self, only_visible=True, max_depth=45):
        return self._c.dump_tree(only_visible=only_visible, max_depth=max_depth)

    def click_node(self, role, name) -> tuple:
        r = self._c.invoke_node(role, name)
        return bool(r.ok), r.detail
