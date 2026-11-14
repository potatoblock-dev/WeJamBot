"""LLMClient fail-closed behaviour when no API key is configured."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.config import Settings
from bot.llm.client import LLM_UNAVAILABLE_REPLY, LLMClient
from bot.personas.catalog import PersonaCatalog
from bot.prompt_book import PromptBook


def _settings(tmp_path: Path, api_key: str = "") -> Settings:
    """Build Settings pointing at temp data dirs with an optional API key."""
    data = tmp_path / "data"
    data.mkdir()
    personas = data / "personas"
    personas.mkdir()
    return Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key=api_key,
        llm_model="test-model",
        llm_timeout_seconds=5.0,
        archive_llm_base_url="https://example.invalid/v1",
        archive_llm_api_key=api_key,
        archive_llm_model="test-archive",
        memory_max_turns=8,
        data_dir=data,
        reply_policy_path=tmp_path / "reply_policy.toml",
        personas_dir=personas,
        personas_index_path=tmp_path / "personas.toml",
        allow_chats_path=data / "allow_chats.txt",
        observe_chats_path=data / "observe_chats.txt",
        bot_mode_path=data / "bot_mode.txt",
        wejam_target="127.0.0.1:7701",
        bot_name="bot",
        coalesce_burst_seconds=30.0,
        coalesce_debounce_seconds=3.0,
    )


def test_complete_without_api_key_does_not_echo_user(tmp_path: Path) -> None:
    """Missing LLM_API_KEY returns a fixed notice, never the inbound user text."""
    (tmp_path / "personas.toml").write_text('active = ""\n', encoding="utf-8")
    settings = _settings(tmp_path, api_key="")
    catalog = PersonaCatalog(settings.personas_index_path, settings.personas_dir)
    client = LLMClient(settings, prompts=PromptBook(catalog))
    inbound = "请把这句话原样发回给我"
    reply = asyncio.run(client.complete([], inbound))
    assert reply == LLM_UNAVAILABLE_REPLY
    assert reply != inbound
    assert inbound not in reply
