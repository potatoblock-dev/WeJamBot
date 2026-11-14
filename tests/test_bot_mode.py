"""Global listen/reply mode helpers and consume routing."""

from __future__ import annotations

from pathlib import Path

from bot.bot_mode import (
    MODE_LISTEN,
    MODE_REPLY,
    apply_bot_mode,
    load_bot_mode,
    save_bot_mode,
    toggle_bot_mode,
)
from bot.channel import IncomingTurn
from bot.eyes import SeenMessage
from bot.runner import consume_messages
from tests.test_bot_brain import FakeEyes


def test_apply_listen_folds_allow_into_observe() -> None:
    """Listen mode clears allow and still observes former whitelist chats."""
    allow, observe = apply_bot_mode(
        MODE_LISTEN, allow={"白糖"}, observe={"99+"}
    )
    assert allow == set()
    assert observe == {"白糖", "99+"}


def test_apply_reply_keeps_lists() -> None:
    """Reply mode leaves allow/observe unchanged."""
    allow, observe = apply_bot_mode(
        MODE_REPLY, allow={"白糖"}, observe={"99+"}
    )
    assert allow == {"白糖"}
    assert observe == {"99+"}


def test_toggle_and_persist(tmp_path: Path) -> None:
    """Mode file round-trips and toggles listen ↔ reply."""
    path = tmp_path / "bot_mode.txt"
    assert load_bot_mode(path) == MODE_LISTEN
    save_bot_mode(path, MODE_REPLY)
    assert load_bot_mode(path) == MODE_REPLY
    assert toggle_bot_mode(MODE_REPLY) == MODE_LISTEN


def test_consume_listen_routes_allow_to_observe() -> None:
    """In listen mode, an allow-listed chat is observed, not replied."""
    eyes = FakeEyes(
        [
            SeenMessage(
                chat="白糖",
                sender="",
                text="你好",
                chat_type="friend",
                preview=True,
            ),
        ]
    )
    replies: list[IncomingTurn] = []
    observes: list[IncomingTurn] = []
    consume_messages(
        eyes,
        replies.append,
        allowed={"白糖"},
        observe=set(),
        observe_submit=observes.append,
        mode=MODE_LISTEN,
    )
    assert replies == []
    assert len(observes) == 1
    assert observes[0].chat == "白糖"
