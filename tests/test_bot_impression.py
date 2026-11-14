"""ImpressionStore path hashing keeps distinct Chinese session keys apart."""

from pathlib import Path

from bot.impression.store import ImpressionStore


def test_chinese_keys_get_distinct_paths(tmp_path: Path) -> None:
    """Two different Chinese session ids must not collapse to one JSON file."""
    store = ImpressionStore(tmp_path / "impressions")
    a = "群A:甲"
    b = "群B:乙"
    path_a = store.path_for(a)
    path_b = store.path_for(b)
    assert path_a != path_b
    store.touch(a, username="甲")
    store.touch(b, username="乙")
    assert path_a.is_file()
    assert path_b.is_file()
    assert store.load(a)["username"] == "甲"
    assert store.load(b)["username"] == "乙"
