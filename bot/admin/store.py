"""Shared admin operations for the native desktop UI."""

from __future__ import annotations

import json
import subprocess
import sys
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
    healthy: bool


@dataclass(frozen=True)
class AdminUiState:
    """Persisted desktop admin UI preferences."""

    policy_anti_injection_expanded: bool = True
    policy_stay_on_prompt_expanded: bool = True


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
    running = bool(status.get("running"))
    return BotSnapshot(
        running=running,
        pid=status.get("pid") if isinstance(status.get("pid"), int) else None,
        llm_model=settings.llm_model,
        llm_configured=bool(settings.llm_api_key),
        wejam_target=settings.wejam_target,
        project=str(bot_control.project_root()),
        healthy=running,
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


def log_file_size() -> int:
    """Return bot.log size in bytes, or 0 if missing."""
    path = bot_control.bot_log_path()
    try:
        return path.stat().st_size
    except OSError:
        return 0


def personas_dir() -> Path:
    """Return the local persona packs folder."""
    path = load_settings().personas_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def reveal_in_file_manager(path: Path) -> Path:
    """Open a folder in Finder (macOS) or the file manager (Linux)."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    return path


def _admin_ui_path() -> Path:
    """JSON file for fold-state and other desktop prefs."""
    return load_settings().data_dir / "admin_ui.json"


def read_admin_ui() -> AdminUiState:
    """Load persisted desktop UI preferences."""
    path = _admin_ui_path()
    if not path.is_file():
        return AdminUiState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AdminUiState()
    if not isinstance(raw, dict):
        return AdminUiState()
    return AdminUiState(
        policy_anti_injection_expanded=bool(raw.get("policy_anti_injection_expanded", True)),
        policy_stay_on_prompt_expanded=bool(raw.get("policy_stay_on_prompt_expanded", True)),
    )


def save_admin_ui(state: AdminUiState) -> Path:
    """Write desktop UI preferences for the next launch."""
    path = _admin_ui_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "policy_anti_injection_expanded": state.policy_anti_injection_expanded,
        "policy_stay_on_prompt_expanded": state.policy_stay_on_prompt_expanded,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def import_impression_files(paths: list[Path]) -> tuple[int, int, list[str]]:
    """Import impression JSON files; return (imported, skipped, error lines)."""
    imported = 0
    skipped = 0
    errors: list[str] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: 无法读取 JSON（{exc}）")
            continue
        rows: list[dict[str, object]] = []
        if isinstance(raw, list):
            rows = [item for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            if raw.get("user_key") or raw.get("user_openid"):
                rows = [raw]
            nested = raw.get("impressions")
            if isinstance(nested, list):
                rows.extend(item for item in nested if isinstance(item, dict))
        if not rows:
            errors.append(f"{path.name}: 没有可导入的记录（需含 user_key）")
            continue
        for index, row in enumerate(rows, start=1):
            key = str(row.get("user_key") or row.get("user_openid") or "").strip()
            if not key:
                skipped += 1
                errors.append(f"{path.name}#{index}: 缺少 user_key，已跳过")
                continue
            save_impression(
                key,
                str(row.get("username") or ""),
                str(row.get("impression") or ""),
            )
            imported += 1
    return imported, skipped, errors
