"""Offline checks for the WeChat dangerous-click blacklist helper."""

from agent.wechat_info import name_is_dangerous


def test_name_is_dangerous_matches_blacklist() -> None:
    """Labels containing irreversible action words are rejected."""
    assert name_is_dangerous("退出登录") is True
    assert name_is_dangerous("删除聊天") is True
    assert name_is_dangerous("发送") is False
    assert name_is_dangerous("") is False
