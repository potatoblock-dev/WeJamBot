"""Heuristics for splitting a display sender out of WeChat preview / bubble text."""

from __future__ import annotations

# WeChat / a11y previews may use ASCII or fullwidth colon, with or without a space.
_COLON_SEPARATORS = (": ", "： ", ":", "：")


def is_plausible_sender_name(name: str) -> bool:
    """True when name looks like a person/group nickname, not a media/system label.

    GUI previews sometimes look like ``[链接] 标题: 摘要``; treating the bracket
    head as a sender poisons impression keys (``群:假昵称``).
    """
    raw = (name or "").strip()
    if not raw or len(raw) > 24:
        return False
    if raw.startswith("["):
        return False
    if raw.startswith("http://") or raw.startswith("https://"):
        return False
    return True


def split_preview_sender(text: str) -> tuple[str, str]:
    """Split ``昵称: 正文`` preview into (sender, body); empty sender when unsure."""
    raw = (text or "").strip()
    if not raw:
        return "", ""
    for sep in _COLON_SEPARATORS:
        if sep not in raw:
            continue
        head, rest = raw.split(sep, 1)
        cand = head.strip()
        body = rest.strip()
        if is_plausible_sender_name(cand) and body:
            return cand, body
    return "", raw
