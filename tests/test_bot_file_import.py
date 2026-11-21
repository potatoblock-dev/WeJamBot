"""Admin archive file import (persona / impression / group culture)."""

from __future__ import annotations

import json
from pathlib import Path

from bot.admin import file_import, store


def test_import_persona_txt_creates_pack(tmp_path: Path, monkeypatch) -> None:
    """A plain txt becomes a persona pack with persona.txt."""
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PERSONAS_DIR", str(tmp_path / "data" / "personas"))
    # reload settings path via fresh calls inside store
    src = tmp_path / "cool_bot.txt"
    src.write_text("【身份】测试搭子\n", encoding="utf-8")
    imported, skipped, errors = file_import.import_persona_paths([src])
    assert imported == 1 and skipped == 0 and errors == []
    packs = {row["id"] for row in store.list_persona_packs()}
    assert "cool_bot" in packs
    assert "测试搭子" in store.read_persona_file("cool_bot", "persona.txt")


def test_import_persona_chinese_stem_with_override(tmp_path: Path, monkeypatch) -> None:
    """Chinese filenames import when the UI supplies id + title."""
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PERSONAS_DIR", str(tmp_path / "data" / "personas"))
    src = tmp_path / "白糖.md"
    src.write_text("【身份】白糖\n", encoding="utf-8")
    assert file_import.persona_import_needs_identity(src)
    suggested_id, suggested_title = file_import.default_persona_identity(src)
    assert suggested_title == "白糖"
    assert suggested_id.startswith("persona_")
    imported, skipped, errors = file_import.import_persona_paths(
        [src],
        pack_overrides={str(src.resolve()): ("baitang", "白糖")},
    )
    assert imported == 1 and skipped == 0 and errors == []
    packs = {row["id"]: row for row in store.list_persona_packs()}
    assert "baitang" in packs
    assert packs["baitang"]["title"] == "白糖"
    assert "白糖" in store.read_persona_file("baitang", "persona.txt")


def test_import_impression_txt(tmp_path: Path, monkeypatch) -> None:
    """Plain text impressions use the stem as username."""
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "白糖.txt"
    src.write_text("爱抬杠\n", encoding="utf-8")
    imported, skipped, errors = file_import.import_impression_paths([src])
    assert imported == 1 and skipped == 0
    rows = store.list_impressions()
    assert any(str(r.get("username") or "") == "白糖" for r in rows)


def test_import_group_culture_txt(tmp_path: Path, monkeypatch) -> None:
    """Text file stem becomes the WeChat chat title."""
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "0x02测试.txt"
    src.write_text("爱玩梗\n", encoding="utf-8")
    imported, skipped, errors = file_import.import_group_culture_paths([src])
    assert imported == 1 and skipped == 0 and errors == []
    rec = store.load_group_culture("0x02测试")
    assert "爱玩梗" in str(rec.get("culture") or "")


def test_import_impression_json_still_works(tmp_path: Path, monkeypatch) -> None:
    """JSON with user_key keeps working."""
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "one.json"
    src.write_text(
        json.dumps(
            {"user_key": "person-1", "username": "甲", "impression": "短"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported, skipped, errors = file_import.import_impression_paths([src])
    assert imported == 1 and skipped == 0
    assert store.load_impression("person-1").get("impression") == "短"
