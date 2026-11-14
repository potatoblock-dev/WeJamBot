"""OpenAI-compatible chat completions client (text + optional images)."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from bot.config import Settings
from bot.personas.catalog import PersonaCatalog
from bot.prompt_book import PromptBook

_log = logging.getLogger(__name__)

# Fixed system notice when the API key is missing or the chat call fails — never echo user text.
# Leading "# [系统]" marks non-persona output so operators can tell faults from roleplay.
LLM_UNAVAILABLE_REPLY = "# [系统] 语言模型当前不可用，本次未能生成回复。"


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

    def _user_content(
        self, text_body: str, image_paths: list[str] | tuple[str, ...] = ()
    ) -> str | list[dict[str, Any]]:
        """Build user message content; images go only on the user turn as data URLs."""
        paths = [p for p in image_paths if p]
        if not paths:
            return text_body
        parts: list[dict[str, Any]] = [{"type": "text", "text": text_body}]
        for path in paths:
            data_url = _image_data_url(path)
            if not data_url:
                continue
            parts.append(
                {"type": "image_url", "image_url": {"url": data_url, "detail": "low"}}
            )
        return parts if len(parts) > 1 else text_body

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
        """Generate a text reply for one chat turn; optional local images for vision."""
        text_body = user_text.strip()
        if not self.is_configured():
            _log.warning("llm complete skipped: LLM_API_KEY not configured")
            return LLM_UNAVAILABLE_REPLY
        if not text_body and not image_paths:
            return "嗯。"
        if not text_body:
            text_body = "（用户发了一张图/表情）"
        system = self._prompts.system_text(
            impression,
            directory=directory,
            others=others,
            group_culture=group_culture,
            knowledge=knowledge,
            prompt_evolution=prompt_evolution,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append(
            {"role": "user", "content": self._user_content(text_body, image_paths)}
        )
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
                "llm request start model=%s messages=%s images=%s",
                self._settings.llm_model,
                len(messages),
                len(tuple(image_paths or ())),
            )
            data = await self._call_api(url, headers, payload, label="chat")
            text = str(data["choices"][0]["message"]["content"]).strip()
            return text or "嗯。"
        except Exception:
            _log.exception("llm complete failed")
            return LLM_UNAVAILABLE_REPLY

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


def _image_data_url(path: str) -> str:
    """Encode a local image file as a data URL for DeepSeek vision."""
    file_path = Path(path)
    if not file_path.is_file():
        _log.warning("llm image missing path=%s", path)
        return ""
    mime, _ = mimetypes.guess_type(file_path.name)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    try:
        raw = file_path.read_bytes()
    except OSError:
        _log.exception("llm image read failed path=%s", path)
        return ""
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"
