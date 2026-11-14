"""SQLite ring buffer of inbound (and bot) chat lines for observe digest and reply context."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TranscriptRow:
    """One persisted chat bubble in the local ring buffer."""

    id: int
    chat: str
    sender: str
    text: str
    from_self: bool
    created_at: float
    source: str


class TranscriptStore:
    """Keep the last N bubbles per chat display name in SQLite."""

    def __init__(self, db_path: Path, max_messages: int = 200) -> None:
        self._db_path = db_path
        self._max_messages = max(10, int(max_messages))
        self._init()

    def set_max_messages(self, max_messages: int) -> None:
        """Hot-update ring size from reply_policy (trim applies on next append)."""
        self._max_messages = max(10, int(max_messages))

    @property
    def db_path(self) -> Path:
        """SQLite file that holds the inbound_transcript ring."""
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        """Open a connection for one short transaction."""
        connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init(self) -> None:
        """Create the inbound_transcript table when missing."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_transcript (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    text TEXT NOT NULL,
                    from_self INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_chat "
                "ON inbound_transcript(chat, id)"
            )

    def append(
        self,
        chat: str,
        *,
        text: str,
        sender: str = "",
        from_self: bool = False,
        source: str = "observe",
        created_at: float | None = None,
    ) -> int:
        """Insert one bubble and trim older rows for this chat; return the new row id."""
        title = (chat or "").strip()
        body = (text or "").strip()
        if not title or not body:
            return 0
        stamp = time.time() if created_at is None else float(created_at)
        src = (source or "observe").strip() or "observe"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO inbound_transcript(
                    chat, sender, text, from_self, created_at, source
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    (sender or "").strip(),
                    body,
                    1 if from_self else 0,
                    stamp,
                    src,
                ),
            )
            row_id = int(cursor.lastrowid or 0)
            connection.execute(
                """
                DELETE FROM inbound_transcript WHERE chat = ? AND id NOT IN (
                    SELECT id FROM inbound_transcript
                    WHERE chat = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (title, title, self._max_messages),
            )
        return row_id

    def list_chats(self) -> list[tuple[str, int, float]]:
        """Return (chat, count, latest_created_at) ordered by newest activity first."""
        if not self._db_path.is_file():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chat, COUNT(*), MAX(created_at)
                FROM inbound_transcript
                GROUP BY chat
                ORDER BY MAX(created_at) DESC, chat COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            (str(item[0]), int(item[1]), float(item[2] or 0.0)) for item in rows
        ]

    def recent(self, chat: str, limit: int) -> list[TranscriptRow]:
        """Return the newest ``limit`` rows for a chat, oldest-first."""
        title = (chat or "").strip()
        cap = max(0, int(limit))
        if not title or cap <= 0:
            return []
        if not self._db_path.is_file():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, chat, sender, text, from_self, created_at, source
                FROM inbound_transcript
                WHERE chat = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (title, cap),
            ).fetchall()
        rows.reverse()
        return [_row(item) for item in rows]

    def since(
        self,
        chat: str,
        after_id: int,
        *,
        sender: str | None = None,
        limit: int = 200,
    ) -> list[TranscriptRow]:
        """Return rows after ``after_id`` for a chat (optional sender filter), oldest-first."""
        title = (chat or "").strip()
        cap = max(1, int(limit))
        if not title:
            return []
        with self._connect() as connection:
            if sender is None:
                rows = connection.execute(
                    """
                    SELECT id, chat, sender, text, from_self, created_at, source
                    FROM inbound_transcript
                    WHERE chat = ? AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (title, int(after_id), cap),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, chat, sender, text, from_self, created_at, source
                    FROM inbound_transcript
                    WHERE chat = ? AND id > ? AND sender = ? AND from_self = 0
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (title, int(after_id), sender.strip(), cap),
                ).fetchall()
        return [_row(item) for item in rows]

    def as_history(self, chat: str, limit: int) -> list[dict[str, str]]:
        """Map recent rows to OpenAI-style role/content dicts for the reply LLM."""
        out: list[dict[str, str]] = []
        for row in self.recent(chat, limit):
            if row.from_self:
                role = "assistant"
                body = row.text
            else:
                role = "user"
                body = row.text
                if row.sender:
                    body = f"[群友] {row.sender}\n{row.text}"
            if body.strip():
                out.append({"role": role, "content": body.strip()})
        return out


def _row(item: tuple[object, ...]) -> TranscriptRow:
    """Build a TranscriptRow from a SQLite tuple."""
    return TranscriptRow(
        id=int(item[0]),
        chat=str(item[1]),
        sender=str(item[2]),
        text=str(item[3]),
        from_self=bool(item[4]),
        created_at=float(item[5]),
        source=str(item[6]),
    )
