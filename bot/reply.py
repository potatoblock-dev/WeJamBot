"""Send model output as WeChat text bubbles via wejam.Client."""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

_STICKER = re.compile(r"\[\[sticker:[^\]]+\]\]")
_SPLIT = re.compile(r"(?m)^\s*---\s*$")


def strip_sticker_markers(text: str) -> str:
    """Drop QQ-style [[sticker:id]] markers; WeJam can only send text."""
    cleaned = _STICKER.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def split_bubbles(text: str) -> list[str]:
    """Split a model reply on a line that is only --- into sendable bubbles."""
    body = strip_sticker_markers(text)
    if not body:
        return []
    parts = [chunk.strip() for chunk in _SPLIT.split(body) if chunk.strip()]
    return parts or ([body] if body else [])


class TextSender:
    """Wrap wejam.Client.send_text for one open connection."""

    def __init__(self, client: object) -> None:
        self._client = client

    def send(self, chat: str, text: str) -> bool:
        """Send one bubble; return False when the driver reports failure."""
        result = self._client.send_text(chat, text)
        ok = bool(getattr(result, "ok", False))
        if not ok:
            detail = str(getattr(result, "detail", "") or "")
            _log.warning("send_text failed chat=%s detail=%s", chat, detail)
        return ok

    def send_bubbles(self, chat: str, reply: str) -> bool:
        """Send each --- split bubble; True if at least one send succeeded."""
        bubbles = split_bubbles(reply)
        if not bubbles:
            return False
        delivered = False
        for bubble in bubbles:
            if self.send(chat, bubble):
                delivered = True
        return delivered
