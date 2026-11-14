"""Offline checks for update-changelog close-button selection."""

from agent.update_dialog import pick_update_close_rect


def test_pick_update_close_skips_window_chrome() -> None:
    """Window-title 关闭 comes first; only the post-changelog one is used."""
    nodes = [
        ("push button", "最大化", (900, 100, 44, 32)),
        ("push button", "关闭", (1009, 113, 44, 32)),  # chrome — must skip
        ("frame", "微信", (0, 0, 1280, 900)),
        ("label", "微信 4.1.13", (400, 250, 200, 24)),
        ("label", "- 支持语音输入", (400, 280, 300, 80)),
        ("push button", "关闭", (848, 212, 44, 32)),  # dialog
    ]
    assert pick_update_close_rect(nodes) == (848, 212, 44, 32)


def test_pick_update_close_ignores_zero_rect() -> None:
    """Zero-size a11y extents are not clickable targets."""
    nodes = [
        ("label", "微信 4.1.13", (400, 250, 200, 24)),
        ("push button", "关闭", (0, 0, 0, 0)),
        ("push button", "关闭", (848, 212, 44, 32)),
    ]
    assert pick_update_close_rect(nodes) == (848, 212, 44, 32)


def test_pick_update_close_absent_without_title() -> None:
    """No version title → do not click any 关闭."""
    nodes = [
        ("push button", "关闭", (1009, 113, 44, 32)),
        ("label", "会话", (10, 10, 40, 20)),
    ]
    assert pick_update_close_rect(nodes) is None
