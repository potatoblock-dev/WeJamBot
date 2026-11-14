"""Send model output as WeChat text bubbles through WeChatEyes."""

from __future__ import annotations

import logging
import re

from bot.eyes import HandsRejected, WeChatEyes

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
    """Split a reply into bubbles and send each one through WeChatEyes."""

    def __init__(self, hands: WeChatEyes) -> None:
        """Keep the eyes handle used for every bubble in this connection."""
        self._hands = hands

    def send(self, chat: str, text: str) -> bool:
        """Send one bubble. False when the eyes reject it; transport errors propagate."""
        try:
            self._hands.send_text(chat, text)
        except HandsRejected as exc:
            _log.warning("send_text failed chat=%s detail=%s", chat, exc)
            return False
        return True

    def send_bubbles(self, chat: str, reply: str) -> bool:
        """Send each --- split bubble; True only when every bubble succeeds."""
        bubbles = split_bubbles(reply)
        if not bubbles:
            return False
        sent = 0
        for bubble in bubbles:
            if not self.send(chat, bubble):
                _log.warning(
                    "send_bubbles partial chat=%s sent=%s/%s",
                    chat,
                    sent,
                    len(bubbles),
                )
                return False
            sent += 1
        return True
