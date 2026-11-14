"""Sole place that decides whether chat and archive LLMs are split or shared."""

from __future__ import annotations

from bot.config import Settings
from bot.llm.archive import ArchiveLLM
from bot.llm.client import LLMClient
from bot.llm.types import ArchiveCompleter, ChatCompleter
from bot.prompt_book import PromptBook


def build_chat_llm(
    settings: Settings, prompts: PromptBook | None = None
) -> ChatCompleter:
    """Build the reply-path LLM (persona-aware)."""
    return LLMClient(settings, prompts=prompts)


def build_archive_llm(settings: Settings) -> ArchiveCompleter:
    """Build the archive-path LLM (no PromptBook).

    Merge later: return the same object as build_chat_llm when LLMClient
    satisfies ArchiveCompleter, or introduce ARCHIVE_LLM_MODE=shared here.
    """
    return ArchiveLLM(settings)
