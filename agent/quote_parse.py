"""Parse WeChat quote/reply bubbles from flat accessibility text."""

from __future__ import annotations

import re
from dataclasses import dataclass

# wxauto / WeChat 4.x UIA style: body then "引用 昵称 的消息: quote"
_QUOTE_AFTER_BODY = re.compile(
    r"^(?P<body>.*?)\s*\n引用\s+(?P<nick>.+?)\s+的消息\s*[:：]\s*(?P<quote>.*)$",
    re.DOTALL,
)
# Quote block first, then the new reply body (some clients)
_QUOTE_BEFORE_BODY = re.compile(
    r"^引用\s+(?P<nick>.+?)\s+的消息\s*[:：]\s*(?P<quote>.*?)\n(?P<body>.+)$",
    re.DOTALL,
)
# Grey citation bar: "昵称: 正文 |" then new body (matches WeChat reply UI chrome)
_QUOTE_BAR = re.compile(
    r"^(?P<nick>[^\n:：]{1,32})\s*[:：]\s*(?P<quote>.+?)\s*[|｜]\s*\n(?P<body>.+)$",
    re.DOTALL,
)
# Placeholder marker only
_QUOTE_MARKER = re.compile(r"^\[引用\]")


@dataclass(frozen=True)
class QuoteParts:
    """Split result: current reply body plus optional cited speaker/text."""

    body: str
    quote_sender: str = ""
    quote_text: str = ""


def parse_quote_message(raw: str) -> QuoteParts | None:
    """Return QuoteParts when ``raw`` looks like a quote bubble; else None.

    Handles wxauto-shaped multi-line text and a grey-bar ``昵称: 正文 |`` form.
    ``[引用]`` alone marks quote type with empty cite fields.
    """
    text = (raw or "").replace("\u00a0", " ").strip()
    if not text:
        return None
    for pattern in (_QUOTE_AFTER_BODY, _QUOTE_BEFORE_BODY, _QUOTE_BAR):
        match = pattern.match(text)
        if not match:
            continue
        body = (match.group("body") or "").strip()
        nick = (match.group("nick") or "").strip()
        quote = (match.group("quote") or "").strip()
        if not quote and not body:
            continue
        return QuoteParts(body=body or text, quote_sender=nick, quote_text=quote)
    if _QUOTE_MARKER.match(text):
        rest = text[len("[引用]") :].lstrip(" \n")
        return QuoteParts(body=rest or text, quote_sender="", quote_text="")
    return None
