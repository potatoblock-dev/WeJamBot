"""Archive LLM: one-shot rewrites with its own HTTP slot (no PromptBook)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import httpx

from bot.config import Settings

_log = logging.getLogger(__name__)


class ArchiveLLM:
    """Call an OpenAI-compatible endpoint for archive/profile rewrites only."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._slot = threading.Lock()

    def is_configured(self) -> bool:
        """True when an archive (or fallen-back chat) API key is present."""
        return bool(self._settings.archive_llm_api_key)

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
        """Serialize archive LLM HTTP calls on a dedicated lock."""
        deadline = self._settings.llm_timeout_seconds + 10.0
        wait_start = time.monotonic()
        await asyncio.to_thread(self._slot.acquire)
        try:
            waited = time.monotonic() - wait_start
            if waited >= 0.3:
                _log.info("archive llm 排队等待 slot %.1fs label=%s", waited, label)
            api_start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._http_timeout()) as client:
                    response = await asyncio.wait_for(
                        client.post(url, headers=headers, json=payload),
                        timeout=deadline,
                    )
                    response.raise_for_status()
                    data = response.json()
            except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
                _log.warning(
                    "archive llm timeout label=%s elapsed=%.1fs err=%s",
                    label,
                    time.monotonic() - api_start,
                    type(exc).__name__,
                )
                raise
            if not isinstance(data, dict):
                raise RuntimeError("Archive LLM response is not a JSON object")
            elapsed = time.monotonic() - api_start
            _log.info(
                "archive llm finished label=%s elapsed=%.1fs", label, elapsed
            )
            return data
        finally:
            self._slot.release()

    async def complete_plain(
        self, system: str, user_text: str, max_tokens: int = 400
    ) -> str:
        """One-shot archive completion; empty string when unconfigured or failed."""
        if not self.is_configured():
            return ""
        url = f"{self._settings.archive_llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.archive_llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.archive_llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        try:
            _log.info(
                "archive llm start model=%s label=plain",
                self._settings.archive_llm_model,
            )
            data = await self._call_api(url, headers, payload, label="plain")
            return str(data["choices"][0]["message"]["content"]).strip()
        except (asyncio.TimeoutError, httpx.TimeoutException):
            # Already warned in _call_api; keep observe path quiet.
            return ""
        except Exception:
            _log.exception("archive llm complete_plain failed")
            return ""
