"""Orchestrate inbound WeChat turns: memory, LLM, text reply."""

from __future__ import annotations

import logging
import time

from bot.archive_text import OBSERVE_NO_REPLY
from bot.channel import IncomingTurn
from bot.eyes import HandsError, SeenMessage, WeChatEyes
from bot.group_culture.store import GroupCultureStore
from bot.group_culture.writer import GroupCultureWriter
from bot.identity.directory import IdentityDirectory
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.writer import KnowledgeWriter
from bot.llm.client import LLM_UNAVAILABLE_REPLY
from bot.llm.types import ChatCompleter
from bot.memory.store import MemoryStore
from bot.prompt_evolution.store import PromptEvolutionStore
from bot.prompt_evolution.writer import PromptEvolutionWriter
from bot.reply import TextSender, strip_sticker_markers
from bot.reply_policy import ReplyGate, is_addressed
from bot.transcript.focus import apply_recent_focus
from bot.transcript.store import TranscriptRow, TranscriptStore

_log = logging.getLogger(__name__)
_DIGEST_USER_LIMIT = 3500


def _normalize_chat_type(raw: str) -> str:
    """Map bridge chat_type onto IncomingTurn values (single → friend)."""
    kind = (raw or "").strip().lower()
    if kind in {"single", "private", "c2c", "friend"}:
        return "friend"
    if kind in {"group", "chatroom"}:
        return "group"
    if kind in {"official", "service"}:
        return kind
    return (raw or "").strip()


class ChatBot:
    """Handle one inbound WeChat turn end-to-end."""

    def __init__(
        self,
        llm: ChatCompleter,
        memory: MemoryStore,
        gate: ReplyGate,
        impressions: ImpressionStore,
        impression_writer: ImpressionWriter,
        sender: TextSender | None = None,
        *,
        group_culture: GroupCultureStore | None = None,
        group_culture_writer: GroupCultureWriter | None = None,
        knowledge: KnowledgeStore | None = None,
        knowledge_writer: KnowledgeWriter | None = None,
        prompt_evolution: PromptEvolutionStore | None = None,
        prompt_evolution_writer: PromptEvolutionWriter | None = None,
        transcript: TranscriptStore | None = None,
        identity: IdentityDirectory | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._gate = gate
        self._impressions = impressions
        self._impression_writer = impression_writer
        self._sender = sender
        self._eyes: WeChatEyes | None = None
        self._group_culture = group_culture
        self._group_culture_writer = group_culture_writer
        self._knowledge = knowledge
        self._knowledge_writer = knowledge_writer
        self._prompt_evolution = prompt_evolution
        self._prompt_evolution_writer = prompt_evolution_writer
        self._transcript = transcript
        self._identity = identity
        # Observe digest: last run time, pending counts, and last digested row id.
        self._observe_last: dict[str, float] = {}
        self._observe_pending: dict[str, int] = {}
        self._observe_watermark: dict[str, int] = {}

    def bind_sender(self, sender: TextSender) -> None:
        """Attach the text sender for this connection. The sender talks to WeChatEyes."""
        self._sender = sender

    def bind_eyes(self, eyes: WeChatEyes) -> None:
        """Attach hands/eyes for Callus context scroll and sticker capture."""
        self._eyes = eyes

    def _enrich_identity(self, message: IncomingTurn) -> IncomingTurn:
        """Cheap ReadLatest pass: fix sender / chat_type before reply policy runs."""
        eyes = self._eyes
        if eyes is None:
            return message
        read_latest = getattr(eyes, "read_latest", None)
        if not callable(read_latest):
            return message
        try:
            latest = read_latest(message.chat)
        except HandsError as exc:
            _log.info("read_latest skipped: %s", exc)
            return message
        if latest is None:
            return message
        return self._apply_latest(message, latest)

    def _latest_is_same_bubble(
        self, message: IncomingTurn, latest: SeenMessage
    ) -> bool:
        """True when ReadLatest points at the watched inbound bubble (not a newer one)."""
        mid = (message.msg_id or "").strip()
        lid = (latest.message_id or "").strip()
        if mid and lid:
            return mid == lid
        mi = int(message.index or 0)
        li = int(latest.index or 0)
        if mi > 0 and li > 0:
            return mi == li
        return False

    def _apply_latest(self, message: IncomingTurn, latest: SeenMessage) -> IncomingTurn:
        """Merge ReadLatest onto the inbound turn; refuse to overwrite a different bubble."""
        bridge_type = _normalize_chat_type(latest.chat_type)
        if not self._latest_is_same_bubble(message, latest):
            # Ambiguous / mismatched latest: only fill blank routing fields.
            sender = message.sender.strip() or (latest.sender or "").strip()
            chat_type = message.chat_type or bridge_type
            if sender == message.sender and chat_type == message.chat_type:
                return message
            return IncomingTurn(
                chat=message.chat,
                sender=sender,
                text=message.text,
                preview_only=message.preview_only,
                from_self=message.from_self,
                chat_type=chat_type,
                msg_id=message.msg_id,
                unread=message.unread,
                content_type=message.content_type,
                index=message.index,
                image_paths=message.image_paths,
            )
        sender = latest.sender or message.sender
        content_type = (
            (latest.content_type or message.content_type or "text").strip().lower()
            or "text"
        )
        index = latest.index or message.index
        image_paths = list(message.image_paths)
        if latest.attachments:
            image_paths = [a.path for a in latest.attachments if a.path]
        chat_type = bridge_type or message.chat_type
        return IncomingTurn(
            chat=message.chat,
            sender=sender,
            text=message.text,
            preview_only=message.preview_only,
            from_self=message.from_self,
            chat_type=chat_type,
            msg_id=message.msg_id or (latest.message_id or ""),
            unread=message.unread,
            content_type=content_type,
            index=index,
            image_paths=tuple(image_paths),
        )

    def _person_id(self, message: IncomingTurn) -> str:
        """Resolve a stable host person_id for impression storage (alias over nick)."""
        if self._identity is None:
            return message.impression_key
        chat = message.chat.strip()
        if not message.is_group:
            # Private chats: alias is (session title, "").
            return self._identity.resolve(chat, "")
        nick = message.sender.strip()
        if not nick:
            # No display nick yet — keep legacy key; merge UI cannot alias unknowns.
            return message.impression_key
        return self._identity.resolve(chat, nick)

    def _sync_transcript_cap(self) -> None:
        """Apply reply_policy transcript_max_messages onto the ring store."""
        if self._transcript is None:
            return
        self._transcript.set_max_messages(
            self._gate.current().transcript_max_messages
        )

    def _append_inbound(self, message: IncomingTurn, *, source: str) -> None:
        """Persist one inbound bubble into the local transcript ring."""
        if self._transcript is None:
            return
        self._sync_transcript_cap()
        body = message.user_text.strip()
        if not body and message.image_paths:
            body = "（表情）"
        if not body:
            return
        self._transcript.append(
            message.chat,
            text=body,
            sender=message.sender.strip(),
            from_self=False,
            source=source,
        )

    def _append_bot_reply(self, chat: str, reply: str) -> None:
        """Persist a delivered bot reply into the transcript ring."""
        if self._transcript is None:
            return
        self._sync_transcript_cap()
        self._transcript.append(
            chat,
            text=reply,
            sender="",
            from_self=True,
            source="allow",
        )

    def _enrich_context_and_media(
        self, message: IncomingTurn
    ) -> tuple[IncomingTurn, list[dict[str, str]]]:
        """Build reply history (transcript first) and optional sticker capture."""
        extra_history: list[dict[str, str]] = []
        image_paths = list(message.image_paths)
        content_type = (message.content_type or "text").strip().lower() or "text"
        index = message.index
        chat_type = message.chat_type
        policy = self._gate.current()
        limit = policy.context_message_limit
        used_transcript = False

        if self._transcript is not None and limit > 0:
            self._sync_transcript_cap()
            local = self._transcript.as_history(message.chat, limit)
            threshold = min(5, limit)
            if len(local) >= threshold:
                extra_history = local
                used_transcript = True

        eyes = self._eyes
        if not used_transcript and eyes is not None and limit > 0:
            read_context = getattr(eyes, "read_context", None)
            if callable(read_context):
                try:
                    rows = read_context(message.chat, limit=limit)
                except HandsError as exc:
                    _log.info("read_context skipped: %s", exc)
                    rows = []
                for row in rows:
                    if not chat_type and row.chat_type:
                        chat_type = _normalize_chat_type(row.chat_type)
                    if row.from_self:
                        role = "assistant"
                        body = row.text
                    else:
                        role = "user"
                        body = row.text
                        if row.sender:
                            body = f"[群友] {row.sender}\n{row.text}"
                    if body.strip():
                        extra_history.append({"role": role, "content": body.strip()})

        needs_gui_capture = (
            eyes is not None
            and content_type == "emotion"
            and not image_paths
            and callable(getattr(eyes, "capture_message_media", None))
        )
        if needs_gui_capture:
            try:
                media = eyes.capture_message_media(message.chat, index)  # type: ignore[attr-defined]
                image_paths = [a.path for a in media if a.path]
            except HandsError as exc:
                _log.info("capture_message_media skipped: %s", exc)

        enriched = IncomingTurn(
            chat=message.chat,
            sender=message.sender,
            text=message.text,
            preview_only=message.preview_only,
            from_self=message.from_self,
            chat_type=chat_type or message.chat_type,
            msg_id=message.msg_id,
            unread=message.unread,
            content_type=content_type,
            index=index,
            image_paths=tuple(image_paths),
        )
        return enriched, extra_history

    async def handle(self, message: IncomingTurn) -> None:
        """Run policy, LLM, send, then refresh impression / group culture / knowledge."""
        message = self._enrich_identity(message)
        _log.info(
            "inbound group=%s chat=%s nick=%s preview=%s text=%r",
            message.is_group,
            message.chat,
            message.sender.strip() or ("(未知)" if message.is_group else message.chat),
            message.preview_only,
            message.user_text[:80],
        )
        self._note_evolution_received(message)
        self._gate.note_inbound_engagement(message)
        skip = self._gate.decide(message)
        if skip:
            _log.info("skip reply reason=%s text=%r", skip, message.user_text[:80])
            return
        # History from transcript before appending this inbound turn.
        message, bridge_history = self._enrich_context_and_media(message)
        self._append_inbound(message, source="allow")
        user_text = message.user_text.strip()
        if not user_text and not message.image_paths:
            # decide() already reserved cooldown/cap; roll back when capture left nothing.
            self._gate.abandon_reply_slot()
            return
        if message.is_group:
            user_text = (
                f"[群友] {message.username}\n{user_text}"
                if user_text
                else f"[群友] {message.username}\n（表情）"
            )
        _log.info("正在回复 session=%s user=%s", message.session_id, message.username)
        limit = max(1, self._gate.current().context_message_limit) if self._gate.current().context_message_limit else 0
        if bridge_history:
            history = bridge_history[-limit:] if limit else bridge_history
        elif self._transcript is not None and limit:
            # Prefer ring after append: drop the just-appended user line from history.
            full = self._transcript.as_history(message.chat, limit + 1)
            history = full[:-1] if full else []
        else:
            history = list(self._memory.history(message.session_id))
        history = apply_recent_focus(
            history, self._gate.current().context_recent_focus
        )
        person_id = self._person_id(message)
        profile = self._impressions.touch(person_id, username=message.username)
        speaker = str(profile.get("username") or message.username)
        directory = self._impressions.directory_block(speaker_name=speaker)
        others = self._impressions.others_block(exclude_key=person_id)
        culture_text = ""
        if (
            message.is_group
            and self._group_culture is not None
            and message.group_id
        ):
            culture_text = str(
                self._group_culture.load(message.group_id).get("culture") or ""
            )
        evolution_text = ""
        if (
            message.is_group
            and self._prompt_evolution is not None
            and message.group_id
        ):
            evolution_text = str(
                self._prompt_evolution.load_active(message.group_id).get("prompt") or ""
            )
        knowledge_text = ""
        if self._knowledge is not None:
            knowledge_text = self._knowledge.slices_for(message.user_text)
        reply = await self._llm.complete(
            history,
            user_text,
            impression=str(profile.get("impression") or ""),
            directory=directory,
            others=others,
            group_culture=culture_text,
            knowledge=knowledge_text,
            prompt_evolution=evolution_text,
            image_paths=message.image_paths,
        )
        reply = strip_sticker_markers(reply)
        if not reply.strip():
            _log.warning("empty llm reply session=%s", message.session_id)
            self._gate.abandon_reply_slot()
            return
        if self._sender is None:
            _log.warning("no sender bound; skipping send session=%s", message.session_id)
            self._gate.abandon_reply_slot()
            return
        _log.info("正在发送 session=%s", message.session_id)
        delivered = self._sender.send_bubbles(message.chat, reply)
        if not delivered:
            _log.warning("reply not delivered session=%s", message.session_id)
            self._gate.abandon_reply_slot()
            return
        self._gate.confirm_reply_slot()
        now = time.time()
        self._gate.note_bot_reply(message.session_id, now)
        policy = self._gate.current()
        if message.is_group and not is_addressed(message, policy.bot_names):
            self._gate.note_unmentioned_reply(message.group_id, now)
        # Fallback copy must not enter memory — it poisons later turns (model
        # then roleplays 「还是没连上」 even after the API is healthy again).
        if reply.strip() == LLM_UNAVAILABLE_REPLY:
            _log.info(
                "replied with llm-unavailable fallback; skip memory session=%s",
                message.session_id,
            )
            return
        self._memory.append(message.session_id, "user", user_text)
        self._memory.append(message.session_id, "assistant", reply)
        self._append_bot_reply(message.chat, reply)
        _log.info("replied session=%s preview=%r", message.session_id, reply[:80])
        await self._run_archive_writers(
            message,
            user_text=user_text,
            assistant_text=reply,
            speaker_name=speaker,
            culture_text=culture_text,
            write_knowledge=True,
        )

    async def observe(self, message: IncomingTurn) -> None:
        """Listen-only: append transcript, batch-digest impression / culture."""
        message = self._enrich_identity(message)
        _log.info(
            "observe group=%s chat=%s nick=%s text=%r",
            message.is_group,
            message.chat,
            message.sender.strip() or ("(未知)" if message.is_group else message.chat),
            message.user_text[:80],
        )
        self._note_evolution_received(message)
        if not message.user_text.strip() and not message.image_paths:
            return
        self._append_inbound(message, source="observe")
        person_id = self._person_id(message)
        imp_key = f"imp:{person_id}"
        cul_key = f"cul:{message.group_id}" if message.is_group and message.group_id else ""
        self._note_observe_pending(imp_key)
        if cul_key:
            self._note_observe_pending(cul_key)

        now = time.time()
        policy = self._gate.current()
        interval = max(0.0, float(policy.observe_min_interval_seconds))
        min_msgs = max(1, int(policy.observe_digest_min_messages))
        imp_ok = self._observe_should_digest(imp_key, now, interval, min_msgs)
        cul_ok = False
        if cul_key:
            cul_ok = self._observe_should_digest(cul_key, now, interval, min_msgs)
        if not imp_ok:
            _log.info(
                "observe defer digest impression person=%s pending=%s",
                person_id[:8],
                self._observe_pending.get(imp_key, 0),
            )
        if cul_key and not cul_ok:
            _log.info(
                "observe defer digest culture chat=%s pending=%s",
                message.group_id[:40],
                self._observe_pending.get(cul_key, 0),
            )
        if not imp_ok and not cul_ok:
            return

        profile = self._impressions.touch(person_id, username=message.username)
        speaker = str(profile.get("username") or message.username)
        culture_text = ""
        if (
            message.is_group
            and self._group_culture is not None
            and message.group_id
        ):
            culture_text = str(
                self._group_culture.load(message.group_id).get("culture") or ""
            )

        if imp_ok:
            digest = self._observe_digest_text(message, imp_key, for_impression=True)
            wrote = await self._run_archive_writers(
                message,
                user_text=digest[0] or message.user_text.strip(),
                assistant_text=OBSERVE_NO_REPLY,
                speaker_name=speaker,
                culture_text=culture_text,
                write_impression=True,
                write_culture=False,
                write_knowledge=False,
                write_evolution=False,
                user_limit=_DIGEST_USER_LIMIT,
            )
            if wrote:
                self._observe_mark_digested(
                    imp_key, now, digest_rows_max_id=digest[1]
                )
            else:
                _log.info(
                    "observe keep pending impression person=%s (archive write failed)",
                    person_id[:8],
                )
        if cul_ok and cul_key:
            digest = self._observe_digest_text(message, cul_key, for_impression=False)
            wrote = await self._run_archive_writers(
                message,
                user_text=digest[0] or message.user_text.strip(),
                assistant_text=OBSERVE_NO_REPLY,
                speaker_name=speaker,
                culture_text=culture_text,
                write_impression=False,
                write_culture=True,
                write_knowledge=False,
                write_evolution=True,
                user_limit=_DIGEST_USER_LIMIT,
            )
            if wrote:
                self._observe_mark_digested(
                    cul_key, now, digest_rows_max_id=digest[1]
                )
            else:
                _log.info(
                    "observe keep pending culture chat=%s (archive write failed)",
                    message.group_id[:40],
                )
        elif imp_ok and message.is_group:
            # Impression-only digest still may draft evolution once.
            await self._run_archive_writers(
                message,
                user_text=message.user_text.strip(),
                assistant_text=OBSERVE_NO_REPLY,
                speaker_name=speaker,
                culture_text=culture_text,
                write_impression=False,
                write_culture=False,
                write_knowledge=False,
                write_evolution=True,
                user_limit=_DIGEST_USER_LIMIT,
            )

    def _note_evolution_received(self, message: IncomingTurn) -> None:
        """Count one bot-received group message toward prompt-evolution throttle."""
        if (
            not message.is_group
            or not message.group_id
            or self._prompt_evolution is None
        ):
            return
        self._prompt_evolution.note_received(message.group_id)

    def _note_observe_pending(self, key: str) -> None:
        """Count one inbound line toward the next archive digest for ``key``."""
        if not key:
            return
        self._observe_pending[key] = self._observe_pending.get(key, 0) + 1

    def _observe_should_digest(
        self,
        key: str,
        now: float,
        interval: float,
        min_messages: int,
    ) -> bool:
        """True when pending lines exist and interval or count threshold is met."""
        pending = self._observe_pending.get(key, 0)
        if pending <= 0:
            return False
        if interval <= 0:
            time_ok = True
        elif key not in self._observe_last:
            # Never digested: only the message-count path may fire (avoid epoch math).
            time_ok = False
        else:
            time_ok = now - self._observe_last[key] >= interval
        count_ok = pending >= min_messages
        return time_ok or count_ok

    def _observe_mark_digested(
        self, key: str, now: float, *, digest_rows_max_id: int
    ) -> None:
        """Clear pending count and advance watermark after a digest attempt."""
        self._observe_pending[key] = 0
        self._observe_last[key] = now
        if digest_rows_max_id > 0:
            self._observe_watermark[key] = max(
                self._observe_watermark.get(key, 0), digest_rows_max_id
            )

    def _observe_digest_text(
        self,
        message: IncomingTurn,
        key: str,
        *,
        for_impression: bool,
    ) -> tuple[str, int]:
        """Build multi-line digest text and max row id since the last watermark."""
        if self._transcript is None:
            body = message.user_text.strip()
            return body, 0
        after_id = self._observe_watermark.get(key, 0)
        if for_impression and message.is_group and message.sender.strip():
            rows = self._transcript.since(
                message.chat, after_id, sender=message.sender.strip()
            )
        else:
            rows = [
                row
                for row in self._transcript.since(message.chat, after_id)
                if not row.from_self
            ]
        if not rows:
            body = message.user_text.strip()
            if message.is_group and body:
                body = f"[群友] {message.username}\n{body}"
            return body, 0
        return _format_digest_rows(rows), max(row.id for row in rows)

    async def _run_archive_writers(
        self,
        message: IncomingTurn,
        *,
        user_text: str,
        assistant_text: str,
        speaker_name: str,
        culture_text: str,
        write_impression: bool = True,
        write_culture: bool = True,
        write_knowledge: bool = False,
        write_evolution: bool = True,
        user_limit: int = 800,
    ) -> bool:
        """Run archive writers; return False when a requested impression/culture write failed."""
        policy = self._gate.current()
        person_id = self._person_id(message)
        ok = True
        if write_impression:
            try:
                wrote = await self._impression_writer.after_turn(
                    user_key=person_id,
                    username=message.username,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    user_limit=user_limit,
                )
                ok = bool(wrote) and ok
            except Exception:
                _log.exception(
                    "impression after_turn failed person=%s", person_id[:8]
                )
                ok = False
        if (
            write_culture
            and message.is_group
            and self._group_culture_writer is not None
            and policy.group_culture_auto
            and message.group_id
        ):
            try:
                wrote = await self._group_culture_writer.after_turn(
                    chat=message.group_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    speaker_name=speaker_name,
                    user_limit=user_limit,
                )
                ok = bool(wrote) and ok
            except Exception:
                _log.exception(
                    "group culture after_turn failed chat=%s",
                    message.group_id[:40],
                )
                ok = False
        if (
            write_evolution
            and message.is_group
            and self._prompt_evolution_writer is not None
            and policy.prompt_evolution_auto
            and message.group_id
        ):
            try:
                await self._prompt_evolution_writer.after_turn(
                    chat=message.group_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    min_messages=policy.prompt_evolution_min_messages,
                    culture_text=culture_text,
                    speaker_name=speaker_name,
                )
            except Exception:
                _log.exception(
                    "prompt evolution after_turn failed chat=%s",
                    message.group_id[:40],
                )
        if write_knowledge and self._knowledge_writer is not None and policy.knowledge_auto:
            try:
                await self._knowledge_writer.after_turn(user_text, assistant_text)
            except Exception:
                _log.exception("knowledge after_turn failed")
        return ok


def _format_digest_rows(rows: list[TranscriptRow]) -> str:
    """Join transcript rows into one archive prompt block."""
    chunks: list[str] = []
    for row in rows:
        if row.from_self:
            chunks.append(f"[bot] {row.text}")
        elif row.sender:
            chunks.append(f"[群友] {row.sender}\n{row.text}")
        else:
            chunks.append(row.text)
    return "\n---\n".join(chunks)
