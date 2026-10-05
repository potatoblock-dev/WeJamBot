"""Colored HTML rendering for admin log lines."""

from __future__ import annotations

import html
import re

_TIME_PREFIX = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(.*)$"
)

_COLOR_TIME = "#6bbf6b"
_COLOR_BODY = "#f5f5f5"
_COLOR_ERROR = "#f87171"


def format_logs_html(lines: list[str]) -> str:
    """Render log lines as HTML with green timestamps and red error bodies."""
    if not lines:
        return (
            '<div style="font-family:Menlo,Monaco,monospace;font-size:12px;'
            f'color:{_COLOR_BODY};">（暂无日志）</div>'
        )
    body = "".join(f"<div>{_format_line(line)}</div>" for line in lines)
    return (
        '<div style="font-family:Menlo,Monaco,monospace;font-size:12px;'
        f'line-height:1.45;color:{_COLOR_BODY};">{body}</div>'
    )


def is_error_line(line: str) -> bool:
    """True for ERROR/CRITICAL levels, tracebacks, and exception continuations."""
    if " ERROR " in line or " CRITICAL " in line:
        return True
    stripped = line.lstrip()
    if stripped.startswith("Traceback"):
        return True
    if stripped.startswith('File "') or stripped.startswith("File '"):
        return True
    if "Error:" in line or "Exception:" in line:
        return True
    if "HTTPStatusError" in line or "Traceback" in line:
        return True
    if line.startswith("  ") and ("File " in line or "raise " in line):
        return True
    return False


def is_warning_line(line: str) -> bool:
    """True for logging WARNING records (not ERROR)."""
    if is_error_line(line):
        return False
    return " WARNING " in line or line.lstrip().startswith("WARNING")


def _format_line(line: str) -> str:
    """Color one log line: green time, white or red message text."""
    match = _TIME_PREFIX.match(line)
    is_error = is_error_line(line)
    if match:
        time_html = f'<span style="color:{_COLOR_TIME}">{html.escape(match.group(1))}</span>'
        body_color = _COLOR_ERROR if is_error else _COLOR_BODY
        body_html = (
            f'<span style="color:{body_color}">{html.escape(match.group(2))}</span>'
        )
        return f"{time_html} {body_html}"
    color = _COLOR_ERROR if is_error else _COLOR_BODY
    return f'<span style="color:{color}">{html.escape(line)}</span>'


_is_error_line = is_error_line
