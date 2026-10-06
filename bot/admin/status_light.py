"""Title-bar status emoji for the native admin window."""

from __future__ import annotations

from collections.abc import Sequence

from bot.admin.log_view import is_error_line, is_warning_line

TITLE_BASE = "WeJam 闲聊控制台"
LIGHT_OFF = "🔴"
LIGHT_ON = "🟢"
LIGHT_BUSY = "🔵"
LIGHT_WARN = "🟡"

_BUSY_MARKERS = (
    "正在回复",
    "正在发送",
    "llm 排队等待",
)
_REPLY_DONE_MARKERS = (
    "replied session=",
    "skip reply",
)


def window_title(light: str) -> str:
    """Put the status emoji to the right of the window name."""
    return f"{TITLE_BASE}  {light}"


def title_light(*, running: bool, healthy: bool, log_lines: Sequence[str]) -> str:
    """Pick red / green / yellow / blue from process health and newest logs."""
    latest = latest_meaningful_line(log_lines)
    if not running or not healthy:
        return LIGHT_OFF
    if latest and is_error_line(latest):
        return LIGHT_OFF
    if is_replying(log_lines):
        return LIGHT_BUSY
    if latest and is_warning_line(latest):
        return LIGHT_WARN
    return LIGHT_ON


def is_replying(log_lines: Sequence[str]) -> bool:
    """True when the newest reply-cycle marker is still in-flight."""
    for line in reversed(log_lines):
        if any(marker in line for marker in _REPLY_DONE_MARKERS):
            return False
        if any(marker in line for marker in _BUSY_MARKERS):
            return True
    return False


def latest_meaningful_line(log_lines: Sequence[str]) -> str:
    """Return the last non-empty log line, or empty if none."""
    for line in reversed(log_lines):
        if line.strip():
            return line
    return ""


def has_new_error(log_lines: Sequence[str]) -> bool:
    """True when new log text contains ERROR or CRITICAL."""
    return any(" ERROR " in line or " CRITICAL " in line for line in log_lines)
