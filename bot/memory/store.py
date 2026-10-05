"""SQLite short-term chat memory, keyed by session_id."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class MemoryStore:
    """Keep the last N user/assistant turns per session in SQLite."""

    def __init__(self, db_path: Path, max_turns: int) -> None:
        self._db_path = db_path
        self._max_turns = max(2, max_turns)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with row factory disabled (plain tuples)."""
        connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init(self) -> None:
        """Create the turns table if it does not exist."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_session "
                "ON chat_turns(session_id, created_at)"
            )

    def history(self, session_id: str) -> list[dict[str, str]]:
        """Return recent turns as OpenAI-style role/content dicts."""
        limit = self._max_turns * 2
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM chat_turns
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def append(self, session_id: str, role: str, content: str) -> None:
        """Insert one turn and trim older rows for this session."""
        text = (content or "").strip()
        if not text:
            return
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO chat_turns(session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, text, now),
            )
            connection.execute(
                """
                DELETE FROM chat_turns WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM chat_turns
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (session_id, session_id, self._max_turns * 2),
            )
