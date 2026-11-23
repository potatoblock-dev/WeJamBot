"""Load personas.toml and persona txt packs (hot-reload like stickers)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")
_ALIAS_LINE = re.compile(r"(?m)^(- aliases:\s*)(.*)$")
_PERSONA_ID_LINE = re.compile(r"(?m)^(- persona_id:\s*)(`[^`]*`|.*)$")
_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}\.txt$")
_PERSONA_TXT = "persona.txt"
_ANTI_TXT = "anti_injection.txt"
_STAY_TXT = "stay_on_prompt.txt"
_SPECIAL_ORDER = (_PERSONA_TXT, _ANTI_TXT, _STAY_TXT)
_FALLBACK_PERSONA = (
    "你是微信里的闲聊搭子，不是客服也不是 AI 助手。"
    "用简体中文、口语、短句，像真人发微信；多数一两句、闲聊尽量四十字内。"
    "不代别人发言、不装作看见没发过的内容。"
    "不用 Markdown、不堆客服腔、不主动甩链接、不输出违法违规内容。"
    "只能发文字；做不到就直说。分多条时单独一行写 ---。"
)
_DEFAULT_PACK = "default"


@dataclass(frozen=True)
class PersonaEntry:
    """One persona pack listed in personas.toml or found as a folder."""

    id: str
    title: str
    path: Path


def normalize_persona_id(raw: str) -> str:
    """Return a lowercase folder-safe id, or empty if the name is invalid."""
    candidate = (raw or "").strip().lower().replace(" ", "_")
    if _ID_RE.match(candidate):
        return candidate
    return ""


def suggest_persona_id(raw: str) -> str:
    """Suggest a folder-safe id from a display title or filename stem.

    Keeps ascii letters/digits/_/- when possible; otherwise ``persona_`` + a
    short hash so Chinese titles still get an importable default.
    """
    text = (raw or "").strip()
    direct = normalize_persona_id(text)
    if direct:
        return direct
    ascii_bits = re.sub(r"[^a-zA-Z0-9_\-]+", "_", text).strip("_").lower()
    ascii_bits = re.sub(r"_+", "_", ascii_bits)
    from_ascii = normalize_persona_id(ascii_bits)
    if from_ascii:
        return from_ascii
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8") or b"persona").hexdigest()[:8]
    return f"persona_{digest}"


def normalize_persona_filename(raw: str) -> str:
    """Return a safe .txt name, or empty if invalid."""
    name = (raw or "").strip().lower()
    if not name.endswith(".txt"):
        name = f"{name}.txt"
    if _FILE_RE.match(name):
        return name
    return ""


class PersonaCatalog:
    """Hot-reloadable map from persona id to a folder of txt files."""

    def __init__(
        self,
        index_path: Path,
        personas_dir: Path,
        json_migrate_path: Path | None = None,
    ) -> None:
        self._index_path = index_path
        self._personas_dir = personas_dir
        self._json_migrate_path = json_migrate_path
        self._mtime = -1.0
        self._folder_stamp: tuple[tuple[str, float], ...] = ()
        self._by_id: dict[str, PersonaEntry] = {}
        self._active = ""
        self._personas_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_from_json_if_needed()
        self._reload_if_changed()

    def invalidate(self) -> None:
        """Force reload on the next lookup."""
        self._mtime = -1.0
        self._folder_stamp = ()

    def active_id(self) -> str:
        """Return the enabled pack id, or empty when none exist."""
        self._reload_if_changed()
        if self._active in self._by_id:
            return self._active
        if self._by_id:
            return sorted(self._by_id)[0]
        return ""

    def list_packs(self) -> list[PersonaEntry]:
        """Return persona packs sorted by id."""
        self._reload_if_changed()
        return [self._by_id[key] for key in sorted(self._by_id)]

    def entry(self, persona_id: str) -> PersonaEntry | None:
        """Return one pack, or None if unknown."""
        self._reload_if_changed()
        return self._by_id.get(normalize_persona_id(persona_id))

    def dir_for(self, persona_id: str) -> Path | None:
        """Return the pack folder if it exists."""
        entry = self.entry(persona_id)
        if entry is None or not entry.path.is_dir():
            return None
        return entry.path

    def list_files(self, persona_id: str) -> list[str]:
        """Return txt filenames in display order for a pack."""
        folder = self.dir_for(persona_id)
        if folder is None:
            return []
        names = [
            path.name
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() == ".txt"
        ]
        return _sorted_txt_names(names)

    def read_file(self, persona_id: str, filename: str) -> str:
        """Read one txt in a pack; missing file is empty."""
        path = self._file_path(persona_id, filename)
        if path is None or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def save_file(self, persona_id: str, filename: str, text: str) -> Path:
        """Write one txt in a pack (creates the file)."""
        path = self._file_path(persona_id, filename, create_dir=True)
        if path is None:
            raise ValueError("无效的人设 id 或文件名")
        path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
        self.invalidate()
        return path

    def add_file(self, persona_id: str, filename: str) -> Path:
        """Create an empty txt in a pack; refuse overwrite."""
        name = normalize_persona_filename(filename)
        if not name:
            raise ValueError("文件名仅限小写字母、数字、下划线和短横，并以 .txt 结尾")
        folder = self.dir_for(persona_id)
        if folder is None:
            raise ValueError("人设不存在")
        path = folder / name
        if path.exists():
            raise ValueError(f"已有文件 {name}")
        path.write_text("", encoding="utf-8")
        self.invalidate()
        return path

    def new_pack(self, persona_id: str, title: str = "") -> PersonaEntry:
        """Create a pack folder with an empty persona.txt and register it."""
        key = normalize_persona_id(persona_id)
        if not key:
            raise ValueError("人设 id 仅限小写字母、数字、下划线和短横")
        self._reload_if_changed()
        if key in self._by_id:
            raise ValueError(f"已有人设 {key}")
        folder = self._personas_dir / key
        folder.mkdir(parents=True, exist_ok=True)
        persona_path = folder / _PERSONA_TXT
        if not persona_path.exists():
            persona_path.write_text("", encoding="utf-8")
        label = (title or key).strip()
        self._by_id[key] = PersonaEntry(id=key, title=label, path=folder.resolve())
        if not self._active:
            self._active = key
        self._write_index()
        self.invalidate()
        return self._by_id[key]

    def update_names(
        self,
        persona_id: str,
        *,
        new_id: str,
        title: str,
        aliases: str,
    ) -> PersonaEntry:
        """Change the display title, folder id, and the aliases line in persona.txt.

        A new id moves the pack folder and follows the active flag. An aliases
        value rewrites `- aliases:`; an empty value clears that line when it
        already exists and does not invent one.
        """
        self._reload_if_changed()
        old = normalize_persona_id(persona_id)
        nxt = normalize_persona_id(new_id)
        label = (title or "").strip()
        if not old or old not in self._by_id:
            raise ValueError("人设不存在")
        if not nxt:
            raise ValueError("人设 id 仅限小写字母、数字、下划线和短横")
        if not label:
            raise ValueError("显示名不能为空")
        if nxt != old and nxt in self._by_id:
            raise ValueError(f"已有人设 {nxt}")
        entry = self._by_id[old]
        folder = entry.path
        if nxt != old:
            dest = (self._personas_dir / nxt).resolve()
            if dest.exists():
                raise ValueError(f"已有人设 {nxt}")
            folder.rename(dest)
            folder = dest
            del self._by_id[old]
            if self._active == old:
                self._active = nxt
        self._by_id[nxt] = PersonaEntry(id=nxt, title=label, path=folder)
        _rewrite_persona_name_lines(folder / _PERSONA_TXT, nxt, aliases)
        self._write_index()
        self.invalidate()
        return self._by_id[nxt]

    def set_active(self, persona_id: str) -> str:
        """Enable one pack; next chat reloads PromptBook from disk."""
        key = normalize_persona_id(persona_id)
        self._reload_if_changed()
        if key not in self._by_id:
            raise ValueError("人设不存在")
        self._active = key
        self._write_index()
        self.invalidate()
        return key

    def delete_pack(self, persona_id: str) -> None:
        """Remove a pack folder; refuse if it is active or the last remaining pack."""
        key = normalize_persona_id(persona_id)
        self._reload_if_changed()
        if key not in self._by_id:
            raise ValueError("人设不存在")
        if len(self._by_id) <= 1:
            raise ValueError("不能删除最后一套人设")
        if key == self.active_id():
            raise ValueError("请先启用另一套人设再删除")
        folder = self._by_id[key].path
        if folder.is_dir():
            shutil.rmtree(folder)
        del self._by_id[key]
        self._write_index()
        self.invalidate()

    def assemble_system_chunks(self) -> list[str]:
        """Build ordered prompt chunks for the active pack."""
        self._reload_if_changed()
        pack_id = self.active_id()
        folder = self.dir_for(pack_id) if pack_id else None
        if folder is None:
            return [_FALLBACK_PERSONA]
        chunks: list[str] = []
        persona = _read_txt(folder / _PERSONA_TXT).strip()
        chunks.append(persona or _FALLBACK_PERSONA)
        extra_names = [
            name
            for name in _sorted_txt_names(
                path.name
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() == ".txt"
            )
            if name not in _SPECIAL_ORDER
        ]
        for name in extra_names:
            body = _read_txt(folder / name).strip()
            if not body:
                continue
            stem = Path(name).stem
            chunks.append(f"【{stem}】\n{body}")
        return chunks

    def _file_path(
        self, persona_id: str, filename: str, *, create_dir: bool = False
    ) -> Path | None:
        """Resolve a txt path inside a pack after validating names."""
        key = normalize_persona_id(persona_id)
        name = normalize_persona_filename(filename)
        if not key or not name:
            return None
        folder = self._personas_dir / key
        if create_dir:
            folder.mkdir(parents=True, exist_ok=True)
        elif not folder.is_dir():
            return None
        return folder / name

    def _migrate_from_json_if_needed(self) -> None:
        """Split bot_prompt.json into 0x01 when that pack's persona.txt is missing."""
        seed = self._personas_dir / _DEFAULT_PACK / _PERSONA_TXT
        if seed.is_file():
            return
        src = self._json_migrate_path
        if src is None or not src.is_file():
            return
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _log.exception("persona migrate failed to read %s", src)
            return
        if not isinstance(data, dict):
            return
        already_indexed = self._has_any_pack()
        folder = self._personas_dir / _DEFAULT_PACK
        folder.mkdir(parents=True, exist_ok=True)
        (folder / _PERSONA_TXT).write_text(
            str(data.get("persona") or "").strip() + "\n", encoding="utf-8"
        )
        (folder / _ANTI_TXT).write_text(
            _lines_to_txt(data.get("anti_injection")), encoding="utf-8"
        )
        (folder / _STAY_TXT).write_text(
            _lines_to_txt(data.get("stay_on_prompt")), encoding="utf-8"
        )
        if already_indexed:
            _log.info("seeded missing persona.txt for pack %s from json", _DEFAULT_PACK)
            return
        self._by_id = {
            _DEFAULT_PACK: PersonaEntry(
                id=_DEFAULT_PACK,
                title="默认",
                path=folder.resolve(),
            )
        }
        self._active = _DEFAULT_PACK
        self._write_index()
        _log.info("migrated bot_prompt.json into persona pack %s", _DEFAULT_PACK)

    def _has_any_pack(self) -> bool:
        """True when toml lists a pack or data/personas already has a subfolder."""
        if self._index_path.is_file():
            try:
                data = tomllib.loads(self._index_path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                data = {}
            rows = data.get("persona") if isinstance(data, dict) else None
            if isinstance(rows, list) and any(isinstance(row, dict) for row in rows):
                return True
        if not self._personas_dir.is_dir():
            return False
        return any(path.is_dir() for path in self._personas_dir.iterdir())

    def _reload_if_changed(self) -> None:
        """Reload when personas.toml or pack folders change."""
        index_mtime = (
            self._index_path.stat().st_mtime if self._index_path.is_file() else -1.0
        )
        folder_stamp = _folder_stamp(self._personas_dir)
        if index_mtime == self._mtime and folder_stamp == self._folder_stamp:
            return
        by_id, active = self._load_index_rows()
        _merge_folder_packs(by_id, self._personas_dir)
        if active not in by_id and by_id:
            active = sorted(by_id)[0]
        self._by_id = by_id
        self._active = active
        self._mtime = index_mtime
        self._folder_stamp = folder_stamp
        _log.info(
            "loaded personas index=%s dir=%s active=%s count=%s",
            self._index_path,
            self._personas_dir,
            self._active,
            len(by_id),
        )

    def _load_index_rows(self) -> tuple[dict[str, PersonaEntry], str]:
        """Parse personas.toml rows that still have folders on disk."""
        by_id: dict[str, PersonaEntry] = {}
        active = ""
        if not self._index_path.is_file():
            return by_id, active
        try:
            data = tomllib.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            _log.exception("failed to load personas index %s", self._index_path)
            return by_id, active
        if not isinstance(data, dict):
            return by_id, active
        active = normalize_persona_id(str(data.get("active") or ""))
        rows = data.get("persona")
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = normalize_persona_id(str(row.get("id") or ""))
            if not key:
                continue
            folder = (self._personas_dir / key).resolve()
            if not folder.is_dir():
                _log.warning("skip persona id=%s missing folder %s", key, folder)
                continue
            title = str(row.get("title") or key).strip() or key
            by_id[key] = PersonaEntry(id=key, title=title, path=folder)
        return by_id, active

    def _write_index(self) -> None:
        """Rewrite personas.toml from the in-memory pack list."""
        lines = [
            "# Persona packs. Text files live under data/personas/<id>/ by default.",
            "# Only active is sent to the model; switch in the admin console.",
            f'active = "{self._active}"',
            "",
        ]
        for key in sorted(self._by_id):
            entry = self._by_id[key]
            title = entry.title.replace("\\", "\\\\").replace('"', '\\"')
            lines.append("[[persona]]")
            lines.append(f'id = "{entry.id}"')
            lines.append(f'title = "{title}"')
            lines.append("")
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _sorted_txt_names(names: list[str] | tuple[str, ...] | object) -> list[str]:
    """Order reserved prompt files first, then the rest alphabetically."""
    unique = sorted({str(name) for name in names})
    head = [name for name in _SPECIAL_ORDER if name in unique]
    tail = [name for name in unique if name not in _SPECIAL_ORDER]
    return head + tail


def _folder_stamp(personas_dir: Path) -> tuple[tuple[str, float], ...]:
    """Snapshot of pack folder names and newest txt mtime."""
    if not personas_dir.is_dir():
        return ()
    items: list[tuple[str, float]] = []
    for path in personas_dir.iterdir():
        if not path.is_dir():
            continue
        newest = path.stat().st_mtime
        for child in path.iterdir():
            if child.is_file() and child.suffix.lower() == ".txt":
                newest = max(newest, child.stat().st_mtime)
        items.append((path.name, newest))
    return tuple(sorted(items))


def _merge_folder_packs(by_id: dict[str, PersonaEntry], personas_dir: Path) -> None:
    """Register subfolders that are not yet listed in personas.toml."""
    if not personas_dir.is_dir():
        return
    for path in sorted(personas_dir.iterdir()):
        if not path.is_dir():
            continue
        key = normalize_persona_id(path.name)
        if not key or key in by_id:
            continue
        by_id[key] = PersonaEntry(id=key, title=key, path=path.resolve())
        _log.info("persona folder scan id=%s", key)


def read_persona_aliases(text: str) -> str:
    """Return the `- aliases:` value from a persona.txt body, or empty."""
    match = _ALIAS_LINE.search(text or "")
    if match is None:
        return ""
    return match.group(2).strip()


def _rewrite_persona_name_lines(path: Path, persona_id: str, aliases: str) -> None:
    """Keep persona.txt's id and aliases lines aligned with the pack names."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    updated = _upsert_persona_id_line(text, persona_id)
    updated = _upsert_alias_line(updated, aliases)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def _upsert_persona_id_line(text: str, persona_id: str) -> str:
    """Replace an existing `- persona_id:` line. Leave files without one unchanged."""
    if not _PERSONA_ID_LINE.search(text):
        return text
    return _PERSONA_ID_LINE.sub(rf"\g<1>`{persona_id}`", text, count=1)


def _upsert_alias_line(text: str, aliases: str) -> str:
    """Replace, clear, or insert one `- aliases:` line."""
    rendered = (aliases or "").strip()
    if _ALIAS_LINE.search(text):
        return _ALIAS_LINE.sub(lambda match: match.group(1) + rendered, text, count=1)
    if not rendered:
        return text
    insert = f"- aliases: {rendered}\n"
    id_line = re.search(r"(?m)^- persona_id:.*\n", text)
    if id_line is not None:
        return text[: id_line.end()] + insert + text[id_line.end() :]
    return insert + (text or "")


def _read_txt(path: Path) -> str:
    """Read utf-8 text or empty when the file is missing."""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _lines_to_txt(value: object) -> str:
    """Join a JSON string array into a newline file body."""
    if not isinstance(value, list):
        return ""
    lines = [str(item).strip() for item in value if str(item).strip()]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
