"""Prompt evolution store, PromptBook inject, and message-count throttle."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.personas.catalog import PersonaCatalog
from bot.prompt_book import PromptBook
from bot.prompt_evolution.store import PromptEvolutionStore
from bot.prompt_evolution.writer import PromptEvolutionWriter


class _FakeLLM:
    """Return a canned JSON evolution proposal without calling the network."""

    def __init__(self, body: str = '{"proposed":"少客服腔，短句。","rationale":"群里嫌啰嗦"}') -> None:
        self.body = body
        self.calls = 0

    def is_configured(self) -> bool:
        """Pretend the API key is present."""
        return True

    async def complete_plain(self, system: str, user_text: str, max_tokens: int = 400) -> str:
        """Return a fixed JSON proposal and count calls."""
        self.calls += 1
        return self.body


def test_pending_approve_rejects_and_inject(tmp_path: Path) -> None:
    """pending → approve writes active; reject removes pending; PromptBook injects active only."""
    evo = PromptEvolutionStore(tmp_path / "prompt_evolution")
    pending = evo.add_pending(
        chat="测试群",
        proposed="本群用语更短。",
        rationale="嫌长",
        source_turn="甲: 你好\nbot: 哈",
    )
    assert evo.has_pending_for("测试群")
    assert evo.load_active("测试群")["prompt"] == ""

    rejected = evo.add_pending(
        chat="另一群",
        proposed="不应生效",
        rationale="测拒绝",
    )
    assert evo.reject_pending(rejected["id"]) is True
    assert evo.load_pending(rejected["id"]) is None

    active = evo.approve_pending(pending["id"])
    assert active is not None
    assert active["prompt"] == "本群用语更短。"
    assert evo.has_pending_for("测试群") is False
    assert evo.load_pending(pending["id"]) is None

    personas = tmp_path / "personas"
    pack = personas / "default"
    pack.mkdir(parents=True)
    (pack / "persona.txt").write_text("基础人设", encoding="utf-8")
    index = tmp_path / "personas.toml"
    index.write_text(
        'active = "default"\n\n[[persona]]\nid = "default"\ntitle = "t"\n',
        encoding="utf-8",
    )
    book = PromptBook(PersonaCatalog(index, personas))
    with_evo = book.system_text(prompt_evolution=active["prompt"])
    without = book.system_text()
    assert "本群进化提示词（已审批）" in with_evo
    assert "本群用语更短。" in with_evo
    assert "本群进化提示词（已审批）" not in without


def test_writer_respects_message_throttle(tmp_path: Path) -> None:
    """Writer skips while pending exists or received count is below threshold."""
    evo = PromptEvolutionStore(tmp_path / "prompt_evolution")
    llm = _FakeLLM()
    writer = PromptEvolutionWriter(llm, evo)  # type: ignore[arg-type]

    for _ in range(3):
        evo.note_received("测试群")
    first = asyncio.run(
        writer.after_turn(
            chat="测试群",
            user_text="测",
            assistant_text="回",
            min_messages=3,
        )
    )
    assert first is not None
    assert llm.calls == 1
    assert evo.has_pending_for("测试群")

    evo.note_received("测试群")
    second = asyncio.run(
        writer.after_turn(
            chat="测试群",
            user_text="再测",
            assistant_text="再回",
            min_messages=3,
        )
    )
    assert second is None
    assert llm.calls == 1

    evo.approve_pending(first["id"])
    # Still only +1 since last proposal; need 3 more received.
    third = asyncio.run(
        writer.after_turn(
            chat="测试群",
            user_text="又测",
            assistant_text="又回",
            min_messages=3,
        )
    )
    assert third is None
    assert llm.calls == 1

    evo.note_received("测试群")
    evo.note_received("测试群")
    fourth = asyncio.run(
        writer.after_turn(
            chat="测试群",
            user_text="条数够了",
            assistant_text="ok",
            min_messages=3,
        )
    )
    assert fourth is not None
    assert llm.calls == 2


def test_can_propose_zero_threshold(tmp_path: Path) -> None:
    """min_messages=0 allows propose whenever there is no pending."""
    evo = PromptEvolutionStore(tmp_path / "prompt_evolution")
    assert evo.can_propose("群", 0) is True
    evo.add_pending(chat="群", proposed="x", rationale="r")
    assert evo.can_propose("群", 0) is False


def test_disable_active(tmp_path: Path) -> None:
    """disable_active removes the overlay file."""
    evo = PromptEvolutionStore(tmp_path / "prompt_evolution")
    pending = evo.add_pending(chat="群A", proposed="叠加", rationale="r")
    evo.approve_pending(pending["id"])
    assert evo.load_active("群A")["prompt"] == "叠加"
    assert evo.disable_active("群A") is True
    assert evo.load_active("群A")["prompt"] == ""
