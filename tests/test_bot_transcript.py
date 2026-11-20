"""Local inbound transcript ring and observe batch digest."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.archive_text import OBSERVE_NO_REPLY
from bot.channel import IncomingTurn
from bot.chat import ChatBot
from bot.eyes import SeenMessage
from bot.group_culture.store import GroupCultureStore
from bot.group_culture.writer import GroupCultureWriter
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.memory.store import MemoryStore
from bot.reply_policy import ReplyGate
from bot.transcript.focus import apply_recent_focus
from bot.transcript.store import TranscriptStore
from tests.test_bot_observe import _RecordingArchive, _SilentSender


def test_transcript_append_and_trim(tmp_path: Path) -> None:
    """Ring keeps only the newest max_messages rows per chat."""
    store = TranscriptStore(tmp_path / "bot.sqlite3", max_messages=10)
    for i in range(15):
        store.append("99+", text=f"m{i}", sender="甲", source="observe")
    rows = store.recent("99+", limit=50)
    assert len(rows) == 10
    assert rows[0].text == "m5"
    assert rows[-1].text == "m14"


def test_apply_recent_focus_marks_tail() -> None:
    """Focus marker sits before the last N rows; older rows stay ahead."""
    history = [
        {"role": "user", "content": f"m{i}"} for i in range(5)
    ]
    out = apply_recent_focus(history, 2)
    assert out[0]["content"] == "m0"
    assert out[1]["content"] == "m1"
    assert out[2]["content"] == "m2"
    assert "近期对话优先" in out[3]["content"]
    assert out[4]["content"] == "m3"
    assert out[5]["content"] == "m4"
    assert apply_recent_focus(history, 0) == history


def test_transcript_list_chats(tmp_path: Path) -> None:
    """list_chats summarizes per-chat counts for the admin browser."""
    store = TranscriptStore(tmp_path / "bot.sqlite3", max_messages=50)
    assert store.list_chats() == []
    store.append("群A", text="1", sender="甲", source="observe")
    store.append("群B", text="2", sender="乙", source="allow")
    store.append("群A", text="3", sender="甲", source="observe")
    chats = store.list_chats()
    by_name = {name: count for name, count, _at in chats}
    assert by_name == {"群A": 2, "群B": 1}
    assert {name for name, _count, _at in chats} == {"群A", "群B"}
    # Newest activity first: 群A was written last.
    assert chats[0][0] == "群A"


def test_observe_defers_until_digest_count(tmp_path: Path) -> None:
    """With a long interval, archive writers wait until digest_min_messages."""
    policy = tmp_path / "reply_policy.toml"
    policy.write_text(
        "enabled = true\ngroup = true\n"
        "observe_min_interval_seconds = 9999\n"
        "observe_digest_min_messages = 3\n"
        "group_culture_auto = true\nprompt_evolution_auto = false\n",
        encoding="utf-8",
    )
    archive = _RecordingArchive()
    impressions = ImpressionStore(tmp_path / "impressions")
    culture = GroupCultureStore(tmp_path / "group_culture")
    db = tmp_path / "bot.sqlite3"
    memory = MemoryStore(db, 8)
    transcript = TranscriptStore(db, 50)
    bot = ChatBot(
        archive,  # type: ignore[arg-type]
        memory,
        ReplyGate(policy),
        impressions,
        ImpressionWriter(archive, impressions),
        group_culture=culture,
        group_culture_writer=GroupCultureWriter(archive, culture),
        transcript=transcript,
    )
    bot.bind_sender(_SilentSender())  # type: ignore[arg-type]
    for i in range(2):
        asyncio.run(
            bot.observe(
                IncomingTurn(
                    chat="测试群",
                    sender="甲",
                    text=f"消息{i}",
                    chat_type="group",
                )
            )
        )
    assert archive.plain_calls == []
    assert len(transcript.recent("测试群", 10)) == 2
    asyncio.run(
        bot.observe(
            IncomingTurn(
                chat="测试群",
                sender="甲",
                text="消息2",
                chat_type="group",
            )
        )
    )
    assert archive.plain_calls
    joined = "\n".join(body for _, body in archive.plain_calls)
    assert "消息0" in joined and "消息2" in joined
    assert OBSERVE_NO_REPLY not in joined or "未回复" in joined


def test_handle_prefers_transcript_skips_read_context(tmp_path: Path) -> None:
    """Enough local transcript lines become history; eyes.read_context is not called."""
    policy = tmp_path / "reply_policy.toml"
    policy.write_text(
        "enabled = true\ngroup = true\nc2c = true\n"
        "speak_mode = \"all\"\ncontext_message_limit = 30\n"
        "group_unmentioned = true\nunmentioned_need_hook = false\n"
        "bot_names = [\"bot\"]\n",
        encoding="utf-8",
    )

    class _ChatLLM(_RecordingArchive):
        """Chat completer that records history passed to complete()."""

        def __init__(self) -> None:
            super().__init__()
            self.histories: list[list[dict[str, str]]] = []

        async def complete(
            self,
            history: list[dict[str, str]],
            user_text: str,
            **kwargs: object,
        ) -> str:
            """Record history and return a short reply."""
            self.histories.append(list(history))
            return "收到"

    class _Eyes:
        """Eyes stub that fails if read_context is used."""

        def read_latest(self, chat: str = "") -> SeenMessage | None:
            """No enrichment."""
            return None

        def read_context(self, chat: str = "", limit: int = 0) -> list[SeenMessage]:
            """Must not run when transcript already has enough lines."""
            raise AssertionError("read_context should be skipped")

    llm = _ChatLLM()
    db = tmp_path / "bot.sqlite3"
    transcript = TranscriptStore(db, 50)
    for i in range(5):
        transcript.append("白糖", text=f"旧{i}", sender="白糖", source="allow")
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        llm,  # type: ignore[arg-type]
        MemoryStore(db, 8),
        ReplyGate(policy),
        impressions,
        ImpressionWriter(llm, impressions),
        transcript=transcript,
    )
    sender = _SilentSender()
    bot.bind_sender(sender)  # type: ignore[arg-type]
    bot.bind_eyes(_Eyes())  # type: ignore[arg-type]
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="白糖",
                sender="",
                text="新消息",
                chat_type="friend",
            )
        )
    )
    assert sender.sent
    assert llm.histories
    assert any("旧0" in row["content"] for row in llm.histories[0])
    assert not any(row["content"] == "新消息" for row in llm.histories[0])
