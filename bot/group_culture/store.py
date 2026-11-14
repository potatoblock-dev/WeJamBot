"""Load and save per-chat group culture JSON (WeChat chat name as key)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+")


def _safe_filename(chat: str) -> str:
    """Turn a WeChat chat title into a single path component."""
    name = _SAFE_ID.sub("_", chat.strip()) or "unknown"
    return name[:80]


class GroupCultureStore:
    """One JSON file per group chat under data/group_culture/."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, chat: str) -> Path:
        """Return the JSON path for this chat title."""
        return self._root / f"{_safe_filename(chat)}.json"

    def load(self, chat: str) -> dict[str, Any]:
        """Read the culture file, or a blank record if it does not exist."""
        path = self.path_for(chat)
        if not path.is_file():
            return _blank(chat)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _blank(chat)
        if not isinstance(data, dict):
            return _blank(chat)
        return _normalize(chat, data)

    def save(self, record: dict[str, Any]) -> Path:
        """Write the culture JSON for a single bot process."""
        chat = str(record.get("chat") or "")
        path = self.path_for(chat)
        payload = _normalize(chat, record)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def touch(self, chat: str, label: str = "") -> dict[str, Any]:
        """Create the file if missing; optionally set label without wiping culture."""
        record = self.load(chat)
        if label.strip():
            record["label"] = label.strip()
        self.save(record)
        return self.load(chat)

    def clear_culture(self, chat: str) -> Path:
        """Wipe culture text but keep the record and label."""
        record = self.load(chat)
        record["culture"] = ""
        return self.save(record)

    def list_records(self) -> list[dict[str, Any]]:
        """Load every group-culture JSON on disk."""
        records: list[dict[str, Any]] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                records.append(_normalize(str(data.get("chat") or path.stem), data))
        return records

    def display_label(self, record: dict[str, Any]) -> str:
        """Short list label: human label, else chat title."""
        label = str(record.get("label") or "").strip()
        if label:
            return label
        chat = str(record.get("chat") or "").strip()
        return chat or "?"


def _blank(chat: str) -> dict[str, Any]:
    """Empty culture used until the model or admin writes one."""
    return {
        "chat": chat,
        "label": "",
        "culture": "",
        "updated_at": "",
    }


def _normalize(chat: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields with string values."""
    blank = _blank(chat)
    for key in ("chat", "label", "culture", "updated_at"):
        if key in data and data[key] is not None:
            blank[key] = str(data[key])
    blank["chat"] = chat or blank["chat"]
    return blank
