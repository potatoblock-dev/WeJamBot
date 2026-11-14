"""Persist the WeChat chat whitelist used by the runner and admin console."""

from __future__ import annotations

from pathlib import Path


def load_allow_chats(path: Path) -> set[str]:
    """Read non-empty, non-comment lines as allowed session names."""
    if not path.is_file():
        return set()
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line)
    return names


def save_allow_chats(path: Path, names: list[str] | set[str]) -> Path:
    """Rewrite the whitelist file, one chat name per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    unique = sorted({str(name).strip() for name in names if str(name).strip()})
    body = "\n".join(unique)
    path.write_text((body + "\n") if body else "", encoding="utf-8")
    return path


def merge_chat_names_for_picker(live: list[str], allowed: set[str] | list[str]) -> list[str]:
    """Build the checkbox list: live sessions first, then allowed names not in live.

    Names already on the whitelist stay visible (and checked) even when the
    session list briefly omits them, so a refresh cannot drop allow entries.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in live:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    for raw in sorted({str(n).strip() for n in allowed if str(n).strip()}):
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out
