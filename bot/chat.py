"""Orchestrate inbound WeChat turns: memory, LLM, text reply."""

from __future__ import annotations

import logging
import time

from bot.channel import IncomingTurn
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.llm.client import LLMClient
from bot.memory.store import MemoryStore
from bot.reply import TextSender, strip_sticker_markers
from bot.reply_policy import ReplyGate, is_addressed

_log = logging.getLogger(__name__)


class ChatBot:
    """Handle one inbound WeChat turn end-to-end."""

    def __init__(
        self,
        llm: LLMClient,
        memory: MemoryStore,
        gate: ReplyGate,
        impressions: ImpressionStore,
        impression_writer: ImpressionWriter,
        sender: TextSender | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._gate = gate
        self._impressions = impressions
        self._impression_writer = impression_writer
        self._sender = sender

    def bind_sender(self, sender: TextSender) -> None:
        """Attach the live wejam send wrapper for this connection."""
        self._sender = sender

    async def handle(self, message: IncomingTurn) -> None:
        """Run policy, LLM, send, then refresh impression."""
        _log.info(
            "inbound group=%s session=%s user=%s preview=%s text=%r",
            message.is_group,
            message.session_id,
            message.username,
            message.preview_only,
            message.user_text[:80],
        )
        self._gate.note_inbound_engagement(message)
        skip = self._gate.decide(message)
        if skip:
            _log.info("skip reply reason=%s text=%r", skip, message.user_text[:80])
            return
        user_text = message.user_text.strip()
        if not user_text:
            return
        if message.is_group:
            user_text = f"[群友] {message.username}\n{user_text}"
        history = self._memory.history(message.session_id)
        profile = self._impressions.touch(message.impression_key, username=message.username)
        speaker = str(profile.get("username") or message.username)
        directory = self._impressions.directory_block(speaker_name=speaker)
        others = self._impressions.others_block(exclude_key=message.impression_key)
        reply = await self._llm.complete(
            history,
            user_text,
            impression=str(profile.get("impression") or ""),
            directory=directory,
            others=others,
        )
        reply = strip_sticker_markers(reply)
        if not reply.strip():
            _log.warning("empty llm reply session=%s", message.session_id)
            return
        self._memory.append(message.session_id, "user", user_text)
        self._memory.append(message.session_id, "assistant", reply)
        if self._sender is None:
            _log.warning("no sender bound; skipping send session=%s", message.session_id)
            return
        delivered = self._sender.send_bubbles(message.chat, reply)
        if not delivered:
            _log.warning("reply not delivered session=%s", message.session_id)
            return
        now = time.time()
        self._gate.note_bot_reply(message.session_id, now)
        policy = self._gate.current()
        if message.is_group and not is_addressed(message, policy.bot_names):
            self._gate.note_unmentioned_reply(message.group_id, now)
        try:
            await self._impression_writer.after_turn(
                user_key=message.impression_key,
                username=message.username,
                user_text=user_text,
                assistant_text=reply,
            )
        except Exception:
            _log.exception("impression after_turn failed key=%s", message.impression_key)
