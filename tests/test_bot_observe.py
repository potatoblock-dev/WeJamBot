"""Observe-only archive path: no send, writers see OBSERVE_NO_REPLY."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.archive_text import OBSERVE_NO_REPLY
from bot.channel import IncomingTurn, should_handle, should_observe
from bot.chat import ChatBot
from bot.group_culture.store import GroupCultureStore
from bot.group_culture.writer import GroupCultureWriter
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.memory.store import MemoryStore
from bot.reply import TextSender
from bot.reply_policy import ReplyGate
from bot.runner import consume_messages
from bot.transcript.store import TranscriptStore
from tests.test_bot_brain import FakeEyes
from bot.eyes import SeenMessage


class _RecordingArchive:
    """Archive fake that records complete_plain prompts and returns fixed text."""

    def __init__(self) -> None:
        self.plain_calls: list[tuple[str, str]] = []
        self.complete_calls = 0

    def is_configured(self) -> bool:
        """Always configured so writers run."""
        return True

    async def complete_plain(
        self, system: str, user_text: str, max_tokens: int = 400
    ) -> str:
        """Record the user prompt and return a stub archive body."""
        self.plain_calls.append((system, user_text))
        return "旁听备忘"

    async def complete(self, *args: object, **kwargs: object) -> str:
        """Must not be used on the observe path."""
        self.complete_calls += 1
        return "不应回复"


class _SilentSender:
    """TextSender stand-in that records send attempts."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_bubbles(self, chat: str, text: str) -> list[str]:
        """Record and pretend delivery succeeded (should not be called on observe)."""
        self.sent.append((chat, text))
        return [text]


def test_should_observe_excludes_allow_overlap() -> None:
    """Reply allowlist wins: same chat must not also take the observe path."""
    turn = IncomingTurn(chat="测试群", sender="甲", text="你好", chat_type="group")
    assert should_handle(turn, {"测试群"})
    assert not should_observe(turn, {"测试群"}, {"测试群"})
    assert should_observe(turn, {"测试群"}, set())


def test_observe_updates_impression_without_send(tmp_path: Path) -> None:
    """observe() writes impression via archive LLM and never sends."""
    policy = tmp_path / "reply_policy.toml"
    policy.write_text(
        "enabled = true\ngroup = true\nobserve_min_interval_seconds = 0\n"
        "group_culture_auto = true\nprompt_evolution_auto = false\n",
        encoding="utf-8",
    )
    archive = _RecordingArchive()
    impressions = ImpressionStore(tmp_path / "impressions")
    culture = GroupCultureStore(tmp_path / "group_culture")
    db = tmp_path / "bot.sqlite3"
    memory = MemoryStore(db, 8)
    bot = ChatBot(
        archive,  # type: ignore[arg-type]  # chat complete unused on observe
        memory,
        ReplyGate(policy),
        impressions,
        ImpressionWriter(archive, impressions),
        group_culture=culture,
        group_culture_writer=GroupCultureWriter(archive, culture),
        transcript=TranscriptStore(db, 50),
    )
    sender = _SilentSender()
    bot.bind_sender(sender)  # type: ignore[arg-type]
    turn = IncomingTurn(
        chat="测试群",
        sender="甲",
        text="今天好冷",
        chat_type="group",
    )
    asyncio.run(bot.observe(turn))
    assert sender.sent == []
    assert archive.complete_calls == 0
    assert archive.plain_calls
    joined = "\n".join(body for _, body in archive.plain_calls)
    assert OBSERVE_NO_REPLY in joined or "未回复" in joined
    record = impressions.load("测试群:甲")
    assert "旁听备忘" in str(record.get("impression") or "")
    culture_rec = culture.load("测试群")
    assert "旁听备忘" in str(culture_rec.get("culture") or "")


def test_consume_routes_observe_only(tmp_path: Path) -> None:
    """Runner routes observe-list chats to observe_submit, not reply submit."""
    eyes = FakeEyes(
        [
            SeenMessage(
                chat="旁听群",
                sender="乙",
                text="看看",
                chat_type="group",
                preview=True,
            ),
        ]
    )
    replies: list[IncomingTurn] = []
    observes: list[IncomingTurn] = []
    consume_messages(
        eyes,
        replies.append,
        allowed=set(),
        observe={"旁听群"},
        observe_submit=observes.append,
    )
    assert replies == []
    assert len(observes) == 1
    assert observes[0].chat == "旁听群"


def test_consume_logs_not_listed(caplog, tmp_path: Path) -> None:
    """Inbound chats outside allow/observe leave a not_listed log line."""
    import logging

    eyes = FakeEyes(
        [
            SeenMessage(
                chat="99+",
                sender="甲",
                text="测试",
                chat_type="group",
                preview=True,
            ),
        ]
    )
    with caplog.at_level(logging.INFO, logger="bot.runner"):
        consume_messages(
            eyes,
            lambda _t: None,
            allowed=set(),
            observe=set(),
            observe_submit=lambda _t: None,
        )
    assert any("not_listed" in r.message and "99+" in r.message for r in caplog.records)
