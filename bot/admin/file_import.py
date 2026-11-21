"""Import persona / impression / group-culture text (and JSON) files for the admin UI."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from bot.admin import store
from bot.personas.catalog import (
    normalize_persona_filename,
    normalize_persona_id,
    suggest_persona_id,
)

_TEXT_SUFFIXES = {".txt", ".md", ".text"}
_JSON_SUFFIXES = {".json"}
_KNOWN_PERSONA_FILES = frozenset(
    {"persona.txt", "anti_injection.txt", "stay_on_prompt.txt"}
)


def collect_import_paths(paths: list[Path]) -> list[Path]:
    """Expand directories one level and keep readable text/json files."""
    out: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.is_file() and _is_importable(child):
                    out.append(child)
            continue
        if path.is_file() and _is_importable(path):
            out.append(path)
    return out


def _is_importable(path: Path) -> bool:
    """True for plain text or JSON payloads we know how to import."""
    suffix = path.suffix.lower()
    return suffix in _TEXT_SUFFIXES or suffix in _JSON_SUFFIXES


def import_persona_paths(
    paths: list[Path],
    *,
    into_persona_id: str = "",
    pack_overrides: dict[str, tuple[str, str]] | None = None,
) -> tuple[int, int, list[str]]:
    """Import txt/md/json into persona packs.

    - Bare ``.txt`` / ``.md``: new or existing pack named from the stem; writes ``persona.txt``.
    - Known names (``persona.txt`` / ``anti_injection.txt`` / ``stay_on_prompt.txt``):
      write into ``into_persona_id`` when set, else stem pack.
    - JSON with ``persona`` / ``anti_injection`` / ``stay_on_prompt`` keys: same as a pack.
    - ``pack_overrides`` maps resolved path string → ``(persona_id, title)`` from the UI.

    Returns (imported_files, skipped, error lines).
    """
    imported = 0
    skipped = 0
    errors: list[str] = []
    target = normalize_persona_id(into_persona_id)
    overrides = pack_overrides or {}
    for path in collect_import_paths(paths):
        suffix = path.suffix.lower()
        override = overrides.get(str(path.resolve())) or overrides.get(str(path))
        try:
            if suffix in _JSON_SUFFIXES:
                n, skip, err = _import_persona_json(
                    path,
                    fallback_id=target,
                    override=override,
                )
            else:
                n, skip, err = _import_persona_text(
                    path,
                    into_persona_id=target,
                    override=override,
                )
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            skipped += 1
            continue
        imported += n
        skipped += skip
        errors.extend(err)
    return imported, skipped, errors


def persona_import_needs_identity(path: Path, *, into_persona_id: str = "") -> bool:
    """True when the UI should ask for pack id / title before importing this file."""
    resolved = Path(path)
    if not resolved.is_file():
        return False
    suffix = resolved.suffix.lower()
    into = normalize_persona_id(into_persona_id)
    if suffix in _JSON_SUFFIXES:
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        if isinstance(raw, dict) and normalize_persona_id(str(raw.get("id") or "")):
            return False
        if normalize_persona_id(resolved.stem):
            return False
        return True
    filename = normalize_persona_filename(resolved.name)
    if filename in _KNOWN_PERSONA_FILES and into:
        return False
    if into and filename:
        # Valid ascii .txt dropped onto a selected pack → write that file name.
        return False
    return True


def default_persona_identity(path: Path) -> tuple[str, str]:
    """Suggest (id, title) for the import dialog from a file path."""
    stem = Path(path).stem.strip() or "persona"
    return suggest_persona_id(stem), stem


def import_impression_paths(paths: list[Path]) -> tuple[int, int, list[str]]:
    """Import impression JSON (existing) or plain text (stem → username)."""
    imported = 0
    skipped = 0
    errors: list[str] = []
    json_paths: list[Path] = []
    for path in collect_import_paths(paths):
        if path.suffix.lower() in _JSON_SUFFIXES:
            json_paths.append(path)
            continue
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
            skipped += 1
            continue
        if not body:
            skipped += 1
            errors.append(f"{path.name}: 空文件，已跳过")
            continue
        username = path.stem.strip() or "imported"
        person_id = str(uuid.uuid4())
        store.save_impression(person_id, username, body)
        imported += 1
    if json_paths:
        j_imp, j_skip, j_err = store.import_impression_files(json_paths)
        imported += j_imp
        skipped += j_skip
        errors.extend(j_err)
    return imported, skipped, errors


def import_group_culture_paths(paths: list[Path]) -> tuple[int, int, list[str]]:
    """Import group-culture JSON or text (stem → chat title)."""
    imported = 0
    skipped = 0
    errors: list[str] = []
    for path in collect_import_paths(paths):
        suffix = path.suffix.lower()
        try:
            if suffix in _JSON_SUFFIXES:
                n, skip, err = _import_culture_json(path)
            else:
                n, skip, err = _import_culture_text(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            skipped += 1
            continue
        imported += n
        skipped += skip
        errors.extend(err)
    return imported, skipped, errors


def _import_persona_text(
    path: Path,
    *,
    into_persona_id: str,
    override: tuple[str, str] | None = None,
) -> tuple[int, int, list[str]]:
    """Write one text file into a persona pack."""
    body = path.read_text(encoding="utf-8")
    filename = normalize_persona_filename(path.name)
    stem_id = normalize_persona_id(path.stem)
    override_id = normalize_persona_id(override[0]) if override else ""
    override_title = (override[1] or "").strip() if override else ""

    if override_id:
        # UI named a pack: treat file body as that pack's persona.txt.
        pack_id = override_id
        file_name = "persona.txt"
    elif filename in _KNOWN_PERSONA_FILES and into_persona_id:
        pack_id = into_persona_id
        file_name = filename
    elif filename in _KNOWN_PERSONA_FILES:
        if filename != "persona.txt" and not stem_id:
            return (
                0,
                1,
                [f"{path.name}: 请先选中一个人设包，再导入 {filename}"],
            )
        pack_id = into_persona_id or stem_id
        file_name = filename
    elif into_persona_id and filename:
        pack_id = into_persona_id
        file_name = filename
    else:
        pack_id = into_persona_id or stem_id
        file_name = "persona.txt"

    if not pack_id:
        return 0, 1, [f"{path.name}: 无法从文件名得到合法人设 id（请填写 id）"]
    known_ids = {str(row["id"]) for row in store.list_persona_packs()}
    title = override_title or path.stem
    if pack_id not in known_ids:
        store.new_persona_pack(pack_id, title)
    store.save_persona_file(pack_id, file_name, body)
    return 1, 0, []


def _import_persona_json(
    path: Path,
    *,
    fallback_id: str,
    override: tuple[str, str] | None = None,
) -> tuple[int, int, list[str]]:
    """Import a JSON object shaped like a persona pack."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return 0, 1, [f"{path.name}: JSON 须为对象"]
    override_id = normalize_persona_id(override[0]) if override else ""
    override_title = (override[1] or "").strip() if override else ""
    pack_id = (
        override_id
        or normalize_persona_id(str(raw.get("id") or path.stem))
        or fallback_id
    )
    if not pack_id:
        return 0, 1, [f"{path.name}: 缺少合法 id（请填写 id）"]
    title = override_title or str(raw.get("title") or path.stem)
    if pack_id not in {row["id"] for row in store.list_persona_packs()}:
        store.new_persona_pack(pack_id, title)
    count = 0
    persona = str(raw.get("persona") or raw.get("persona.txt") or "").strip()
    if persona:
        store.save_persona_file(pack_id, "persona.txt", persona)
        count += 1
    anti = raw.get("anti_injection")
    if isinstance(anti, list):
        store.save_persona_file(
            pack_id, "anti_injection.txt", "\n".join(str(x) for x in anti)
        )
        count += 1
    elif isinstance(anti, str) and anti.strip():
        store.save_persona_file(pack_id, "anti_injection.txt", anti)
        count += 1
    stay = raw.get("stay_on_prompt")
    if isinstance(stay, list):
        store.save_persona_file(
            pack_id, "stay_on_prompt.txt", "\n".join(str(x) for x in stay)
        )
        count += 1
    elif isinstance(stay, str) and stay.strip():
        store.save_persona_file(pack_id, "stay_on_prompt.txt", stay)
        count += 1
    if count == 0:
        return 0, 1, [f"{path.name}: 没有 persona / anti_injection / stay_on_prompt"]
    return count, 0, []


def _import_culture_text(path: Path) -> tuple[int, int, list[str]]:
    """Import one chat culture from a text file named after the chat."""
    chat = path.stem.strip()
    body = path.read_text(encoding="utf-8").strip()
    if not chat:
        return 0, 1, [f"{path.name}: 文件名不能为空"]
    if not body:
        return 0, 1, [f"{path.name}: 空文件，已跳过"]
    store.save_group_culture(chat, "", body)
    return 1, 0, []


def _import_culture_json(path: Path) -> tuple[int, int, list[str]]:
    """Import one or many group-culture JSON records."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    if isinstance(raw, list):
        rows = [item for item in raw if isinstance(item, dict)]
    elif isinstance(raw, dict):
        if raw.get("chat"):
            rows = [raw]
        nested = raw.get("cultures") or raw.get("group_culture")
        if isinstance(nested, list):
            rows.extend(item for item in nested if isinstance(item, dict))
    if not rows:
        return 0, 1, [f"{path.name}: 没有可导入的群文化（需含 chat）"]
    imported = 0
    skipped = 0
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        chat = str(row.get("chat") or "").strip()
        if not chat:
            skipped += 1
            errors.append(f"{path.name}#{index}: 缺少 chat")
            continue
        store.save_group_culture(
            chat,
            str(row.get("label") or ""),
            str(row.get("culture") or row.get("body") or ""),
        )
        imported += 1
    return imported, skipped, errors
