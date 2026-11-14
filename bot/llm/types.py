"""Narrow LLM protocols so chat/archive can split or merge at the factory only."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChatCompleter(Protocol):
    """Reply-path model: persona-aware chat completion."""

    def is_configured(self) -> bool:
        """True when the reply model can be called."""
        ...

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
        """Generate one chat reply."""
        ...


@runtime_checkable
class ArchiveCompleter(Protocol):
    """Archive-path model: one-shot rewrites (impression / culture / evolution)."""

    def is_configured(self) -> bool:
        """True when the archive model can be called."""
        ...

    async def complete_plain(
        self, system: str, user_text: str, max_tokens: int = 400
    ) -> str:
        """One-shot completion without chat history or persona PromptBook."""
        ...


__all__ = ("ArchiveCompleter", "ChatCompleter")
