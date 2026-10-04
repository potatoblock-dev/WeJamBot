"""全局 UI 操作锁。

微信界面、X server、pyatspi 的 D-Bus 连接这三样**本质上都是单线程资源**，
而 agent 里有多条并发路径会碰它们：

    * gRPC 线程池（默认 8 个 worker）：ListChats / ListMessages / SendText / ...
    * 登录状态机线程：每秒轮询无障碍树、解码二维码、必要时点击

不加锁的后果是实打实的：
    * 两个 SendText 并发 -> 键码交错，消息变成乱码
    * SendText 撞上版本探测（会开关设置面板）-> 点击落到错误位置
    * 多个线程同时走 pyatspi -> D-Bus 连接被并发使用

因此所有「读界面 / 动界面」的操作都必须经过这把锁。

用 RLock 而不是 Lock：底层原语（如 input.click_at）自身也要互斥，
而操作级函数会调用它们，需要可重入。
"""
import threading
from functools import wraps

UI_LOCK = threading.RLock()


def synchronized(fn):
    """把函数整体放进 UI 锁里。

    粒度说明：**操作级**函数（send_text / list_messages / 登录轮询）必须整体
    持锁 —— 它们是「打开会话→定位→点击→输入→回车」这样一串动作，
    中间被别的线程插入就会把消息打乱。底层原语也加，作为兜底。
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with UI_LOCK:
            return fn(*args, **kwargs)
    return wrapper


__all__ = ["UI_LOCK", "synchronized"]
