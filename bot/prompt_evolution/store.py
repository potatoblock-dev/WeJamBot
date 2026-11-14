"""Pending proposals and approved per-group prompt evolution overlays."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+")


def _safe_filename(chat: str) -> str:
    """Turn a WeChat chat title into a single path component."""
    name = _SAFE_ID.sub("_", chat.strip()) or "unknown"
    return name[:80]


def _now_iso() -> str:
    """UTC timestamp for JSON records."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PromptEvolutionStore:
    """Disk layout: pending/, active/, state/ under data/prompt_evolution/."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._pending = root / "pending"
        self._active = root / "active"
        self._state = root / "state"
        for path in (self._pending, self._active, self._state):
            path.mkdir(parents=True, exist_ok=True)

    def active_path(self, chat: str) -> Path:
        """JSON path for the approved overlay of this chat."""
        return self._active / f"{_safe_filename(chat)}.json"

    def state_path(self, chat: str) -> Path:
        """Per-chat received-message counters for proposal throttle."""
        return self._state / f"{_safe_filename(chat)}.json"

    def load_active(self, chat: str) -> dict[str, Any]:
        """Read the approved overlay, or a blank record if missing."""
        path = self.active_path(chat)
        if not path.is_file():
            return _blank_active(chat)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _blank_active(chat)
        if not isinstance(data, dict):
            return _blank_active(chat)
        return _normalize_active(chat, data)

    def save_active(self, record: dict[str, Any]) -> Path:
        """Write an approved overlay for one group chat."""
        chat = str(record.get("chat") or "")
        path = self.active_path(chat)
        payload = _normalize_active(chat, record)
        payload["updated_at"] = _now_iso()
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def disable_active(self, chat: str) -> bool:
        """Remove the approved overlay for this chat. True if a file was deleted."""
        path = self.active_path(chat)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list_active(self) -> list[dict[str, Any]]:
        """Load every approved overlay on disk."""
        records: list[dict[str, Any]] = []
        for path in sorted(self._active.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                records.append(
                    _normalize_active(str(data.get("chat") or path.stem), data)
                )
        return records

    def list_pending(self) -> list[dict[str, Any]]:
        """Load every pending proposal, newest first."""
        records: list[dict[str, Any]] = []
        for path in self._pending.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                records.append(_normalize_pending(data))
        records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return records

    def load_pending(self, proposal_id: str) -> dict[str, Any] | None:
        """Load one pending proposal by id, or None."""
        path = self._pending / f"{proposal_id.strip()}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return _normalize_pending(data)

    def has_pending_for(self, chat: str) -> bool:
        """True when any pending proposal targets this chat title."""
        key = chat.strip()
        if not key:
            return False
        for row in self.list_pending():
            if str(row.get("chat") or "").strip() == key:
                return True
        return False

    def note_received(self, chat: str) -> int:
        """Count one inbound message the bot received for this chat; return new total."""
        key = chat.strip()
        if not key:
            return 0
        state = self._load_state(key)
        state["received"] = int(state.get("received") or 0) + 1
        self._save_state(key, state)
        return int(state["received"])

    def can_propose(self, chat: str, min_messages: int) -> bool:
        """Whether a new auto proposal is allowed for this chat (by received count)."""
        if self.has_pending_for(chat):
            return False
        threshold = max(0, int(min_messages))
        state = self._load_state(chat)
        received = int(state.get("received") or 0)
        proposed_at = int(state.get("proposed_at_received") or 0)
        if threshold <= 0:
            return True
        return (received - proposed_at) >= threshold

    def add_pending(
        self,
        *,
        chat: str,
        proposed: str,
        rationale: str = "",
        source_turn: str = "",
    ) -> dict[str, Any]:
        """Create a pending proposal and stamp the message-count throttle."""
        proposal_id = uuid.uuid4().hex
        created = _now_iso()
        record = _normalize_pending(
            {
                "id": proposal_id,
                "chat": chat.strip(),
                "created_at": created,
                "source_turn": source_turn,
                "proposed": proposed,
                "rationale": rationale,
            }
        )
        path = self._pending / f"{proposal_id}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._mark_proposed(chat)
        return record

    def reject_pending(self, proposal_id: str) -> bool:
        """Delete a pending proposal. True if a file was removed."""
        path = self._pending / f"{proposal_id.strip()}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def approve_pending(self, proposal_id: str, label: str = "") -> dict[str, Any] | None:
        """Promote pending → active for that chat, then delete pending."""
        pending = self.load_pending(proposal_id)
        if pending is None:
            return None
        chat = str(pending.get("chat") or "").strip()
        if not chat:
            return None
        existing = self.load_active(chat)
        active = _normalize_active(
            chat,
            {
                "chat": chat,
                "label": label.strip() or str(existing.get("label") or ""),
                "prompt": str(pending.get("proposed") or ""),
                "from_proposal_id": str(pending.get("id") or ""),
            },
        )
        self.save_active(active)
        self.reject_pending(proposal_id)
        return self.load_active(chat)

    def display_label(self, record: dict[str, Any]) -> str:
        """Short list label: human label, else chat title."""
        label = str(record.get("label") or "").strip()
        if label:
            return label
        chat = str(record.get("chat") or "").strip()
        return chat or "?"

    def _load_state(self, chat: str) -> dict[str, Any]:
        """Read per-chat received counters; missing file → zeros."""
        path = self.state_path(chat)
        blank = {
            "chat": chat.strip(),
            "received": 0,
            "proposed_at_received": 0,
        }
        if not path.is_file():
            return blank
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return blank
        if not isinstance(data, dict):
            return blank
        blank["received"] = max(0, int(data.get("received") or 0))
        blank["proposed_at_received"] = max(
            0, int(data.get("proposed_at_received") or 0)
        )
        return blank

    def _save_state(self, chat: str, state: dict[str, Any]) -> None:
        """Persist received / proposed_at_received for this chat."""
        path = self.state_path(chat)
        payload = {
            "chat": chat.strip(),
            "received": max(0, int(state.get("received") or 0)),
            "proposed_at_received": max(
                0, int(state.get("proposed_at_received") or 0)
            ),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _mark_proposed(self, chat: str) -> None:
        """Set proposed_at_received to the current received count after a proposal."""
        key = chat.strip()
        if not key:
            return
        state = self._load_state(key)
        state["proposed_at_received"] = int(state.get("received") or 0)
        self._save_state(key, state)


def _blank_active(chat: str) -> dict[str, Any]:
    """Empty approved overlay until admin accepts a proposal."""
    return {
        "chat": chat,
        "label": "",
        "prompt": "",
        "updated_at": "",
        "from_proposal_id": "",
    }


def _normalize_active(chat: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known active fields as strings."""
    blank = _blank_active(chat)
    for key in ("chat", "label", "prompt", "updated_at", "from_proposal_id"):
        if key in data and data[key] is not None:
            blank[key] = str(data[key])
    blank["chat"] = chat or blank["chat"]
    return blank


def _normalize_pending(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known pending fields as strings."""
    return {
        "id": str(data.get("id") or ""),
        "chat": str(data.get("chat") or ""),
        "created_at": str(data.get("created_at") or ""),
        "source_turn": str(data.get("source_turn") or ""),
        "proposed": str(data.get("proposed") or ""),
        "rationale": str(data.get("rationale") or ""),
    }
