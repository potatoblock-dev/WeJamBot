"""Watch WeChat messages and feed the chat brain (reconnect like echo_bot)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from bot.allowlist import load_allow_chats
from bot.bot_mode import apply_bot_mode, load_bot_mode
from bot.channel import IncomingTurn, incoming_turn, should_handle, should_observe
from bot.chat import ChatBot
from bot.coalesce import TurnCoalescer
from bot.config import load_settings, project_root
from bot.eyes import WeChatEyes
from bot.group_culture.store import GroupCultureStore
from bot.group_culture.writer import GroupCultureWriter
from bot.identity.directory import IdentityDirectory
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.knowledge.store import KnowledgeStore
from bot.knowledge.writer import KnowledgeWriter
from bot.llm.factory import build_archive_llm, build_chat_llm
from bot.memory.store import MemoryStore
from bot.personas.catalog import PersonaCatalog
from bot.prompt_book import PromptBook
from bot.prompt_evolution.store import PromptEvolutionStore
from bot.prompt_evolution.writer import PromptEvolutionWriter
from bot.reply import TextSender
from bot.reply_policy import ReplyGate, load_reply_settings
from bot.seed import seed_runtime_files
from bot.transcript.store import TranscriptStore

_log = logging.getLogger(__name__)


def build_bot() -> ChatBot:
    """Wire chat LLM, archive LLM, memory, personas, and writers."""
    settings = load_settings()
    seed_runtime_files(settings)
    catalog = PersonaCatalog(settings.personas_index_path, settings.personas_dir)
    gate = ReplyGate(settings.reply_policy_path)
    chat_llm = build_chat_llm(
        settings,
        prompts=PromptBook(catalog, gate=gate),
    )
    archive_llm = build_archive_llm(settings)
    db_path = settings.data_dir / "bot.sqlite3"
    memory = MemoryStore(db_path, settings.memory_max_turns)
    # Ring size follows reply_policy; ChatBot re-reads cap on each append.
    transcript = TranscriptStore(
        db_path,
        load_reply_settings(settings.reply_policy_path).transcript_max_messages,
    )
    impressions = ImpressionStore(settings.data_dir / "impressions")
    identity = IdentityDirectory(
        settings.data_dir / "identity" / "aliases.json",
        impressions,
    )
    writer = ImpressionWriter(archive_llm, impressions)
    group_culture = GroupCultureStore(settings.data_dir / "group_culture")
    culture_writer = GroupCultureWriter(archive_llm, group_culture)
    prompt_evolution = PromptEvolutionStore(settings.data_dir / "prompt_evolution")
    evolution_writer = PromptEvolutionWriter(archive_llm, prompt_evolution)
    knowledge = KnowledgeStore(settings.data_dir / "knowledge" / "notebook.md")
    knowledge.ensure_scaffold(
        project_root() / "examples" / "knowledge" / "notebook.md"
    )
    knowledge_writer = KnowledgeWriter(archive_llm, knowledge)
    return ChatBot(
        chat_llm,
        memory,
        gate,
        impressions,
        writer,
        group_culture=group_culture,
        group_culture_writer=culture_writer,
        knowledge=knowledge,
        knowledge_writer=knowledge_writer,
        prompt_evolution=prompt_evolution,
        prompt_evolution_writer=evolution_writer,
        transcript=transcript,
        identity=identity,
    )


def _handle_sync(bot: ChatBot, turn: IncomingTurn) -> None:
    """Run one ChatBot reply turn on a fresh loop so the watch thread can keep receiving."""
    asyncio.run(bot.handle(turn))


def _observe_sync(bot: ChatBot, turn: IncomingTurn) -> None:
    """Run one observe (archive-only) turn on a fresh loop."""
    asyncio.run(bot.observe(turn))


def consume_messages(
    hands: WeChatEyes,
    submit: Callable[[IncomingTurn], None],
    *,
    allowed: set[str] | None = None,
    allow_path: Path | None = None,
    observe: set[str] | None = None,
    observe_path: Path | None = None,
    observe_submit: Callable[[IncomingTurn], None] | None = None,
    mode_path: Path | None = None,
    mode: str | None = None,
    once: bool = False,
) -> None:
    """Read plain messages; reply allowlist → submit, observe-only → observe_submit.

    Pass allow_path / observe_path to reload lists on every line. Pass fixed sets
    for tests. ``mode_path`` / ``mode`` apply global listen vs reply.

    When neither ``mode`` nor ``mode_path`` is set, mode defaults to **listen**
    (allow chats are folded into observe; no replies). Reply-path unit tests
    must pass ``mode=\"reply\"`` explicitly.
    """
    if allowed is None and allow_path is None:
        raise ValueError("consume_messages needs allowed or allow_path")
    fixed_allow = allowed if allowed is not None else set()
    fixed_observe = observe if observe is not None else set()
    for message in hands.subscribe():
        current_allow = (
            load_allow_chats(allow_path) if allow_path is not None else fixed_allow
        )
        current_observe = (
            load_allow_chats(observe_path)
            if observe_path is not None
            else fixed_observe
        )
        current_mode = (
            load_bot_mode(mode_path)
            if mode_path is not None
            else (mode if mode is not None else "listen")
        )
        effective_allow, effective_observe = apply_bot_mode(
            current_mode, allow=current_allow, observe=current_observe
        )
        turn = incoming_turn(message)
        if should_handle(turn, effective_allow):
            submit(turn)
        elif observe_submit is not None and should_observe(
            turn, effective_observe, effective_allow
        ):
            observe_submit(turn)
        elif not turn.from_self and (
            turn.text.strip() or turn.image_paths
        ):
            # Visible inbound that neither list claims — otherwise looks like "deaf".
            _log.info(
                "inbound skipped not_listed chat=%s allow=%s observe=%s mode=%s preview=%r",
                turn.chat,
                turn.chat in current_allow,
                turn.chat in current_observe,
                current_mode,
                turn.user_text[:80],
            )
        if once:
            return


def _open_wejam(target: str) -> WeChatEyes:
    """Open the WeJam adapter. This is the runner's only link to the driver."""
    from bot.hands import WeJamHands

    return WeJamHands(target)


def run_loop(
    bot: ChatBot | None = None,
    *,
    once: bool = False,
    open_hands: Callable[[], WeChatEyes] | None = None,
) -> None:
    """Watch globally, handle allowed chats, observe listed chats, reconnect on drop."""
    if bot is None:
        bot = build_bot()
    settings = load_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _log.info("chat runner target=%s", settings.wejam_target)
    reply_coalescer = TurnCoalescer(
        lambda turn: _handle_sync(bot, turn),
        burst_window=settings.coalesce_burst_seconds,
        debounce=settings.coalesce_debounce_seconds,
    )
    observe_coalescer = TurnCoalescer(
        lambda turn: _observe_sync(bot, turn),
        burst_window=settings.coalesce_burst_seconds,
        debounce=settings.coalesce_debounce_seconds,
    )

    def _opener() -> WeChatEyes:
        """Open WeJam hands for this reconnect attempt."""
        return _open_wejam(settings.wejam_target)

    opener = open_hands or _opener
    while True:
        try:
            allowed = load_allow_chats(settings.allow_chats_path)
            observed = load_allow_chats(settings.observe_chats_path)
            mode = load_bot_mode(settings.bot_mode_path)
            _log.info(
                "bot_mode=%s allow chats=%s observe chats=%s",
                mode,
                sorted(allowed) or "(empty — will not reply)",
                sorted(observed) or "(empty — no observe)",
            )
            hands = opener()
            try:
                bot.bind_sender(TextSender(hands))
                bot.bind_eyes(hands)
                consume_messages(
                    hands,
                    reply_coalescer.submit,
                    allow_path=settings.allow_chats_path,
                    observe_path=settings.observe_chats_path,
                    observe_submit=observe_coalescer.submit,
                    mode_path=settings.bot_mode_path,
                    once=once,
                )
                if once:
                    reply_coalescer.flush_pending()
                    observe_coalescer.flush_pending()
                    return
            finally:
                hands.close()
        except KeyboardInterrupt:
            _log.info("runner stopped")
            reply_coalescer.flush_pending()
            observe_coalescer.flush_pending()
            return
        except Exception:
            _log.exception("wejam connection interrupted; retry in 2s")
            if once:
                reply_coalescer.flush_pending()
                observe_coalescer.flush_pending()
                raise
            time.sleep(2)


def main() -> int:
    """CLI entry: python -m bot"""
    run_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
