"""Channel mapping and whitelist handle rules (no live WeChat)."""

from types import SimpleNamespace

from bot.channel import IncomingTurn, should_handle, turns_from_event
from bot.reply import split_bubbles, strip_sticker_markers


def test_turns_from_event_skips_self_and_non_text() -> None:
    """Map added messages; skip time/system kinds; keep from_self for the runner to drop."""
    event = SimpleNamespace(
        chat="文件传输助手",
        unread=1,
        added=[
            SimpleNamespace(
                kind=1,
                text="你好",
                sender="",
                preview_only=True,
                from_self=False,
                id="a",
                chat="",
            ),
            SimpleNamespace(
                kind=1,
                text="我发的",
                sender="",
                preview_only=True,
                from_self=True,
                id="b",
                chat="",
            ),
            SimpleNamespace(
                kind=2,
                text="12:00",
                sender="",
                preview_only=False,
                from_self=False,
                id="c",
                chat="",
            ),
        ],
    )
    turns = turns_from_event(event)
    assert [t.text for t in turns] == ["你好", "我发的"]
    assert turns[1].from_self is True
    assert turns[0].preview_only is True


def test_should_handle_requires_allow_and_drops_self() -> None:
    """from_self and chats outside the whitelist are not handled."""
    allowed = {"文件传输助手"}
    mine = IncomingTurn(chat="文件传输助手", sender="", text="hi", from_self=True)
    other = IncomingTurn(chat="1145", sender="甲", text="hi", from_self=False)
    ok = IncomingTurn(chat="文件传输助手", sender="", text="hi", from_self=False)
    assert should_handle(mine, allowed) is False
    assert should_handle(other, allowed) is False
    assert should_handle(ok, allowed) is True
    assert should_handle(ok, set()) is False


def test_group_session_id_uses_sender() -> None:
    """Group turns key memory by chat plus sender when the preview names one."""
    turn = IncomingTurn(chat="1145", sender="甲", text="不会啊", chat_type="group")
    assert turn.is_group is True
    assert turn.session_id == "1145:甲"


def test_strip_sticker_and_split_bubbles() -> None:
    """[[sticker:]] is removed; --- splits bubbles."""
    raw = "先这样 [[sticker:facepalm]]\n\n---\n\n再说一句"
    assert "sticker" not in strip_sticker_markers(raw)
    assert split_bubbles(raw) == ["先这样", "再说一句"]
