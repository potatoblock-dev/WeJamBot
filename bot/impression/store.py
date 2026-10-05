"""Load and save per-user impression JSON keyed by WeChat session identity."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_filename(user_key: str) -> str:
    """Turn a session key into a single path component."""
    name = _SAFE_ID.sub("_", user_key.strip()) or "unknown"
    return name[:80]


class ImpressionStore:
    """One JSON file per speaker/session under data/impressions/."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, user_key: str) -> Path:
        """Return the JSON path for this identity."""
        return self._root / f"{_safe_filename(user_key)}.json"

    def load(self, user_key: str) -> dict[str, Any]:
        """Read the impression file, or a blank record if it does not exist."""
        path = self.path_for(user_key)
        if not path.is_file():
            return _blank(user_key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _blank(user_key)
        if not isinstance(data, dict):
            return _blank(user_key)
        return _normalize(user_key, data)

    def save(self, record: dict[str, Any]) -> Path:
        """Write the impression JSON for a single bot process."""
        user_key = str(record.get("user_key") or "")
        path = self.path_for(user_key)
        payload = _normalize(user_key, record)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return path

    def touch(self, user_key: str, username: str = "") -> dict[str, Any]:
        """Create or refresh identity fields without wiping impression."""
        record = self.load(user_key)
        if username:
            record["username"] = username
        self.save(record)
        return self.load(user_key)

    def list_records(self) -> list[dict[str, Any]]:
        """Load every impression JSON on disk."""
        records: list[dict[str, Any]] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                key = str(data.get("user_key") or path.stem)
                records.append(_normalize(key, data))
        return records

    def filled_records(self) -> list[dict[str, Any]]:
        """Return impression files that actually have body text."""
        return [
            record
            for record in self.list_records()
            if str(record.get("impression") or "").strip()
        ]

    def directory_label(self, record: dict[str, Any]) -> str:
        """Format one impression row as a roster line."""
        username = str(record.get("username") or "").strip()
        user_key = str(record.get("user_key") or "").strip()
        if username:
            return username
        short = user_key[:16] if user_key else "?"
        return f"（无昵称）{short}"

    def directory_block(self, speaker_name: str = "") -> str:
        """Compact roster of files that have impression text."""
        filled = self.filled_records()
        lines = [
            f"【已知印象】共 {len(filled)} 份"
            "（按昵称对应；问有几份、是谁时按本名单如实说。）"
        ]
        for record in filled:
            lines.append(f"- {self.directory_label(record)}")
        speaker = (speaker_name or "").strip()
        if speaker:
            lines.append(f"本轮说话的是：{speaker}")
        return "\n".join(lines)

    def others_block(self, exclude_key: str = "") -> str:
        """Full impression bodies for roster members other than the current speaker."""
        skip = (exclude_key or "").strip()
        chunks: list[str] = []
        for record in self.filled_records():
            key = str(record.get("user_key") or "").strip()
            if skip and key == skip:
                continue
            body = str(record.get("impression") or "").strip()
            if not body:
                continue
            label = self.directory_label(record)
            chunks.append(f"【印象·{label}】\n{body}")
        if not chunks:
            return ""
        header = (
            "【其他人的印象正文】评价、对比、提起某人时用对应档；"
            "不要串到当前说话人身上，也不要把全文逐字念给群友。"
        )
        return header + "\n\n" + "\n\n".join(chunks)


def _blank(user_key: str) -> dict[str, Any]:
    """Empty impression used until the model writes one."""
    return {
        "user_key": user_key,
        "username": "",
        "impression": "",
        "updated_at": "",
    }


def _normalize(user_key: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields with string values."""
    blank = _blank(user_key)
    for key in ("user_key", "username", "impression", "updated_at"):
        if key in data and data[key] is not None:
            blank[key] = str(data[key])
    blank["user_key"] = user_key or blank["user_key"]
    return blank
