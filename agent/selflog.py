"""自发消息溯源：判断某条消息是不是本服务自己发出去的。

为什么需要它（而不是让客户端打前缀）：
    机器人监听 → 自己回复 → 回复又出现在会话预览里 → 再触发一次监听 → 死循环。
    常见的土办法是给回复加个「[bot]」前缀再自己过滤，但这污染了对外消息内容，
    而且任何别的客户端接进来都得重复实现一遍。

    正确做法是服务端做溯源：SendText 必然经过 agent，agent 记下自己发过什么，
    推送时直接标 `from_self=true`，客户端不需要任何约定。

匹配的难点：会话列表给的是**预览**，可能被截断，群聊里还可能带「昵称: 」前缀。
所以按「去掉前缀 + 前缀匹配」判定，并对过短的文本只做精确匹配以免误判。
"""
import threading
import time
from collections import deque

_SENT = deque(maxlen=128)
_lock = threading.Lock()

# 只在这个时间窗口内做溯源：太久的旧记录不该影响判断
WINDOW_SECONDS = 120.0
# 短于这个长度只做精确匹配，避免「好」匹配上「好的我知道了」
MIN_PREFIX_LEN = 4


def remember(chat, text):
    """记录一条自己发出的消息。"""
    t = (text or "").strip()
    if not t:
        return
    with _lock:
        _SENT.append((chat or "", t, time.time()))


def _strip_sender(text):
    """群聊预览形如「昵称 7.31: 正文」，去掉前缀只留正文。"""
    if ": " in text:
        head, rest = text.split(": ", 1)
        if 0 < len(head) <= 24 and rest.strip():
            return rest.strip()
    return text


def is_own(chat, text):
    """这条消息是不是自己刚发的。"""
    body = _strip_sender((text or "").strip())
    if not body:
        return False
    now = time.time()
    with _lock:
        for c, sent, ts in reversed(_SENT):
            if now - ts > WINDOW_SECONDS:
                break
            if c != (chat or ""):
                continue
            if sent == body:
                return True
            # 预览被截断时，正文是原文的前缀
            if len(body) >= MIN_PREFIX_LEN and sent.startswith(body):
                return True
            if len(sent) >= MIN_PREFIX_LEN and body.startswith(sent):
                return True
    return False


def clear():
    with _lock:
        _SENT.clear()
