"""Raise the effective weight of the most recent transcript turns in reply history."""

from __future__ import annotations

_FOCUS_MARKER = (
    "【近期对话优先】下面这些是最近的聊天，回复时优先依据；"
    "更早的内容只作背景，不要盖过近期语境。"
)


def apply_recent_focus(
    history: list[dict[str, str]],
    recent_n: int,
) -> list[dict[str, str]]:
    """Insert a focus marker before the last ``recent_n`` history rows.

    Does not change message text. ``recent_n <= 0`` leaves history unchanged.
    When the whole history is within ``recent_n``, the marker goes at the front.
    """
    focus = max(0, int(recent_n))
    if focus <= 0 or not history:
        return list(history)
    marker = {"role": "system", "content": _FOCUS_MARKER}
    if len(history) <= focus:
        return [marker, *history]
    older = history[:-focus]
    recent = history[-focus:]
    return [*older, marker, *recent]
