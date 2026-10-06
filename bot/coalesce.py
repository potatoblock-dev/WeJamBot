"""Merge consecutive inbound WeChat lines from the same session before one reply."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace

from bot.channel import IncomingTurn

_log = logging.getLogger(__name__)

Handler = Callable[[IncomingTurn], None]


def merge_turns(turns: list[IncomingTurn]) -> IncomingTurn:
    """Join a burst of lines; keep the latest ids and skip duplicate consecutive text."""
    if not turns:
        raise ValueError("merge_turns requires at least one turn")
    if len(turns) == 1:
        return turns[0]
    last = turns[-1]
    texts: list[str] = []
    for item in turns:
        text = item.text.strip()
        if text and (not texts or texts[-1] != text):
            texts.append(text)
    return replace(
        last,
        text="\n".join(texts),
        preview_only=any(item.preview_only for item in turns),
        unread=max(item.unread for item in turns),
    )


class TurnCoalescer:
    """Debounce same-session lines on a timer thread (watch loop stays blocking)."""

    def __init__(
        self,
        handler: Handler,
        *,
        burst_window: float = 30.0,
        debounce: float = 3.0,
    ) -> None:
        self._handler = handler
        self._burst_window = max(1.0, burst_window)
        self._debounce = max(0.2, debounce)
        self._lock = threading.Lock()
        self._buffers: dict[str, list[IncomingTurn]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._last_arrival: dict[str, float] = {}
        self._deliver_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def submit(self, turn: IncomingTurn) -> None:
        """Queue one inbound line; flush after a pause or when the burst window expires."""
        session = turn.session_id
        overflow: list[IncomingTurn] = []
        with self._lock:
            now = time.time()
            gap = now - self._last_arrival.get(session, 0.0)
            if self._buffers.get(session) and gap > self._burst_window:
                overflow = self._take_unlocked(session)
            self._buffers.setdefault(session, []).append(turn)
            self._last_arrival[session] = now
            self._reschedule_unlocked(session)
        if overflow:
            self._deliver(overflow)

    def flush_pending(self) -> None:
        """Deliver every buffered session immediately (shutdown and tests)."""
        sessions: list[str]
        with self._lock:
            sessions = list(self._buffers)
        for session in sessions:
            with self._lock:
                batch = self._take_unlocked(session)
            self._deliver(batch)

    def _reschedule_unlocked(self, session: str) -> None:
        """Restart the debounce timer after another line lands."""
        old = self._timers.pop(session, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(self._debounce, self._on_timer, args=(session,))
        timer.daemon = True
        self._timers[session] = timer
        timer.start()

    def _take_unlocked(self, session: str) -> list[IncomingTurn]:
        """Pop the pending burst and cancel its timer."""
        old = self._timers.pop(session, None)
        if old is not None:
            old.cancel()
        return self._buffers.pop(session, [])

    def _on_timer(self, session: str) -> None:
        """Flush this session after the user paused typing."""
        with self._lock:
            batch = self._take_unlocked(session)
        try:
            self._deliver(batch)
        except Exception:
            _log.exception("coalesce deliver failed session=%s", session)

    def _deliver(self, batch: list[IncomingTurn]) -> None:
        """Merge and call the bot handler, one in-flight turn per session."""
        if not batch:
            return
        merged = merge_turns(batch)
        _log.info(
            "coalesce flush session=%s lines=%s preview=%r",
            merged.session_id,
            len(batch),
            merged.user_text[:120],
        )
        with self._deliver_locks[merged.session_id]:
            self._handler(merged)
