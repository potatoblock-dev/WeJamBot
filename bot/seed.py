"""Copy example persona / knowledge / whitelist into data/ on first run."""

from __future__ import annotations

import shutil
from pathlib import Path

from bot.config import Settings, project_root


def seed_runtime_files(settings: Settings) -> None:
    """Ensure persona packs, knowledge, allow-list, and prompt_evolution dirs exist."""
    _seed_persona_pack(settings, "default")
    _seed_persona_pack(settings, "template")
    if not settings.allow_chats_path.is_file():
        settings.allow_chats_path.parent.mkdir(parents=True, exist_ok=True)
        settings.allow_chats_path.write_text("文件传输助手\n", encoding="utf-8")
    if not settings.observe_chats_path.is_file():
        settings.observe_chats_path.parent.mkdir(parents=True, exist_ok=True)
        settings.observe_chats_path.write_text("", encoding="utf-8")
    knowledge_dir = settings.data_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    notebook = knowledge_dir / "notebook.md"
    seed_nb = project_root() / "examples" / "knowledge" / "notebook.md"
    if not notebook.is_file() and seed_nb.is_file():
        shutil.copy(seed_nb, notebook)
    (settings.data_dir / "group_culture").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "impressions").mkdir(parents=True, exist_ok=True)
    evo_root = settings.data_dir / "prompt_evolution"
    for sub in ("pending", "active", "state"):
        (evo_root / sub).mkdir(parents=True, exist_ok=True)
    _touch(settings.reply_policy_path)
    _touch(settings.personas_index_path)


def _seed_persona_pack(settings: Settings, pack_id: str) -> None:
    """Copy examples/personas/<id>/persona.txt into data when the runtime file is missing."""
    example = project_root() / "examples" / "personas" / pack_id / "persona.txt"
    dest_dir = settings.personas_dir / pack_id
    dest = dest_dir / "persona.txt"
    if example.is_file() and not dest.is_file():
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, dest)


def _touch(path: Path) -> None:
    """No-op if the committed default file already exists."""
    if path.is_file():
        return
