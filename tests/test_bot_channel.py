"""Channel mapping, WeJamHands adapter, and text coalescing (no live WeChat)."""

from types import SimpleNamespace

import grpc

from bot.channel import IncomingTurn, incoming_turn, should_handle
from bot.coalesce import TurnCoalescer, merge_turns
from bot.eyes import (
    PHASE_QR_READY,
    HandsRejected,
    HandsUnavailable,
    SeenMessage,
    WeChatEyes,
)
from bot.hands import WeJamHands
from bot.reply import TextSender, split_bubbles, strip_sticker_markers
from wejam.client import PHASE_QR_READY as DRIVER_QR_READY


class _DriverError(grpc.RpcError):
    """Minimal gRPC failure the adapter can translate without a live channel."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        """Return the status the adapter should map."""
        return self._code

    def details(self) -> str:
        """Return the status text."""
        return self._details


class _Driver:
    """In-memory wejam client. Returns driver-shaped objects, not SeenMessage."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.events: list[object] = []
        self.send_result = SimpleNamespace(ok=True, detail="")
        self.chats: list[object] = []
        self.login = SimpleNamespace(phase=DRIVER_QR_READY, detail="scan")
        self.qr = SimpleNamespace(payload="qr-payload", revision="r1")

    def close(self) -> None:
        """No channel to close."""
        return None

    def send_text(self, chat: str, text: str) -> SimpleNamespace:
        """Record a send, or return the canned failure result."""
        self.sent.append((chat, text))
        return self.send_result

    def watch_messages(self, chat: str = "") -> object:
        """Yield canned driver events."""
        return iter(self.events)

    def list_chats(self) -> SimpleNamespace:
        """Return canned session rows."""
        return SimpleNamespace(chats=self.chats)

    def login_state(self) -> SimpleNamespace:
        """Return a canned proto-shaped login state."""
        return self.login

    def qr_code(self) -> SimpleNamespace:
        """Return a canned QR payload."""
        return self.qr


def test_subscribe_hides_driver_events() -> None:
    """Time/system rows stay in the adapter; the brain only sees SeenMessage."""
    driver = _Driver()
    driver.events.append(
        SimpleNamespace(
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
    )
    hands = WeJamHands(client=driver)
    assert isinstance(hands, WeChatEyes)
    seen = list(hands.subscribe())
    assert [item.text for item in seen] == ["你好", "我发的"]
    assert all(isinstance(item, SeenMessage) for item in seen)
    assert seen[1].from_self is True
    assert seen[0].preview is True
    turn = incoming_turn(seen[0])
    assert turn.preview_only is True
    assert turn.text == "你好"


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


def test_merge_turns_joins_unique_lines() -> None:
    """Consecutive duplicate previews collapse; distinct lines stay in order."""
    a = IncomingTurn(chat="文件传输助手", sender="", text="一", preview_only=True, unread=1)
    b = IncomingTurn(chat="文件传输助手", sender="", text="一", preview_only=True, unread=1)
    c = IncomingTurn(chat="文件传输助手", sender="", text="二", preview_only=True, unread=2)
    merged = merge_turns([a, b, c])
    assert merged.text == "一\n二"
    assert merged.unread == 2
    assert merged.preview_only is True


def test_merge_turns_keeps_earlier_image_paths() -> None:
    """Emotion+paths then a text line must not drop the sticker paths."""
    sticker = IncomingTurn(
        chat="文件传输助手",
        sender="",
        text="",
        content_type="emotion",
        image_paths=("/tmp/a.gif",),
    )
    follow = IncomingTurn(
        chat="文件传输助手",
        sender="",
        text="看这个",
        content_type="text",
    )
    merged = merge_turns([sticker, follow])
    assert merged.text == "看这个"
    assert merged.image_paths == ("/tmp/a.gif",)
    assert merged.content_type == "emotion"


def test_coalescer_flush_pending_merges_session() -> None:
    """submit then flush_pending delivers one merged turn per session."""
    got: list[IncomingTurn] = []
    coalescer = TurnCoalescer(got.append, burst_window=30.0, debounce=30.0)
    coalescer.submit(IncomingTurn(chat="文件传输助手", sender="", text="甲"))
    coalescer.submit(IncomingTurn(chat="文件传输助手", sender="", text="乙"))
    coalescer.flush_pending()
    assert len(got) == 1
    assert got[0].text == "甲\n乙"


def test_hands_send_text_uses_client() -> None:
    """send_text returns nothing on success and raises HandsRejected on ok=false."""
    driver = _Driver()
    hands = WeJamHands(client=driver)
    hands.send_text("文件传输助手", "hi")
    assert driver.sent == [("文件传输助手", "hi")]
    sender = TextSender(hands)
    assert sender.send("文件传输助手", "again") is True
    assert driver.sent[-1] == ("文件传输助手", "again")
    driver.send_result = SimpleNamespace(ok=False, detail="busy")
    try:
        hands.send_text("文件传输助手", "nope")
    except HandsRejected as exc:
        assert "busy" in str(exc)
    else:
        raise AssertionError("expected HandsRejected")
    assert sender.send("文件传输助手", "nope") is False


def test_hands_maps_login_qr_and_chats() -> None:
    """Session rows, login phase, and QR payload are plain values."""
    driver = _Driver()
    driver.chats.append(
        SimpleNamespace(name="文件传输助手", last_message="你好", unread_count=2)
    )
    hands = WeJamHands(client=driver)
    chats = hands.list_conversations()
    assert chats[0].name == "文件传输助手"
    assert chats[0].preview == "你好"
    assert chats[0].unread == 2
    status = hands.login_status()
    assert status.phase == PHASE_QR_READY
    assert status.detail == "scan"
    qr = hands.qr_code()
    assert qr.payload == "qr-payload"
    assert qr.revision == "r1"


def test_hands_maps_unavailable() -> None:
    """A down transport becomes HandsUnavailable, not a protobuf status."""

    class Down(_Driver):
        def login_state(self) -> SimpleNamespace:
            """Fail the login read the way a dropped gRPC channel does."""
            raise _DriverError(grpc.StatusCode.UNAVAILABLE, "down")

    hands = WeJamHands(client=Down())
    try:
        hands.login_status()
    except HandsUnavailable as exc:
        assert "down" in str(exc)
    else:
        raise AssertionError("expected HandsUnavailable")


def test_merge_chat_names_keeps_allowed_missing_from_live() -> None:
    """Whitelist names not in the live list still appear for checkboxes."""
    from bot.allowlist import merge_chat_names_for_picker

    merged = merge_chat_names_for_picker(
        ["文件传输助手", "1145"],
        {"旧群", "文件传输助手"},
    )
    assert merged == ["文件传输助手", "1145", "旧群"]


def test_save_allow_chats_roundtrip(tmp_path) -> None:
    """Whitelist file stores one unique display name per line."""
    from bot.allowlist import load_allow_chats, save_allow_chats

    path = tmp_path / "allow_chats.txt"
    save_allow_chats(path, ["1145", "文件传输助手", "1145", "  "])
    assert load_allow_chats(path) == {"1145", "文件传输助手"}


def test_openclaw_read_context_unimplemented_is_soft_empty() -> None:
    """Openclaw aborts ReadContext; hands must return [] instead of raising."""

    class OpenClawish(_Driver):
        def read_context(self, chat: str = "", limit: int = 0) -> SimpleNamespace:
            raise _DriverError(
                grpc.StatusCode.UNIMPLEMENTED,
                "openclaw bridge: ReadContext unsupported",
            )

        def read_latest(self, chat: str = "") -> SimpleNamespace:
            raise _DriverError(
                grpc.StatusCode.UNIMPLEMENTED,
                "openclaw bridge: ReadLatest unsupported",
            )

    hands = WeJamHands(client=OpenClawish())
    assert hands.read_context("文件传输助手", limit=30) == []
    assert hands.read_latest("文件传输助手") is None


def test_subscribe_keeps_empty_emotion_bubbles() -> None:
    """Emotion rows with no text still reach the brain for media capture."""
    driver = _Driver()
    driver.events.append(
        SimpleNamespace(
            chat="文件传输助手",
            unread=0,
            added=[
                SimpleNamespace(
                    kind=1,
                    text="",
                    sender="",
                    preview_only=False,
                    from_self=False,
                    id="emo1",
                    chat="",
                    content_type="emotion",
                    index=3,
                    stable_id="",
                    attachments=(),
                ),
                SimpleNamespace(
                    kind=1,
                    text="",
                    sender="",
                    preview_only=False,
                    from_self=False,
                    id="blank",
                    chat="",
                    content_type="text",
                    index=4,
                    stable_id="",
                    attachments=(),
                ),
            ],
        )
    )
    hands = WeJamHands(client=driver)
    seen = list(hands.subscribe())
    assert len(seen) == 1
    assert seen[0].content_type == "emotion"
    turn = incoming_turn(seen[0])
    assert should_handle(turn, {"文件传输助手"}) is True
