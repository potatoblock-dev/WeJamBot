"""Copy example persona / whitelist into data/ on first run."""

from __future__ import annotations

import shutil
from pathlib import Path

from bot.config import Settings, project_root


def seed_runtime_files(settings: Settings) -> None:
    """Ensure default persona pack and allow-list exist so the console has something to edit."""
    example = project_root() / "examples" / "personas" / "default" / "persona.txt"
    dest_dir = settings.personas_dir / "default"
    dest = dest_dir / "persona.txt"
    if example.is_file() and not dest.is_file():
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, dest)
    if not settings.allow_chats_path.is_file():
        settings.allow_chats_path.parent.mkdir(parents=True, exist_ok=True)
        settings.allow_chats_path.write_text("文件传输助手\n", encoding="utf-8")
    _touch(settings.reply_policy_path)
    _touch(settings.personas_index_path)


def _touch(path: Path) -> None:
    """No-op if the committed default file already exists."""
    if path.is_file():
        return
