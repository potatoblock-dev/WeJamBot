"""Sender-name plausibility and incoming_turn false-sender scrub."""

from __future__ import annotations

from bot.channel import IncomingTurn, incoming_turn
from bot.eyes import SeenMessage
from bot.sender_name import is_plausible_sender_name, split_preview_sender


def test_rejects_bracket_media_labels() -> None:
    """Link/image preview heads must not become nicknames."""
    assert is_plausible_sender_name("[链接]邀请你加入群聊") is False
    assert is_plausible_sender_name("[图片]") is False
    assert is_plausible_sender_name("https://example.com") is False


def test_accepts_normal_nicknames() -> None:
    """Ordinary display names stay valid senders."""
    assert is_plausible_sender_name("白糖") is True
    assert is_plausible_sender_name("崔欣宇") is True


def test_incoming_turn_drops_false_sender() -> None:
    """Host scrub clears bracket senders so impression_key stays the chat title."""
    turn = incoming_turn(
        SeenMessage(
            chat="白糖",
            text="邀请你加入群聊",
            sender="[链接]邀请你加入群聊",
            chat_type="friend",
        )
    )
    assert turn.sender == ""
    assert turn.impression_key == "白糖"
    assert turn.username == "白糖"


def test_incoming_turn_keeps_real_group_sender() -> None:
    """Real group nicknames remain on the turn."""
    turn = incoming_turn(
        SeenMessage(
            chat="99+",
            text="测试",
            sender="白糖",
            chat_type="group",
        )
    )
    assert turn.sender == "白糖"
    assert turn.impression_key == "99+:白糖"


def test_split_preview_sender_colon_variants() -> None:
    """ASCII/fullwidth colons (with or without space) yield a nickname."""
    assert split_preview_sender("白糖: 你好") == ("白糖", "你好")
    assert split_preview_sender("白糖:你好") == ("白糖", "你好")
    assert split_preview_sender("白糖：你好") == ("白糖", "你好")
    assert split_preview_sender("slh也是吗") == ("", "slh也是吗")


def test_incoming_turn_rescues_nick_from_body() -> None:
    """Host re-splits nick from body when the bridge left sender empty."""
    turn = incoming_turn(
        SeenMessage(
            chat="99+",
            text="违心事:妈的吵得我耳鸣",
            sender="",
            chat_type="group",
            preview=True,
        )
    )
    assert turn.sender == "违心事"
    assert turn.text == "妈的吵得我耳鸣"
    assert turn.impression_key == "99+:违心事"
