"""WeJam —— 微信 Linux 自动化的客户端库。

两种用法：
    门面（省字）:  from wejam import WeChat
    客户端（精细）: from wejam import Client

包名与 proto 包名（wejam.v1）一致，生成的 gRPC 绑定就在 wejam/v1/ 里，
所以 `from wejam.v1 import wejam_pb2` 自然可用，无需 sys.path 注入。

刻意用惰性导入：服务端（agent）只需要 wejam.v1 里的消息定义，
不应被迫拖上客户端才需要的依赖（qrcode 等）。
"""

_LAZY = {"Client", "PHASE_NAME", "DEFAULT_TARGET", "WeChat", "Msg", "Session"}

__all__ = ["WeChat", "Msg", "Session", "Client", "PHASE_NAME", "DEFAULT_TARGET"]


def __getattr__(name):
    if name in ("Client", "PHASE_NAME", "DEFAULT_TARGET"):
        from . import client
        return getattr(client, name)
    if name in ("WeChat", "Msg", "Session"):
        from . import facade
        return getattr(facade, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
