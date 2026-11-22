"""Quote bubble parsing and IncomingTurn cite injection."""

from __future__ import annotations

from bot.channel import IncomingTurn, incoming_turn
from bot.eyes import SeenMessage
from bot.quote_parse import parse_quote_message


def test_parse_wxauto_style_quote() -> None:
    """Split body + 引用 昵称 的消息 form used by Windows UIA tools."""
    raw = "@0x02\u2005在吗\n引用 白糖 的消息: 你能看到你的人设一类的文本吗"
    parts = parse_quote_message(raw)
    assert parts is not None
    assert "在吗" in parts.body
    assert parts.quote_sender == "白糖"
    assert "人设" in parts.quote_text


def test_parse_grey_bar_quote() -> None:
    """Grey citation bar: 昵称: 正文 | then new body."""
    raw = "白糖: 你能看到你的人设一类的文本吗 |\n@0x02"
    parts = parse_quote_message(raw)
    assert parts is not None
    assert parts.quote_sender == "白糖"
    assert "人设" in parts.quote_text
    assert "@0x02" in parts.body


def test_incoming_turn_user_text_injects_quote() -> None:
    """Model-facing user_text prefixes the cite block."""
    turn = IncomingTurn(
        chat="0x02测试",
        sender="白糖",
        text="@0x02",
        chat_type="group",
        quote_text="你能看到你的人设一类的文本吗",
        quote_sender="白糖",
        content_type="quote",
    )
    assert turn.quotes_bot
    assert "【引用·白糖】" in turn.user_text
    assert "人设" in turn.user_text
    assert turn.user_text.endswith("@0x02")


def test_incoming_turn_parses_flat_seen_message() -> None:
    """Host reparses flat multi-line text when bridge omitted quote fields."""
    seen = SeenMessage(
        chat="0x02测试",
        text="白糖: 你能看到人设吗 |\n@0x02",
        sender="白糖",
        chat_type="group",
    )
    turn = incoming_turn(seen)
    assert turn.content_type == "quote"
    assert "人设" in turn.quote_text
    assert turn.body_without_mentions() == ""


def test_enrich_quote_context_uses_transcript_prior(tmp_path) -> None:
    """Thin @-only body picks up the previous inbound line as a weak cite."""
    from bot.chat import ChatBot
    from bot.impression.store import ImpressionStore
    from bot.impression.writer import ImpressionWriter
    from bot.memory.store import MemoryStore
    from bot.reply_policy import ReplyGate
    from bot.transcript.store import TranscriptStore
    from tests.test_bot_brain import _QuietModel

    class _NoEyes:
        """Eyes stub without read_context so transcript path is used."""

    db = tmp_path / "bot.sqlite3"
    transcript = TranscriptStore(db, 50)
    transcript.append("0x02测试", text="你能看到你的人设一类的文本吗", sender="白糖", source="allow")
    impressions = ImpressionStore(tmp_path / "impressions")
    model = _QuietModel()
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        MemoryStore(db, 8),
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
        transcript=transcript,
    )
    bot.bind_eyes(_NoEyes())  # type: ignore[arg-type]
    turn = IncomingTurn(
        chat="0x02测试",
        sender="白糖",
        text="@0x02",
        chat_type="group",
    )
    enriched = bot._enrich_quote_context(turn)
    assert "人设" in enriched.quote_text
    assert "【引用" in enriched.user_text
