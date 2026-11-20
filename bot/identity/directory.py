"""Map (chat display name, nick) aliases onto stable host-side person_id values."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.impression.store import ImpressionStore


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for alias rows."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _alias_key(chat: str, nick: str) -> str:
    """Normalize a chat+nick pair into a lookup key."""
    return f"{(chat or '').strip()}\0{(nick or '').strip()}"


class IdentityDirectory:
    """JSON alias table: display names resolve to UUID person_id values."""

    def __init__(self, path: Path, impressions: ImpressionStore) -> None:
        self._path = path
        self._impressions = impressions
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._aliases: list[dict[str, str]] = []
        self._load()
        self.migrate_legacy_impressions()

    def _load(self) -> None:
        """Read aliases.json from disk (empty table when missing/corrupt)."""
        if not self._path.is_file():
            self._aliases = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._aliases = []
            return
        rows = data.get("aliases") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            self._aliases = []
            return
        cleaned: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            person_id = str(row.get("person_id") or "").strip()
            chat = str(row.get("chat") or "").strip()
            if not person_id or not chat:
                continue
            cleaned.append(
                {
                    "chat": chat,
                    "nick": str(row.get("nick") or "").strip(),
                    "person_id": person_id,
                    "updated_at": str(row.get("updated_at") or ""),
                }
            )
        self._aliases = cleaned

    def _save(self) -> None:
        """Persist the in-memory alias table."""
        payload = {"aliases": self._aliases}
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def resolve(self, chat: str, nick: str = "") -> str:
        """Return person_id for this chat+nick, creating one when unseen."""
        title = (chat or "").strip()
        name = (nick or "").strip()
        if not title:
            raise ValueError("resolve requires a chat display name")
        with self._lock:
            existing = self._find_unlocked(title, name)
            if existing:
                return existing
            person_id = str(uuid.uuid4())
            self._aliases.append(
                {
                    "chat": title,
                    "nick": name,
                    "person_id": person_id,
                    "updated_at": _utc_now(),
                }
            )
            self._save()
            return person_id

    def lookup(self, chat: str, nick: str = "") -> str | None:
        """Return person_id when the alias exists, else None (no create)."""
        with self._lock:
            return self._find_unlocked((chat or "").strip(), (nick or "").strip())

    def _find_unlocked(self, chat: str, nick: str) -> str | None:
        """Find person_id for an exact chat+nick alias."""
        want = _alias_key(chat, nick)
        for row in self._aliases:
            if _alias_key(row["chat"], row["nick"]) == want:
                return row["person_id"]
        return None

    def aliases_for(self, person_id: str) -> list[dict[str, str]]:
        """Return all alias rows pointing at this person."""
        pid = (person_id or "").strip()
        with self._lock:
            return [dict(row) for row in self._aliases if row["person_id"] == pid]

    def list_persons(self) -> list[dict[str, Any]]:
        """Summarize each person_id with current nicknames for the admin UI."""
        with self._lock:
            by_id: dict[str, list[dict[str, str]]] = {}
            for row in self._aliases:
                by_id.setdefault(row["person_id"], []).append(dict(row))
        out: list[dict[str, Any]] = []
        for person_id, aliases in sorted(by_id.items(), key=lambda item: item[0]):
            record = self._impressions.load(person_id)
            labels = []
            for alias in aliases:
                nick = alias["nick"]
                chat = alias["chat"]
                labels.append(f"{chat}:{nick}" if nick else chat)
            out.append(
                {
                    "person_id": person_id,
                    "username": str(record.get("username") or ""),
                    "impression": str(record.get("impression") or ""),
                    "aliases": aliases,
                    "alias_labels": labels,
                    "updated_at": str(record.get("updated_at") or ""),
                }
            )
        # Include orphan impression files keyed by UUID with no alias yet.
        known = set(by_id)
        for record in self._impressions.list_records():
            key = str(record.get("user_key") or "").strip()
            if not key or key in known:
                continue
            if not _looks_like_person_id(key):
                continue
            out.append(
                {
                    "person_id": key,
                    "username": str(record.get("username") or ""),
                    "impression": str(record.get("impression") or ""),
                    "aliases": [],
                    "alias_labels": [],
                    "updated_at": str(record.get("updated_at") or ""),
                }
            )
        return out

    def link_alias(
        self,
        person_id: str,
        chat: str,
        nick: str = "",
        *,
        reassign: bool = True,
    ) -> str:
        """Point ``(chat, nick)`` at ``person_id`` so cross-group uses one impression.

        Creates a new alias row when unseen. If the pair already points at another
        person and ``reassign`` is true, rewrites that row (other aliases on the
        old person stay). Returns the target ``person_id``.
        """
        target = (person_id or "").strip()
        title = (chat or "").strip()
        name = (nick or "").strip()
        if not target:
            raise ValueError("link_alias requires person_id")
        if not _looks_like_person_id(target):
            raise ValueError("person_id must be a UUID")
        if not title:
            raise ValueError("link_alias requires a chat display name")
        with self._lock:
            existing = self._find_unlocked(title, name)
            if existing == target:
                return target
            if existing and not reassign:
                raise ValueError(
                    f"alias already bound to {existing[:8]}…; pass reassign=True to move"
                )
            if existing:
                for row in self._aliases:
                    if _alias_key(row["chat"], row["nick"]) == _alias_key(title, name):
                        row["person_id"] = target
                        row["updated_at"] = _utc_now()
                        break
            else:
                self._aliases.append(
                    {
                        "chat": title,
                        "nick": name,
                        "person_id": target,
                        "updated_at": _utc_now(),
                    }
                )
            self._save()
            # Ensure an impression file exists so console edit/save has a home.
            self._impressions.touch(target, username=name or title)
            return target

    def merge(self, from_person_id: str, into_person_id: str) -> dict[str, Any]:
        """Merge from_person into into_person: impressions + all aliases."""
        source = (from_person_id or "").strip()
        target = (into_person_id or "").strip()
        if not source or not target:
            raise ValueError("merge requires from_person_id and into_person_id")
        if source == target:
            raise ValueError("cannot merge a person into itself")
        with self._lock:
            src_rec = self._impressions.load(source)
            dst_rec = self._impressions.load(target)
            src_body = str(src_rec.get("impression") or "").strip()
            dst_body = str(dst_rec.get("impression") or "").strip()
            if src_body and dst_body and src_body != dst_body:
                dst_rec["impression"] = (
                    f"{dst_body}\n\n【曾用印象·合并自另一昵称】\n{src_body}"
                )[:1200]
            elif src_body and not dst_body:
                dst_rec["impression"] = src_body[:1200]
            src_name = str(src_rec.get("username") or "").strip()
            if src_name and not str(dst_rec.get("username") or "").strip():
                dst_rec["username"] = src_name
            dst_rec["user_key"] = target
            self._impressions.save(dst_rec)
            for row in self._aliases:
                if row["person_id"] == source:
                    row["person_id"] = target
                    row["updated_at"] = _utc_now()
            self._save()
            self._impressions.delete(source)
            return self._impressions.load(target)

    def migrate_legacy_impressions(self) -> int:
        """Assign person_ids to legacy ``群:昵称`` / bare-chat impression files.

        Returns the number of files migrated.
        """
        moved = 0
        with self._lock:
            for record in list(self._impressions.list_records()):
                key = str(record.get("user_key") or "").strip()
                if not key or _looks_like_person_id(key):
                    continue
                chat, nick = _split_legacy_key(key)
                if not chat:
                    continue
                existing = self._find_unlocked(chat, nick)
                if existing:
                    person_id = existing
                else:
                    person_id = str(uuid.uuid4())
                    self._aliases.append(
                        {
                            "chat": chat,
                            "nick": nick,
                            "person_id": person_id,
                            "updated_at": _utc_now(),
                        }
                    )
                username = str(record.get("username") or "").strip() or (
                    nick or chat
                )
                body = str(record.get("impression") or "")
                new_rec = self._impressions.load(person_id)
                if body and not str(new_rec.get("impression") or "").strip():
                    new_rec["impression"] = body
                if username:
                    new_rec["username"] = username
                new_rec["user_key"] = person_id
                self._impressions.save(new_rec)
                if self._impressions.path_for(key) != self._impressions.path_for(
                    person_id
                ):
                    self._impressions.delete(key)
                moved += 1
            if moved:
                self._save()
        return moved


def _looks_like_person_id(value: str) -> bool:
    """True when value looks like a UUID person_id (not ``群:昵称``)."""
    text = (value or "").strip()
    if len(text) != 36 or text.count("-") != 4:
        return False
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


def _split_legacy_key(user_key: str) -> tuple[str, str]:
    """Split a legacy impression key into (chat, nick)."""
    key = (user_key or "").strip()
    if not key:
        return "", ""
    if ":" in key:
        chat, nick = key.split(":", 1)
        return chat.strip(), nick.strip()
    return key, ""
