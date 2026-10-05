"""WeJam 宿主侧客户端：只消费契约，不接触容器内部实现。"""
import os

import grpc

from .v1 import wejam_pb2, wejam_pb2_grpc

DEFAULT_TARGET = os.environ.get("WEJAM_TARGET", "127.0.0.1:7700")

PHASE_NAME = {
    wejam_pb2.LOGIN_PHASE_UNSPECIFIED: "UNSPECIFIED",
    wejam_pb2.LOGIN_PHASE_STARTING: "STARTING",
    wejam_pb2.LOGIN_PHASE_QR_READY: "QR_READY",
    wejam_pb2.LOGIN_PHASE_ONE_CLICK_READY: "ONE_CLICK_READY",
    wejam_pb2.LOGIN_PHASE_WAIT_PHONE: "WAIT_PHONE",
    wejam_pb2.LOGIN_PHASE_LOGGED_IN: "LOGGED_IN",
    wejam_pb2.LOGIN_PHASE_UNKNOWN: "UNKNOWN",
}

# 导出给 CLI 用，避免魔法数字（枚举值不等于书写顺序）
PHASE_STARTING = wejam_pb2.LOGIN_PHASE_STARTING
PHASE_QR_READY = wejam_pb2.LOGIN_PHASE_QR_READY
PHASE_ONE_CLICK_READY = wejam_pb2.LOGIN_PHASE_ONE_CLICK_READY
PHASE_WAIT_PHONE = wejam_pb2.LOGIN_PHASE_WAIT_PHONE
PHASE_LOGGED_IN = wejam_pb2.LOGIN_PHASE_LOGGED_IN


class Client:
    def __init__(self, target=None, timeout=30):
        self.target = target or DEFAULT_TARGET
        self.timeout = timeout
        self._channel = grpc.insecure_channel(self.target)
        self._stub = wejam_pb2_grpc.WeJamStub(self._channel)

    def close(self):
        self._channel.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- 状态 ----------
    def status(self):
        return self._stub.GetStatus(wejam_pb2.GetStatusRequest(), timeout=self.timeout)

    # ---------- 登录 ----------
    def login_state(self):
        return self._stub.GetLoginState(wejam_pb2.GetLoginStateRequest(), timeout=self.timeout)

    def watch_login(self, timeout=None):
        """订阅登录状态变化，逐条 yield。"""
        return self._stub.WatchLogin(wejam_pb2.WatchLoginRequest(), timeout=timeout)

    def qr_code(self):
        return self._stub.GetLoginQRCode(wejam_pb2.GetLoginQRCodeRequest(), timeout=self.timeout)

    def submit_login(self):
        return self._stub.SubmitLogin(wejam_pb2.SubmitLoginRequest(), timeout=self.timeout)

    # ---------- 会话与消息 ----------
    def list_chats(self):
        return self._stub.ListChats(wejam_pb2.ListChatsRequest(), timeout=self.timeout)

    def list_messages(self, chat="", limit=0):
        req = wejam_pb2.ListMessagesRequest(chat=chat, limit=limit)
        return self._stub.ListMessages(req, timeout=self.timeout)

    def watch_messages(self, chat="", timeout=None):
        return self._stub.WatchMessages(wejam_pb2.WatchMessagesRequest(chat=chat),
                                        timeout=timeout)

    def send_text(self, chat, text):
        req = wejam_pb2.SendTextRequest(chat=chat, text=text)
        return self._stub.SendText(req, timeout=self.timeout)

    def invoke_message(self, chat, index, action):
        req = wejam_pb2.InvokeMessageRequest(chat=chat, index=index, action=action)
        return self._stub.InvokeMessage(req, timeout=self.timeout)

    def message_sender(self, chat, index):
        req = wejam_pb2.GetMessageSenderRequest(chat=chat, index=index)
        return self._stub.GetMessageSender(req, timeout=self.timeout)

    def get_badges(self):
        return self._stub.GetBadges(wejam_pb2.GetBadgesRequest(), timeout=self.timeout)

    # ---------- 逃生舱 ----------
    def dump_tree(self, only_visible=True, max_depth=45):
        req = wejam_pb2.DumpA11yTreeRequest(only_visible=only_visible, max_depth=max_depth)
        return self._stub.DumpA11yTree(req, timeout=self.timeout)

    def invoke_node(self, role, name):
        req = wejam_pb2.InvokeNodeRequest(role=role, name=name)
        return self._stub.InvokeNode(req, timeout=self.timeout)
