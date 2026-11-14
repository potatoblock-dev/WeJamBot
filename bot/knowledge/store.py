"""Host-owned knowledge notebook: fixed ## sections, ### entries, rebuilt TOC."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# Top-level ## headings the model may fill; 目录 is host-owned.
SECTION_ORDER = ("黑话与梗", "人物称呼", "话题与背景", "待补")
SECTION_SET = frozenset(SECTION_ORDER)
MAX_BYTES = 80 * 1024
_ENTRY_CAP = 400
_TITLE_CAP = 40

_HEADING2 = re.compile(r"^##\s+(.+?)\s*$")
_HEADING3 = re.compile(r"^###\s+(.+?)\s*$")


@dataclass(frozen=True)
class KnowledgeEntry:
    """One ### term under a fixed ## section."""

    section: str
    title: str
    body: str


class KnowledgeStore:
    """Read/write data/knowledge/notebook.md with structure validation."""

    def __init__(self, notebook_path: Path) -> None:
        self._path = notebook_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Absolute path to the single notebook file."""
        return self._path

    def ensure_scaffold(self, seed: Path | None = None) -> None:
        """Create notebook.md from seed or a built-in skeleton when missing."""
        if self._path.is_file():
            return
        if seed is not None and seed.is_file():
            self._path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
            return
        self._path.write_text(_skeleton(), encoding="utf-8")

    def list_entries(self) -> list[KnowledgeEntry]:
        """Parse every ### entry under known ## sections."""
        text = self._read()
        return _parse_entries(text)

    def toc_block(self) -> str:
        """Return only the ## 目录 section body for prompt injection."""
        text = self._read()
        toc = _section_body(text, "目录")
        if toc.strip():
            return "【本地知识库目录】\n" + toc.strip()
        lines = ["【本地知识库目录】"]
        for section in SECTION_ORDER:
            lines.append(f"- {section}")
            for entry in self.list_entries():
                if entry.section == section:
                    lines.append(f"  - {entry.title}")
        return "\n".join(lines)

    def slices_for(self, query: str, *, max_entries: int = 6) -> str:
        """Return TOC plus ### bodies whose title/body overlap the query text."""
        q = (query or "").strip()
        entries = self.list_entries()
        hits: list[KnowledgeEntry] = []
        if q:
            for entry in entries:
                if _matches(entry, q):
                    hits.append(entry)
                if len(hits) >= max_entries:
                    break
        parts = [self.toc_block()]
        if hits:
            parts.append("【命中词条】")
            for entry in hits:
                parts.append(f"### {entry.title}（{entry.section}）\n{entry.body}")
        else:
            parts.append("【命中词条】无。不懂的先看目录，仍不懂就短问群友，勿装懂。")
        return "\n\n".join(parts)

    def upsert(
        self,
        section: str,
        title: str,
        body: str,
        *,
        force_pending: bool = False,
    ) -> bool:
        """Insert or replace one ### entry; rebuild TOC. False when rejected."""
        section = section.strip()
        title = " ".join(title.split())[:_TITLE_CAP].strip()
        body = body.strip()[:_ENTRY_CAP]
        if force_pending:
            section = "待补"
        if section not in SECTION_SET:
            _log.warning("knowledge reject unknown section=%r", section)
            return False
        if not title or not body:
            return False
        if self._path.is_file() and self._path.stat().st_size >= MAX_BYTES:
            _log.warning("knowledge notebook at size cap; skip upsert title=%s", title)
            return False
        entries = [
            e for e in self.list_entries() if not (e.section == section and e.title == title)
        ]
        entries.append(KnowledgeEntry(section=section, title=title, body=body))
        self._write_entries(entries)
        return True

    def _read(self) -> str:
        """Load notebook text or empty string."""
        if not self._path.is_file():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _write_entries(self, entries: list[KnowledgeEntry]) -> None:
        """Rewrite the whole notebook from entries with a fresh TOC."""
        by_section: dict[str, list[KnowledgeEntry]] = {s: [] for s in SECTION_ORDER}
        for entry in entries:
            if entry.section in by_section:
                by_section[entry.section].append(entry)
        lines = [
            "# 本地知识库",
            "",
            "宿主维护结构；模型只通过回合后写入填充词条，禁止新增顶级章节。",
            "",
            "## 目录",
            "",
        ]
        for section in SECTION_ORDER:
            lines.append(f"- [{section}](#{_anchor(section)})")
            for entry in by_section[section]:
                lines.append(f"  - [{entry.title}](#{_anchor(entry.title)})")
        lines.append("")
        for section in SECTION_ORDER:
            lines.append(f"## {section}")
            lines.append("")
            rows = by_section[section]
            if not rows:
                lines.append("（尚无词条）")
                lines.append("")
                continue
            for entry in rows:
                lines.append(f"### {entry.title}")
                lines.append("")
                lines.append(entry.body)
                lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        if len(text.encode("utf-8")) > MAX_BYTES:
            _log.warning("knowledge rewrite would exceed size cap; abort")
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(text, encoding="utf-8")


def _skeleton() -> str:
    """Built-in empty notebook when the example seed is missing."""
    lines = [
        "# 本地知识库",
        "",
        "宿主维护结构；模型只通过回合后写入填充词条，禁止新增顶级章节。",
        "",
        "## 目录",
        "",
    ]
    for section in SECTION_ORDER:
        lines.append(f"- [{section}](#{_anchor(section)})")
    lines.append("")
    for section in SECTION_ORDER:
        lines.extend([f"## {section}", "", "（尚无词条）", ""])
    return "\n".join(lines)


def _anchor(title: str) -> str:
    """GitHub-ish anchor for TOC links (display only; not used for lookup)."""
    return re.sub(r"\s+", "-", title.strip().lower())


def _parse_entries(text: str) -> list[KnowledgeEntry]:
    """Walk markdown and collect ### under known ## sections."""
    entries: list[KnowledgeEntry] = []
    section = ""
    title = ""
    body_lines: list[str] = []

    def flush() -> None:
        """Commit the open ### entry if it sits under a known section."""
        nonlocal title, body_lines
        if section in SECTION_SET and title:
            body = "\n".join(body_lines).strip()
            if body and body != "（尚无词条）":
                entries.append(KnowledgeEntry(section=section, title=title, body=body))
        title = ""
        body_lines = []

    for raw in text.splitlines():
        h2 = _HEADING2.match(raw)
        if h2:
            flush()
            section = h2.group(1).strip()
            continue
        h3 = _HEADING3.match(raw)
        if h3:
            flush()
            title = h3.group(1).strip()
            body_lines = []
            continue
        if title:
            body_lines.append(raw)
    flush()
    return entries


def _section_body(text: str, heading: str) -> str:
    """Return lines under ## heading until the next ##."""
    lines = text.splitlines()
    collecting = False
    out: list[str] = []
    for raw in lines:
        h2 = _HEADING2.match(raw)
        if h2:
            if collecting:
                break
            collecting = h2.group(1).strip() == heading
            continue
        if collecting:
            out.append(raw)
    return "\n".join(out)


def _matches(entry: KnowledgeEntry, query: str) -> bool:
    """True when the query mentions the entry title or a chunk of its body."""
    if entry.title and entry.title in query:
        return True
    if entry.title and len(entry.title) >= 2:
        for i in range(len(entry.title) - 1):
            if entry.title[i : i + 2] in query:
                return True
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", entry.body):
        if token in query:
            return True
    return False
