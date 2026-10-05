"""Watch WeChat messages and feed the chat brain (reconnect like echo_bot)."""

from __future__ import annotations

import asyncio
import logging
import time

from wejam import Client

from bot.allowlist import load_allow_chats
from bot.channel import should_handle, turns_from_event
from bot.chat import ChatBot
from bot.config import load_settings
from bot.impression.store import ImpressionStore
from bot.impression.writer import ImpressionWriter
from bot.llm.client import LLMClient
from bot.memory.store import MemoryStore
from bot.personas.catalog import PersonaCatalog
from bot.prompt_book import PromptBook
from bot.reply import TextSender
from bot.reply_policy import ReplyGate
from bot.seed import seed_runtime_files

_log = logging.getLogger(__name__)


def build_bot() -> ChatBot:
    """Wire LLM, memory, personas, impressions, and reply policy."""
    settings = load_settings()
    seed_runtime_files(settings)
    catalog = PersonaCatalog(settings.personas_index_path, settings.personas_dir)
    gate = ReplyGate(settings.reply_policy_path)
    llm = LLMClient(
        settings,
        prompts=PromptBook(catalog, gate=gate),
    )
    memory = MemoryStore(settings.data_dir / "bot.sqlite3", settings.memory_max_turns)
    impressions = ImpressionStore(settings.data_dir / "impressions")
    writer = ImpressionWriter(llm, impressions)
    return ChatBot(llm, memory, gate, impressions, writer)


def run_loop(bot: ChatBot | None = None, *, once: bool = False) -> None:
    """Connect to wejam, watch globally, handle allowed chats; reconnect on errors."""
    if bot is None:
        bot = build_bot()
    settings = load_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _log.info("chat runner target=%s", settings.wejam_target)
    while True:
        try:
            allowed = load_allow_chats(settings.allow_chats_path)
            _log.info("allow chats=%s", sorted(allowed) or "(empty — will not reply)")
            with Client(settings.wejam_target) as client:
                bot.bind_sender(TextSender(client))
                for event in client.watch_messages():
                    allowed = load_allow_chats(settings.allow_chats_path)
                    for turn in turns_from_event(event):
                        if not should_handle(turn, allowed):
                            continue
                        asyncio.run(bot.handle(turn))
                    if once:
                        return
        except KeyboardInterrupt:
            _log.info("runner stopped")
            return
        except Exception:
            _log.exception("wejam connection interrupted; retry in 2s")
            if once:
                raise
            time.sleep(2)


def main() -> int:
    """CLI entry: python -m bot"""
    run_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
