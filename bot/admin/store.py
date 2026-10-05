"""Shared admin operations for the native desktop UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.admin import bot_control, env_settings
from bot.allowlist import load_allow_chats, save_allow_chats
from bot.config import load_settings
from bot.impression.store import ImpressionStore
from bot.personas.catalog import PersonaCatalog
from bot.reply_policy import (
    SPEAK_MODES,
    guard_list_to_lines,
    lines_to_guard_list,
    load_reply_settings,
    merge_policy_guards,
    next_speak_mode,
    normalize_speak_mode,
    policy_guard_lists,
    speak_mode_label,
    strip_policy_guards,
    upsert_speak_mode_text,
)
from bot.seed import seed_runtime_files


@dataclass(frozen=True)
class BotSnapshot:
    """Runtime status shown in the admin UI."""

    running: bool
    pid: int | None
    llm_model: str
    llm_configured: bool
    wejam_target: str
    project: str


def _persona_catalog() -> PersonaCatalog:
    """Build a catalog from current settings (reloads toml and folders)."""
    settings = load_settings()
    seed_runtime_files(settings)
    return PersonaCatalog(settings.personas_index_path, settings.personas_dir)


def _impression_store() -> ImpressionStore:
    """Impression JSON root under data/impressions."""
    return ImpressionStore(load_settings().data_dir / "impressions")


def bot_snapshot() -> BotSnapshot:
    """Return bot process and config summary for the dashboard."""
    settings = load_settings()
    status = bot_control.bot_status()
    return BotSnapshot(
        running=bool(status.get("running")),
        pid=status.get("pid") if isinstance(status.get("pid"), int) else None,
        llm_model=settings.llm_model,
        llm_configured=bool(settings.llm_api_key),
        wejam_target=settings.wejam_target,
        project=str(bot_control.project_root()),
    )


def start_bot() -> BotSnapshot:
    """Start the chat runner via start-bot.sh."""
    bot_control.start_bot()
    return bot_snapshot()


def stop_bot() -> BotSnapshot:
    """Stop the chat runner via stop-bot.sh."""
    bot_control.stop_bot()
    return bot_snapshot()


def list_persona_packs() -> list[dict[str, object]]:
    """List persona packs with active flag and txt filenames."""
    catalog = _persona_catalog()
    active = catalog.active_id()
    rows: list[dict[str, object]] = []
    for entry in catalog.list_packs():
        rows.append(
            {
                "id": entry.id,
                "title": entry.title,
                "active": entry.id == active,
                "files": catalog.list_files(entry.id),
                "path": str(entry.path),
            }
        )
    return rows


def persona_active_id() -> str:
    """Return the enabled persona pack id."""
    return _persona_catalog().active_id()


def set_persona_active(persona_id: str) -> str:
    """Enable one persona pack for the next chat turn."""
    return _persona_catalog().set_active(persona_id)


def list_persona_files(persona_id: str) -> list[str]:
    """Txt filenames in a pack, reserved files first."""
    return _persona_catalog().list_files(persona_id)


def read_persona_file(persona_id: str, filename: str) -> str:
    """Read one txt from a pack."""
    return _persona_catalog().read_file(persona_id, filename)


def save_persona_file(persona_id: str, filename: str, text: str) -> Path:
    """Write one txt in a pack."""
    return _persona_catalog().save_file(persona_id, filename, text)


def add_persona_file(persona_id: str, filename: str) -> Path:
    """Create an empty txt in a pack."""
    return _persona_catalog().add_file(persona_id, filename)


def new_persona_pack(persona_id: str, title: str = "") -> dict[str, object]:
    """Create a pack and return its list-row dict."""
    entry = _persona_catalog().new_pack(persona_id, title)
    return {
        "id": entry.id,
        "title": entry.title,
        "active": entry.id == persona_active_id(),
        "files": list_persona_files(entry.id),
        "path": str(entry.path),
    }


def delete_persona_pack(persona_id: str) -> None:
    """Delete a non-active pack that is not the last remaining pack."""
    _persona_catalog().delete_pack(persona_id)


def read_reply_policy() -> tuple[str, Path]:
    """Load reply_policy.toml text."""
    path = load_settings().reply_policy_path
    if not path.is_file():
        return "", path
    return path.read_text(encoding="utf-8"), path


def read_reply_policy_parts() -> tuple[str, str, str]:
    """Split reply_policy.toml into frequency body, anti lines, and stay lines."""
    text, _path = read_reply_policy()
    anti, stay = policy_guard_lists(text)
    return (
        strip_policy_guards(text),
        guard_list_to_lines(anti),
        guard_list_to_lines(stay),
    )


def save_reply_policy(text: str) -> Path:
    """Write reply_policy.toml."""
    path = load_settings().reply_policy_path
    path.write_text(text, encoding="utf-8")
    return path


def save_reply_policy_parts(body: str, anti_text: str, stay_text: str) -> Path:
    """Merge frequency toml with guard editors and write reply_policy.toml."""
    merged = merge_policy_guards(
        body,
        lines_to_guard_list(anti_text),
        lines_to_guard_list(stay_text),
    )
    return save_reply_policy(merged)


def read_speak_mode() -> str:
    """Return the current group speak mode from reply_policy.toml."""
    return load_reply_settings(load_settings().reply_policy_path).speak_mode


def speak_mode_options() -> tuple[tuple[str, str], ...]:
    """Id and Chinese label for each reply-mode picker button."""
    return tuple((mode, speak_mode_label(mode)) for mode in SPEAK_MODES)


def set_speak_mode(mode: str) -> str:
    """Write speak_mode into reply_policy.toml; next inbound message reloads it."""
    path = load_settings().reply_policy_path
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    nxt = normalize_speak_mode(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(upsert_speak_mode_text(text, nxt), encoding="utf-8")
    return nxt


def cycle_speak_mode() -> str:
    """Advance speak_mode in reply_policy.toml."""
    return set_speak_mode(next_speak_mode(read_speak_mode()))


def list_impressions() -> list[dict[str, Any]]:
    """Return all impression records."""
    return _impression_store().list_records()


def load_impression(user_key: str) -> dict[str, Any]:
    """Load one impression by session key."""
    return _impression_store().load(user_key)


def save_impression(user_key: str, username: str, impression: str) -> Path:
    """Create or update one speaker's impression file."""
    store = _impression_store()
    record = store.load(user_key)
    record["username"] = username.strip()
    record["impression"] = impression.strip()
    return store.save(record)


def read_allow_chats() -> list[str]:
    """Return allowed WeChat session names."""
    names = load_allow_chats(load_settings().allow_chats_path)
    return sorted(names)


def write_allow_chats(text: str) -> Path:
    """Save whitelist from a multiline editor buffer."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return save_allow_chats(load_settings().allow_chats_path, lines)


def tail_log_lines(lines: int = 120) -> list[str]:
    """Return the last runner log lines."""
    path = bot_control.bot_log_path()
    if not path.is_file():
        return []
    cap = max(10, min(lines, 400))
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return all_lines[-cap:]


def clear_monitor_logs() -> Path:
    """Truncate the runner log so the monitor view starts empty."""
    path = bot_control.bot_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path
