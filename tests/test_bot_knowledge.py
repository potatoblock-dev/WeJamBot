"""Knowledge notebook store: TOC, upsert, section reject, query slices."""

from __future__ import annotations

from pathlib import Path

from bot.knowledge.store import KnowledgeStore
from bot.knowledge.writer import _parse_rows
from bot.prompt_book import PromptBook
from bot.personas.catalog import PersonaCatalog


def test_upsert_rebuilds_toc_and_rejects_unknown_section(tmp_path: Path) -> None:
    """Known sections write ### entries; unknown ## names are refused."""
    path = tmp_path / "notebook.md"
    store = KnowledgeStore(path)
    store.ensure_scaffold()
    assert store.upsert("黑话与梗", "名单有正文空", "群里催评审时的口头禅")
    assert not store.upsert("随便新章节", "x", "y")
    text = path.read_text(encoding="utf-8")
    assert "## 黑话与梗" in text
    assert "### 名单有正文空" in text
    assert "- [名单有正文空]" in text
    assert "随便新章节" not in text


def test_slices_for_returns_hits(tmp_path: Path) -> None:
    """Prompt slice includes TOC and only matching entries."""
    store = KnowledgeStore(tmp_path / "notebook.md")
    store.ensure_scaffold()
    store.upsert("黑话与梗", "名单有正文空", "催评审时说")
    store.upsert("话题与背景", "无关话题", "别的事")
    block = store.slices_for("今天又名单有正文空了")
    assert "名单有正文空" in block
    assert "催评审时说" in block
    # TOC may list every title; only matched bodies go under 【命中词条】.
    assert "别的事" not in block
    assert "【命中词条】" in block


def test_parse_knowledge_rows() -> None:
    """Fixed pipe rows parse; junk lines are dropped."""
    raw = (
        "黑话与梗|名单有正文空|催评审口头禅|high\n"
        "nonsense\n"
        "话题与背景|周会|每周一|low\n"
        "NONE\n"
    )
    rows = _parse_rows(raw)
    assert len(rows) == 2
    assert rows[0][0] == "黑话与梗"
    assert rows[1][3] == "low"


def test_prompt_book_injects_culture_and_knowledge(tmp_path: Path) -> None:
    """System prompt carries group culture and knowledge blocks."""
    personas = tmp_path / "personas" / "default"
    personas.mkdir(parents=True)
    (personas / "persona.txt").write_text("主口吻", encoding="utf-8")
    catalog = PersonaCatalog(tmp_path / "personas.toml", tmp_path / "personas")
    book = PromptBook(catalog)
    text = book.system_text(
        group_culture="群里爱说名单有正文空",
        knowledge="【本地知识库目录】\n- 黑话与梗",
    )
    assert "本群氛围与梗" in text
    assert "名单有正文空" in text
    assert "本地知识库" in text
