"""OpenAI-compatible chat completions client (text only for WeJam v1)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import httpx

from bot.config import Settings
from bot.personas.catalog import PersonaCatalog
from bot.prompt_book import PromptBook

_log = logging.getLogger(__name__)


class LLMClient:
    """Call an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, settings: Settings, prompts: PromptBook | None = None) -> None:
        self._settings = settings
        if prompts is not None:
            self._prompts = prompts
        else:
            catalog = PersonaCatalog(
                settings.personas_index_path,
                settings.personas_dir,
            )
            self._prompts = PromptBook(
                catalog,
                policy_path=settings.reply_policy_path,
            )
        self._slot = threading.Lock()

    def is_configured(self) -> bool:
        """True when an API key is present so we can call a model."""
        return bool(self._settings.llm_api_key)

    def _http_timeout(self) -> httpx.Timeout:
        """Split connect/read limits so a hung socket cannot block forever."""
        total = max(5.0, self._settings.llm_timeout_seconds)
        connect = min(10.0, total)
        return httpx.Timeout(total, connect=connect, read=total, write=connect, pool=connect)

    async def _call_api(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        label: str,
    ) -> dict[str, Any]:
        """Serialize LLM HTTP calls across worker threads with a threading lock."""
        deadline = self._settings.llm_timeout_seconds + 10.0
        wait_start = time.monotonic()
        await asyncio.to_thread(self._slot.acquire)
        try:
            waited = time.monotonic() - wait_start
            if waited >= 0.3:
                _log.info("llm 排队等待 slot %.1fs label=%s", waited, label)
            api_start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._http_timeout()) as client:
                    response = await asyncio.wait_for(
                        client.post(url, headers=headers, json=payload),
                        timeout=deadline,
                    )
                    response.raise_for_status()
                    data = response.json()
            except asyncio.TimeoutError:
                _log.error(
                    "llm request timeout label=%s elapsed=%.1fs",
                    label,
                    time.monotonic() - api_start,
                )
                raise
            if not isinstance(data, dict):
                raise RuntimeError("LLM response is not a JSON object")
            elapsed = time.monotonic() - api_start
            _log.info("llm request finished label=%s elapsed=%.1fs", label, elapsed)
            return data
        finally:
            self._slot.release()

    async def complete(
        self,
        history: list[dict[str, str]],
        user_text: str,
        impression: str = "",
        directory: str = "",
        others: str = "",
    ) -> str:
        """Generate a text reply for one chat turn."""
        text_body = user_text.strip()
        if not self.is_configured():
            return text_body or "收到。"
        if not text_body:
            return "嗯。"
        system = self._prompts.system_text(
            impression,
            directory=directory,
            others=others,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": text_body})
        url = f"{self._settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 400,
            "thinking": {"type": "disabled"},
        }
        try:
            _log.info(
                "llm request start model=%s messages=%s",
                self._settings.llm_model,
                len(messages),
            )
            data = await self._call_api(url, headers, payload, label="chat")
            text = str(data["choices"][0]["message"]["content"]).strip()
            return text or "嗯。"
        except Exception:
            _log.exception("llm complete failed")
            return "这会儿没接上模型，稍后再试。"

    async def complete_plain(self, system: str, user_text: str, max_tokens: int = 400) -> str:
        """One-shot completion without chat history (used to rewrite impressions)."""
        if not self.is_configured():
            return ""
        url = f"{self._settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        data = await self._call_api(url, headers, payload, label="impression")
        return str(data["choices"][0]["message"]["content"]).strip()
