"""Global listen vs reply mode for the chat runner (hot-reloaded from disk)."""

from __future__ import annotations

from pathlib import Path

MODE_LISTEN = "listen"
MODE_REPLY = "reply"
MODE_LABELS = {
    MODE_LISTEN: "监听",
    MODE_REPLY: "回复",
}


def normalize_bot_mode(value: object) -> str:
    """Map a stored/UI value to listen or reply."""
    key = str(value or "").strip().lower()
    if key in {MODE_REPLY, "reply", "回复", "chat"}:
        return MODE_REPLY
    return MODE_LISTEN


def bot_mode_label(mode: str) -> str:
    """Chinese label for the Run-tab toggle button."""
    return MODE_LABELS[normalize_bot_mode(mode)]


def load_bot_mode(path: Path) -> str:
    """Read bot mode from a one-line file; default listen when missing."""
    if not path.is_file():
        return MODE_LISTEN
    try:
        raw = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return MODE_LISTEN
    if not raw:
        return MODE_LISTEN
    return normalize_bot_mode(raw[0])


def save_bot_mode(path: Path, mode: str) -> Path:
    """Persist listen/reply mode for the next inbound message."""
    value = normalize_bot_mode(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return path


def toggle_bot_mode(mode: str) -> str:
    """Flip listen ↔ reply."""
    current = normalize_bot_mode(mode)
    return MODE_REPLY if current == MODE_LISTEN else MODE_LISTEN


def apply_bot_mode(
    mode: str,
    *,
    allow: set[str],
    observe: set[str],
) -> tuple[set[str], set[str]]:
    """Return effective allow/observe sets for the current global mode.

    Listen mode never replies: allow chats are folded into observe so they still
    archive. Reply mode uses the lists as saved.
    """
    if normalize_bot_mode(mode) == MODE_REPLY:
        return set(allow), set(observe)
    # listen: no replies; whitelist sessions still get observe/archive.
    return set(), set(observe) | set(allow)
