"""Host-side WeChat eyes and hands: read and send only through wejam.Client."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from wejam import Client


class WeJamHands:
    """Serialize watch/list/send so the chat brain never imports protobuf."""

    def __init__(
        self,
        target: str | None = None,
        timeout: int = 30,
        *,
        client: object | None = None,
    ) -> None:
        """Use an injected client in tests; otherwise open wejam.Client."""
        self._client = client if client is not None else Client(target, timeout=timeout)
        self._lock = threading.RLock()

    def close(self) -> None:
        """Close the gRPC channel."""
        self._client.close()

    def __enter__(self) -> WeJamHands:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def watch_messages(self, chat: str = "") -> Iterator[Any]:
        """Yield driver MessageEvent values. Empty chat means global preview watch."""
        return self._client.watch_messages(chat=chat)

    def send_text(self, chat: str, text: str) -> Any:
        """Send one WeChat text bubble; return the driver's SendResult."""
        with self._lock:
            return self._client.send_text(chat, text)

    def list_messages(self, chat: str = "", limit: int = 0) -> Any:
        """Open-chat full history when a caller opts in (clears unread for that chat)."""
        with self._lock:
            return self._client.list_messages(chat=chat, limit=limit)
