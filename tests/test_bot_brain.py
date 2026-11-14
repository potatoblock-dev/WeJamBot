"""Brain loop against a fake WeChatEyes. No container and no wejam import."""

from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from bot.channel import IncomingTurn
from bot.chat import ChatBot
from bot.coalesce import TurnCoalescer
from bot.eyes import (
    Conversation,
    HandsRejected,
    LoginQr,
    LoginStatus,
    SeenMessage,
    WeChatEyes,
)
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.llm.client import LLM_UNAVAILABLE_REPLY
from bot.memory.store import MemoryStore
from bot.reply import TextSender
from bot.reply_policy import ReplyGate
from bot.runner import consume_messages

_ROOT = Path(__file__).resolve().parents[1]
_BRAIN_SOURCES = (
    "bot/eyes.py",
    "bot/chat.py",
    "bot/channel.py",
    "bot/reply.py",
    "bot/coalesce.py",
    "bot/reply_policy.py",
    "bot/runner.py",
    "tests/test_bot_brain.py",
)
_FORBIDDEN = ("wejam", "grpc", "google.protobuf")


class _QuietModel:
    """Stand-in chat model. is_configured is false so impressions are not rewritten."""

    def is_configured(self) -> bool:
        """Skip the impression rewrite HTTP call."""
        return False

    async def complete(
        self,
        history: list[dict[str, str]],
        user_text: str,
        impression: str = "",
        directory: str = "",
        others: str = "",
        group_culture: str = "",
        knowledge: str = "",
        prompt_evolution: str = "",
        image_paths: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Return a fixed reply so the test does not call a network model."""
        return "在的"


class FakeEyes:
    """WeChatEyes that replays canned lines and records sends."""

    def __init__(self, messages: list[SeenMessage]) -> None:
        """Store the lines subscribe() will yield."""
        self._messages = list(messages)
        self.sent: list[tuple[str, str]] = []

    def list_conversations(self) -> list[Conversation]:
        """No session list in this test."""
        return []

    def subscribe(self, chat: str = "") -> Iterator[SeenMessage]:
        """Yield the canned lines once, then stop."""
        return iter(self._messages)

    def send_text(self, chat: str, text: str) -> None:
        """Record one bubble."""
        self.sent.append((chat, text))

    def login_status(self) -> LoginStatus:
        """Unused by the chat loop."""
        return LoginStatus(phase="logged_in")

    def qr_code(self) -> LoginQr:
        """Unused by the chat loop."""
        return LoginQr(payload="")

    def close(self) -> None:
        """Nothing to release."""
        return None


def _imported_modules(path: Path) -> list[str]:
    """Collect module names from import statements in one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_brain_sources_do_not_import_driver() -> None:
    """Chat, policy, and the eyes contract do not import protobuf or wejam."""
    for rel in _BRAIN_SOURCES:
        modules = _imported_modules(_ROOT / rel)
        for name in modules:
            assert not name.startswith(_FORBIDDEN), f"{rel} imports {name}"
            assert "protobuf" not in name, f"{rel} imports {name}"
            if rel != "bot/runner.py":
                assert name != "bot.hands", f"{rel} imports the WeJam adapter"


def test_importing_brain_does_not_load_driver() -> None:
    """A fresh interpreter that imports the brain does not load wejam or grpc."""
    script = (
        "import bot.chat, bot.channel, bot.eyes, bot.reply, bot.runner, bot.coalesce\n"
        "import sys\n"
        "bad = [k for k in sys.modules if k == 'wejam' or k.startswith('wejam.') "
        "or k == 'grpc' or 'protobuf' in k]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_brain_replies_through_fake_eyes(tmp_path: Path) -> None:
    """A private line becomes one send. Own messages are ignored. No container."""
    eyes = FakeEyes(
        [
            SeenMessage(chat="文件传输助手", text="在吗", preview=True),
            SeenMessage(chat="文件传输助手", text="自己说的", from_self=True, preview=True),
        ]
    )
    assert isinstance(eyes, WeChatEyes)
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    memory = MemoryStore(tmp_path / "bot.sqlite3", 8)
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        memory,
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
    coalescer = TurnCoalescer(
        lambda turn: asyncio.run(bot.handle(turn)),
        burst_window=30.0,
        debounce=30.0,
    )
    consume_messages(
        eyes,
        coalescer.submit,
        allowed={"文件传输助手"},
        mode="reply",
    )
    coalescer.flush_pending()
    assert eyes.sent == [("文件传输助手", "在的")]
    history = memory.history("文件传输助手")
    assert history == [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在的"},
    ]


def test_brain_skips_memory_when_send_fails(tmp_path: Path) -> None:
    """Failed delivery must not append user/assistant turns to SQLite."""

    class _RejectEyes(FakeEyes):
        """Fake eyes that always reject send_text."""

        def send_text(self, chat: str, text: str) -> None:
            raise HandsRejected("busy")

    eyes = _RejectEyes([SeenMessage(chat="文件传输助手", text="在吗", preview=True)])
    model = _QuietModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    memory = MemoryStore(tmp_path / "bot.sqlite3", 8)
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        memory,
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
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
    assert memory.history("文件传输助手") == []


class _UnavailableModel(_QuietModel):
    """Model that always returns the fixed LLM-unavailable fallback copy."""

    async def complete(
        self,
        history: list[dict[str, str]],
        user_text: str,
        impression: str = "",
        directory: str = "",
        others: str = "",
        group_culture: str = "",
        knowledge: str = "",
        prompt_evolution: str = "",
        image_paths: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Return the same notice LLMClient uses when the API key is missing."""
        return LLM_UNAVAILABLE_REPLY


def test_brain_skips_memory_on_llm_unavailable_fallback(tmp_path: Path) -> None:
    """Fallback notice is sent, but must not be stored as chat history."""
    eyes = FakeEyes([])
    model = _UnavailableModel()
    impressions = ImpressionStore(tmp_path / "impressions")
    memory = MemoryStore(tmp_path / "bot.sqlite3", 8)
    bot = ChatBot(
        model,  # type: ignore[arg-type]
        memory,
        ReplyGate(tmp_path / "missing.toml"),
        impressions,
        ImpressionWriter(model, impressions),  # type: ignore[arg-type]
    )
    bot.bind_sender(TextSender(eyes))
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
    assert eyes.sent == [("文件传输助手", LLM_UNAVAILABLE_REPLY)]
    assert memory.history("文件传输助手") == []
