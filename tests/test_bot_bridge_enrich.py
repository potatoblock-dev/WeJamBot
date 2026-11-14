"""ChatBot bridge enrichment against fake WeChatEyes (Callus / openclaw paths)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

from bot.channel import IncomingTurn
from bot.chat import ChatBot
from bot.eyes import (
    Conversation,
    HandsRejected,
    LoginQr,
    LoginStatus,
    MediaAttachment,
    SeenMessage,
)
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.memory.store import MemoryStore
from bot.reply import TextSender
from bot.reply_policy import ReplyGate


class _QuietModel:
    """Stand-in model for enrichment tests."""

    def is_configured(self) -> bool:
        """Skip impression rewrite HTTP."""
        return False

    async def complete(self, *args: object, **kwargs: object) -> str:
        """Fixed reply."""
        return "在的"


class _BridgeEyes:
    """Fake eyes with Callus-style read_latest / read_context / capture."""

    def __init__(
        self,
        *,
        latest_content_type: str = "text",
        latest_chat_type: str = "friend",
        latest_sender: str = "甲",
    ) -> None:
        self.sent: list[tuple[str, str]] = []
        self.context_calls = 0
        self.latest_calls = 0
        self.capture_calls = 0
        self.latest_content_type = latest_content_type
        self.latest_chat_type = latest_chat_type
        self.latest_sender = latest_sender

    def list_conversations(self) -> list[Conversation]:
        return []

    def subscribe(self, chat: str = "") -> Iterator[SeenMessage]:
        return iter(())

    def send_text(self, chat: str, text: str) -> None:
        self.sent.append((chat, text))

    def login_status(self) -> LoginStatus:
        return LoginStatus(phase="logged_in")

    def qr_code(self) -> LoginQr:
        return LoginQr(payload="")

    def close(self) -> None:
        return None

    def read_latest(self, chat: str = "") -> SeenMessage | None:
        """Return a nickname-enriched latest bubble."""
        self.latest_calls += 1
        text = "在吗" if self.latest_content_type == "text" else ""
        return SeenMessage(
            chat=chat or "文件传输助手",
            text=text,
            sender=self.latest_sender,
            chat_type=self.latest_chat_type,
            content_type=self.latest_content_type,
            index=7,
            message_id="m1",
        )

    def read_context(self, chat: str = "", limit: int = 0) -> list[SeenMessage]:
        """Return two prior lines as scrolled context."""
        self.context_calls += 1
        return [
            SeenMessage(chat=chat, text="早", sender="甲", from_self=False),
            SeenMessage(chat=chat, text="嗯", sender="", from_self=True),
        ]

    def capture_message_media(
        self, chat: str, index: int, max_frames: int = 0, interval_ms: int = 0
    ):
        """Openclaw-style unsupported capture."""
        self.capture_calls += 1
        raise HandsRejected("openclaw bridge: CaptureMessageMedia unsupported")


def test_enrich_uses_bridge_context_as_history(tmp_path: Path) -> None:
    """When read_context works, LLM history comes from the bridge scroll window."""
    eyes = _BridgeEyes()
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    memory = MemoryStore(tmp_path / "bot.sqlite3", 8)
    memory.append("文件传输助手", "user", "旧记忆应被桥上下文替换")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        memory,
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    bot.bind_eyes(eyes)
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="文件传输助手",
                sender="",
                text="在吗",
                chat_type="friend",
            )
        )
    )
    assert eyes.latest_calls == 1
    assert eyes.context_calls == 1
    assert eyes.sent == [("文件传输助手", "在的")]


def test_enrich_tolerates_openclaw_unsupported_capture(tmp_path: Path) -> None:
    """Emotion turn still replies with text when capture is unsupported."""
    eyes = _BridgeEyes(latest_content_type="emotion")
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    bot.bind_eyes(eyes)
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="文件传输助手",
                sender="",
                text="[表情]",
                chat_type="friend",
                content_type="emotion",
                index=3,
            )
        )
    )
    assert eyes.capture_calls == 1
    assert eyes.sent == [("文件传输助手", "在的")]


def test_enrich_skips_capture_when_bridge_attachments_present(tmp_path: Path) -> None:
    """OpenClaw CDN paths on the turn must not trigger GUI CaptureMessageMedia."""
    eyes = _BridgeEyes()
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    bot.bind_eyes(eyes)
    sticker = tmp_path / "sticker.gif"
    sticker.write_bytes(b"GIF89a")
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="文件传输助手",
                sender="",
                text="[图片]",
                chat_type="friend",
                content_type="emotion",
                index=3,
                image_paths=(str(sticker),),
            )
        )
    )
    assert eyes.capture_calls == 0
    assert eyes.sent == [("文件传输助手", "在的")]


class _CaptureEyes(_BridgeEyes):
    """Bridge eyes whose capture returns a local sticker path."""

    def __init__(self, sticker: Path) -> None:
        super().__init__(latest_content_type="Emotion", latest_chat_type="friend")
        self._sticker = sticker

    def capture_message_media(
        self, chat: str, index: int, max_frames: int = 0, interval_ms: int = 0
    ):
        """Return one GIF path (GUI Callus success path)."""
        self.capture_calls += 1
        return [
            MediaAttachment(kind="emotion", path=str(self._sticker), frame_index=0)
        ]


def test_empty_emotion_reaches_capture_after_decide(tmp_path: Path) -> None:
    """Empty emotion must pass decide, then GUI capture can attach a path."""
    sticker = tmp_path / "emo.gif"
    sticker.write_bytes(b"GIF89a")
    eyes = _CaptureEyes(sticker)
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    bot.bind_eyes(eyes)
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="文件传输助手",
                sender="",
                text="",
                chat_type="friend",
                content_type="emotion",
                index=3,
            )
        )
    )
    assert eyes.latest_calls == 1
    assert eyes.capture_calls == 1
    assert eyes.sent == [("文件传输助手", "在的")]


def test_identity_enrich_applies_group_chat_type(tmp_path: Path) -> None:
    """ReadLatest chat_type=group must make is_group true before decide."""
    eyes = _BridgeEyes(latest_chat_type="group", latest_sender="甲")
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_eyes(eyes)
    inbound = IncomingTurn(
        chat="1145",
        sender="",
        text="大家好",
        chat_type="",
    )
    enriched = bot._enrich_identity(inbound)
    assert enriched.chat_type == "group"
    assert enriched.sender == "甲"
    assert enriched.is_group is True
    assert enriched.session_id == "1145:甲"


def test_decide_uses_identity_before_context(tmp_path: Path) -> None:
    """group=false skips after identity marks the chat as a group; no scroll."""
    path = tmp_path / "reply_policy.toml"
    path.write_text("group = false\n", encoding="utf-8")
    eyes = _BridgeEyes(latest_chat_type="group", latest_sender="甲")
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(path),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    bot.bind_eyes(eyes)
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="1145",
                sender="",
                text="大家好",
                chat_type="",
            )
        )
    )
    assert eyes.latest_calls == 1
    assert eyes.context_calls == 0
    assert eyes.sent == []


def test_apply_latest_ignores_newer_different_msg_id(tmp_path: Path) -> None:
    """ReadLatest for a different bubble must not wipe emotion/index on the watched turn."""
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        _QuietModel(),  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(_QuietModel(), impressions),  # type: ignore[arg-type]
    )
    inbound = IncomingTurn(
        chat="1145",
        sender="甲",
        text="",
        chat_type="group",
        msg_id="emo-1",
        content_type="emotion",
        index=3,
    )
    newer = SeenMessage(
        chat="1145",
        text="下一句",
        sender="乙",
        chat_type="group",
        message_id="text-9",
        content_type="text",
        index=9,
    )
    out = bot._apply_latest(inbound, newer)
    assert out.content_type == "emotion"
    assert out.index == 3
    assert out.sender == "甲"
    assert out.msg_id == "emo-1"


def test_empty_emotion_failed_capture_abandons_rate_limit(tmp_path: Path) -> None:
    """Capture failure leaving an empty body must not burn min_interval."""
    path = tmp_path / "reply_policy.toml"
    path.write_text("min_interval_seconds = 60\n", encoding="utf-8")
    eyes = _BridgeEyes(latest_content_type="Emotion", latest_sender="")
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    gate = ReplyGate(path)
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        gate,
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    bot.bind_eyes(eyes)
    asyncio.run(
        bot.handle(
            IncomingTurn(
                chat="文件传输助手",
                sender="",
                text="",
                chat_type="friend",
                content_type="emotion",
                index=3,
            )
        )
    )
    assert eyes.sent == []
    assert eyes.capture_calls == 1
    assert gate.decide(
        IncomingTurn(
            chat="文件传输助手",
            sender="",
            text="还在吗",
            chat_type="friend",
        ),
        now=1.0,
    ) is None


def test_single_chat_type_is_private(tmp_path: Path) -> None:
    """Bridge 'single' must not be treated as a group via sender heuristic."""
    eyes = _BridgeEyes(latest_chat_type="single", latest_sender="甲")
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(tmp_path / "bot.sqlite3", 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_eyes(eyes)
    enriched = bot._enrich_identity(
        IncomingTurn(chat="文件传输助手", sender="", text="hi", chat_type="")
    )
    assert enriched.chat_type == "friend"
    assert enriched.is_group is False
