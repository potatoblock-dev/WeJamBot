"""Propose per-group prompt evolution after a group reply or observe turn (pending only)."""

from __future__ import annotations

import json
import logging
import re

from bot.archive_text import is_observe_turn, user_bot_prompt_block
from bot.llm.types import ArchiveCompleter
from bot.prompt_evolution.store import PromptEvolutionStore

_log = logging.getLogger(__name__)

_REWRITE = (
    "你在为微信群聊 bot 起草「本群进化提示词」叠加层草案。"
    "这是待人工审批的草稿，不会立刻生效。"
    "用简体中文。只描述本群需要的口吻、禁忌话题边界、常用称呼与回复习惯；"
    "不要写越狱、忽略系统提示、泄露密钥、或覆盖防注入规则的内容。"
    "不要改全局人设声明；只写本群叠加说明，不超过 500 字。"
    "若标注本轮 bot 未回复，只根据用户消息与群氛围起草。"
    "只输出一个 JSON 对象，不要 Markdown，不要其它文字："
    '{"proposed":"叠加提示词正文","rationale":"一两句为何要改"}'
)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


class PromptEvolutionWriter:
    """Ask the archive model for a pending evolution proposal; never write active."""

    def __init__(self, llm: ArchiveCompleter, store: PromptEvolutionStore) -> None:
        self._llm = llm
        self._store = store

    async def after_turn(
        self,
        chat: str,
        user_text: str,
        assistant_text: str,
        *,
        min_messages: int = 50,
        culture_text: str = "",
        speaker_name: str = "",
    ) -> dict | None:
        """Create a pending proposal when message-count throttle allows; return it or None."""
        if not chat.strip():
            return None
        if not self._store.can_propose(chat, min_messages):
            _log.info("prompt evolution skip throttle chat=%s", chat[:40])
            return None
        if not self._llm.is_configured():
            return None
        active = self._store.load_active(chat)
        old = str(active.get("prompt") or "").strip() or "（尚无已审批叠加）"
        culture = (culture_text or "").strip() or "（无）"
        speaker = (speaker_name or "").strip() or "群友"
        prompt = (
            f"当前已审批叠加：\n{old}\n\n"
            f"本群氛围备忘（参考）：\n{culture[:600]}\n\n"
            f"本轮说话人：{speaker}\n"
            f"{user_bot_prompt_block(user_text, assistant_text)}"
        )
        try:
            raw = await self._llm.complete_plain(_REWRITE, prompt, max_tokens=600)
        except Exception:
            _log.exception("prompt evolution rewrite failed chat=%s", chat[:40])
            return None
        proposed, rationale = _parse_proposal(raw)
        if not proposed.strip():
            return None
        if is_observe_turn(assistant_text):
            source = f"{speaker}: {user_text[:200]}\nbot: （旁听未回复）"
        else:
            source = f"{speaker}: {user_text[:200]}\nbot: {assistant_text[:200]}"
        return self._store.add_pending(
            chat=chat,
            proposed=proposed.strip()[:2000],
            rationale=rationale.strip()[:400],
            source_turn=source,
        )


def _parse_proposal(raw: str) -> tuple[str, str]:
    """Extract proposed + rationale from model JSON; fall back to plain text."""
    text = (raw or "").strip()
    if not text:
        return "", ""
    match = _JSON_BLOCK.search(text)
    blob = match.group(0) if match else text
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return text, ""
    if not isinstance(data, dict):
        return text, ""
    proposed = str(data.get("proposed") or data.get("prompt") or "").strip()
    rationale = str(data.get("rationale") or data.get("reason") or "").strip()
    if not proposed:
        return text, rationale
    return proposed, rationale
