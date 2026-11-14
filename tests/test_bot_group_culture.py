"""Group culture store and writer (mock LLM)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.group_culture.store import GroupCultureStore
from bot.group_culture.writer import GroupCultureWriter


class _FakeLLM:
    """Return a canned culture rewrite without calling the network."""

    def is_configured(self) -> bool:
        """Pretend the API key is present."""
        return True

    async def complete_plain(self, system: str, user_text: str, max_tokens: int = 400) -> str:
        """Return a short culture note for the store to save."""
        return "群里爱说名单有正文空，语气偏直接。"


def test_group_culture_writer_saves(tmp_path: Path) -> None:
    """after_turn writes culture JSON for the chat title."""
    culture_store = GroupCultureStore(tmp_path / "group_culture")
    writer = GroupCultureWriter(_FakeLLM(), culture_store)  # type: ignore[arg-type]
    asyncio.run(
        writer.after_turn(
            chat="测试群",
            user_text="名单有正文空",
            assistant_text="又来了",
            speaker_name="甲",
        )
    )
    record = culture_store.load("测试群")
    assert "名单有正文空" in record["culture"]
    assert culture_store.path_for("测试群").is_file()


def test_clear_culture_keeps_chat(tmp_path: Path) -> None:
    """clear_culture wipes body but keeps the file and label."""
    culture_store = GroupCultureStore(tmp_path / "group_culture")
    culture_store.touch("测试群", label="备注")
    culture_store.save(
        {"chat": "测试群", "label": "备注", "culture": "旧内容", "updated_at": ""}
    )
    culture_store.clear_culture("测试群")
    record = culture_store.load("测试群")
    assert record["culture"] == ""
    assert record["label"] == "备注"
