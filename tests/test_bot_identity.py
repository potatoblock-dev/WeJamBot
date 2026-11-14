"""IdentityDirectory resolve / merge / legacy impression migration."""

from __future__ import annotations

from pathlib import Path

from bot.identity.directory import IdentityDirectory
from bot.impression.store import ImpressionStore


def test_resolve_stable_for_same_nick(tmp_path: Path) -> None:
    """Same chat+nick always resolves to one person_id."""
    impressions = ImpressionStore(tmp_path / "impressions")
    identity = IdentityDirectory(tmp_path / "aliases.json", impressions)
    a = identity.resolve("99+", "白糖")
    b = identity.resolve("99+", "白糖")
    assert a == b
    assert identity.lookup("99+", "违心事") is None


def test_merge_moves_aliases_and_impression(tmp_path: Path) -> None:
    """After merge, old nick resolves to the target person and source file is gone."""
    impressions = ImpressionStore(tmp_path / "impressions")
    identity = IdentityDirectory(tmp_path / "aliases.json", impressions)
    old_id = identity.resolve("99+", "旧昵称")
    new_id = identity.resolve("99+", "新昵称")
    impressions.touch(old_id, username="旧昵称")
    impressions.save(
        {
            "user_key": old_id,
            "username": "旧昵称",
            "impression": "旧印象正文",
        }
    )
    impressions.touch(new_id, username="新昵称")
    impressions.save(
        {
            "user_key": new_id,
            "username": "新昵称",
            "impression": "新印象正文",
        }
    )
    identity.merge(old_id, new_id)
    assert identity.resolve("99+", "旧昵称") == new_id
    assert identity.resolve("99+", "新昵称") == new_id
    merged = impressions.load(new_id)
    assert "新印象正文" in str(merged.get("impression") or "")
    assert "旧印象正文" in str(merged.get("impression") or "")
    assert not impressions.path_for(old_id).is_file()


def test_migrate_legacy_group_key(tmp_path: Path) -> None:
    """Legacy ``群:昵称`` files become person_id + alias rows."""
    impressions = ImpressionStore(tmp_path / "impressions")
    impressions.save(
        {
            "user_key": "99+:白糖",
            "username": "白糖",
            "impression": "旁听备忘",
        }
    )
    identity = IdentityDirectory(tmp_path / "aliases.json", impressions)
    person_id = identity.resolve("99+", "白糖")
    record = impressions.load(person_id)
    assert record.get("impression") == "旁听备忘"
    assert not impressions.path_for("99+:白糖").is_file()


def test_unmerged_rename_stays_two_persons(tmp_path: Path) -> None:
    """Without merge, a rename yields two distinct person ids."""
    impressions = ImpressionStore(tmp_path / "impressions")
    identity = IdentityDirectory(tmp_path / "aliases.json", impressions)
    a = identity.resolve("99+", "甲")
    b = identity.resolve("99+", "乙")
    assert a != b
