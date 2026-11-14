"""Extract knowledge rows after a chat turn and upsert into the notebook."""

from __future__ import annotations

import logging
import re

from bot.knowledge.store import SECTION_SET, KnowledgeStore
from bot.llm.types import ArchiveCompleter

_log = logging.getLogger(__name__)

_REWRITE = (
    "根据下面这一轮对话，判断是否有应写入「本地知识库」的词条。"
    "只输出 0～3 行，每行格式严格为：章节|标题|一两句说明|high 或 low"
    "允许的章节只能是：黑话与梗、人物称呼、话题与背景、待补。"
    "high=已从对话确认；low=不确定，宿主会写入待补。"
    "人物称呼只记群内怎么叫谁，禁止隐私（微信号、住址、真实姓名等）。"
    "没有值得记的就输出空行或 NONE。"
    "不要输出 Markdown，不要解释。"
)

_ROW = re.compile(
    r"^(黑话与梗|人物称呼|话题与背景|待补)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(high|low)\s*$",
    re.IGNORECASE,
)


class KnowledgeWriter:
    """Ask the model for fixed-format rows, then let KnowledgeStore write the MD."""

    def __init__(self, llm: ArchiveCompleter, store: KnowledgeStore) -> None:
        self._llm = llm
        self._store = store

    async def after_turn(self, user_text: str, assistant_text: str) -> None:
        """Parse model rows and upsert; ignore failures so chat is not blocked."""
        if not self._llm.is_configured():
            return
        self._store.ensure_scaffold()
        prompt = (
            f"用户说：{user_text[:800]}\n"
            f"你刚回：{assistant_text[:400]}\n"
        )
        try:
            text = await self._llm.complete_plain(_REWRITE, prompt, max_tokens=300)
        except Exception:
            _log.exception("knowledge rewrite failed")
            return
        for section, title, body, confidence in _parse_rows(text):
            force_pending = confidence == "low"
            ok = self._store.upsert(
                section,
                title,
                body,
                force_pending=force_pending,
            )
            if ok:
                _log.info(
                    "knowledge upsert section=%s title=%s confidence=%s",
                    "待补" if force_pending else section,
                    title,
                    confidence,
                )


def _parse_rows(text: str) -> list[tuple[str, str, str, str]]:
    """Parse chapter|title|body|high/low lines; skip junk."""
    rows: list[tuple[str, str, str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip().strip("`")
        if not line or line.upper() == "NONE":
            continue
        match = _ROW.match(line)
        if not match:
            continue
        section, title, body, confidence = match.groups()
        if section not in SECTION_SET:
            continue
        rows.append((section, title.strip(), body.strip(), confidence.lower()))
        if len(rows) >= 3:
            break
    return rows
