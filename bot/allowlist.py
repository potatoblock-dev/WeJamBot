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
