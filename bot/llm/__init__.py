"""OpenAI-compatible chat and archive completions."""

from bot.llm.archive import ArchiveLLM
from bot.llm.client import LLM_UNAVAILABLE_REPLY, LLMClient
from bot.llm.factory import build_archive_llm, build_chat_llm
from bot.llm.types import ArchiveCompleter, ChatCompleter

__all__ = (
    "ArchiveCompleter",
    "ArchiveLLM",
    "ChatCompleter",
    "LLMClient",
    "LLM_UNAVAILABLE_REPLY",
    "build_archive_llm",
    "build_chat_llm",
)
