"""WeJam gRPC adapter for WeChatEyes. Driver events stay in this module."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import grpc

from bot.eyes import (
    PHASE_LOGGED_IN,
    PHASE_ONE_CLICK_READY,
    PHASE_QR_READY,
    PHASE_STARTING,
    PHASE_UNKNOWN,
    PHASE_UNSPECIFIED,
    PHASE_WAIT_PHONE,
    Conversation,
    HandsError,
    HandsRejected,
    HandsUnavailable,
    LoginQr,
    LoginStatus,
    MediaAttachment,
    SeenMessage,
)
from wejam.client import PHASE_NAME, Client

# Proto phase labels from wejam.client.PHASE_NAME → stable eyes strings.
_PHASE = {
    "UNSPECIFIED": PHASE_UNSPECIFIED,
    "STARTING": PHASE_STARTING,
    "QR_READY": PHASE_QR_READY,
    "ONE_CLICK_READY": PHASE_ONE_CLICK_READY,
    "WAIT_PHONE": PHASE_WAIT_PHONE,
    "LOGGED_IN": PHASE_LOGGED_IN,
    "UNKNOWN": PHASE_UNKNOWN,
}

# MessageKind values the brain is allowed to see: unspecified and text.
_TEXT_KINDS = (0, 1)


class WeJamHands:
    """WeChatEyes backed by wejam.Client. Protobuf objects do not leave this class."""

    def __init__(
        self,
        target: str | None = None,
        timeout: int = 30,
        *,
        client: object | None = None,
    ) -> None:
        """Use an injected client in tests; otherwise open wejam.Client."""
        self._client = client if client is not None else Client(target, timeout=timeout)
        self._lock = threading.RLock()

    def close(self) -> None:
        """Close the gRPC channel."""
        self._client.close()

    def __enter__(self) -> WeJamHands:
        """Enter the context manager without opening a second channel."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the channel when the with-block ends."""
        self.close()

    def list_conversations(self) -> list[Conversation]:
        """Read the session list and drop the protobuf chat rows."""
        with self._lock:
            response = _call(self._client.list_chats)
        rows = list(getattr(response, "chats", ()) or ())
        return [_conversation(row) for row in rows]

    def subscribe(self, chat: str = "") -> Iterator[SeenMessage]:
        """Yield text lines from WatchMessages. Non-text kinds are discarded here."""
        stream = _call(lambda: self._client.watch_messages(chat=chat))
        try:
            for event in stream:
                yield from _messages_from_event(event)
        except grpc.RpcError as exc:
            raise _translate(exc) from exc

    def send_text(self, chat: str, text: str) -> None:
        """Send one bubble. A driver ok=false becomes HandsRejected."""
        with self._lock:
            result = _call(lambda: self._client.send_text(chat, text))
        if not bool(getattr(result, "ok", False)):
            detail = str(getattr(result, "detail", "") or "")
            raise HandsRejected(detail or "send_text failed")

    def login_status(self) -> LoginStatus:
        """Map the proto login phase onto a PHASE_* string."""
        with self._lock:
            state = _call(self._client.login_state)
        label = PHASE_NAME.get(getattr(state, "phase", None), "")
        phase = _PHASE.get(str(label), PHASE_UNKNOWN)
        detail = str(getattr(state, "detail", "") or "")
        return LoginStatus(phase=phase, detail=detail)

    def qr_code(self) -> LoginQr:
        """Copy the decoded QR payload. An empty payload is returned as-is."""
        with self._lock:
            qr = _call(self._client.qr_code)
        return LoginQr(
            payload=str(getattr(qr, "payload", "") or ""),
            revision=str(getattr(qr, "revision", "") or ""),
        )

    def read_latest(self, chat: str = "") -> SeenMessage | None:
        """Newest bubble including sender; soft-empty when the bridge lacks the RPC."""
        with self._lock:
            resp = _call_optional(
                lambda: self._client.read_latest(chat=chat),
                default=None,
            )
        if resp is None:
            return None
        msg = getattr(resp, "message", None)
        if msg is None or not str(getattr(msg, "id", "") or ""):
            return None
        chat_type = str(getattr(resp, "chat_type", "") or "")
        return _seen_message(msg, chat_type=chat_type)

    def read_context(self, chat: str = "", limit: int = 0) -> list[SeenMessage]:
        """Scroll-collect messages; openclaw returns UNIMPLEMENTED → empty list."""
        with self._lock:
            resp = _call_optional(
                lambda: self._client.read_context(chat=chat, limit=limit),
                default=None,
            )
        if resp is None:
            return []
        chat_type = str(getattr(resp, "chat_type", "") or "")
        return [
            _seen_message(row, chat_type=chat_type)
            for row in getattr(resp, "messages", ()) or ()
        ]

    def capture_message_media(
        self,
        chat: str,
        index: int,
        max_frames: int = 0,
        interval_ms: int = 0,
    ) -> list[MediaAttachment]:
        """Crop bubble frames; missing RPC or ok=false becomes HandsRejected."""
        with self._lock:
            resp = _call_optional(
                lambda: self._client.capture_message_media(
                    chat, index, max_frames=max_frames, interval_ms=interval_ms
                ),
                default=None,
            )
        if resp is None:
            raise HandsRejected("capture_message_media unsupported on this bridge")
        if not bool(getattr(resp, "ok", False)):
            detail = str(getattr(resp, "detail", "") or "")
            raise HandsRejected(detail or "capture_message_media failed")
        return [_media(row) for row in getattr(resp, "attachments", ()) or ()]


def _call(fn):
    """Run one driver call and turn a gRPC status into a hands error."""
    try:
        return fn()
    except grpc.RpcError as exc:
        raise _translate(exc) from exc


def _call_optional(fn, *, default):
    """Like _call, but UNIMPLEMENTED (openclaw / old bridges) yields default."""
    try:
        return fn()
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
            return default
        raise _translate(exc) from exc


def _translate(exc: grpc.RpcError) -> HandsError:
    """Map a gRPC status onto HandsUnavailable or HandsRejected."""
    code = exc.code()
    detail = str(exc.details() or "") or code.name
    if code in (
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.CANCELLED,
    ):
        return HandsUnavailable(detail)
    if code in (
        grpc.StatusCode.FAILED_PRECONDITION,
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNIMPLEMENTED,
    ):
        return HandsRejected(detail)
    return HandsError(detail)


def _conversation(row: object) -> Conversation:
    """Copy one driver chat row into a Conversation."""
    return Conversation(
        name=str(getattr(row, "name", "") or ""),
        preview=str(getattr(row, "last_message", "") or ""),
        unread=int(getattr(row, "unread_count", 0) or 0),
    )


def _media(row: object) -> MediaAttachment:
    """Copy one MediaAttachment protobuf row."""
    return MediaAttachment(
        kind=str(getattr(row, "kind", "") or ""),
        path=str(getattr(row, "path", "") or ""),
        sha256=str(getattr(row, "sha256", "") or ""),
        sampled=bool(getattr(row, "sampled", False)),
        frame_index=int(getattr(row, "frame_index", 0) or 0),
    )


def _seen_message(message: object, *, chat_type: str = "", unread: int = 0) -> SeenMessage:
    """Copy one wire Message onto SeenMessage including optional attachments."""
    attachments = tuple(_media(row) for row in getattr(message, "attachments", ()) or ())
    return SeenMessage(
        chat=str(getattr(message, "chat", "") or "").strip(),
        text=str(getattr(message, "text", "") or "").strip(),
        sender=str(getattr(message, "sender", "") or "").strip(),
        from_self=bool(getattr(message, "from_self", False)),
        preview=bool(getattr(message, "preview_only", False)),
        chat_type=chat_type or str(getattr(message, "chat_type", "") or ""),
        unread=unread,
        message_id=str(getattr(message, "id", "") or ""),
        content_type=str(getattr(message, "content_type", "") or "text"),
        index=int(getattr(message, "index", 0) or 0),
        stable_id=str(getattr(message, "stable_id", "") or ""),
        attachments=attachments,
    )


def _messages_from_event(event: object) -> Iterator[SeenMessage]:
    """Copy chat bubbles off a driver MessageEvent. Time and system rows stop here.

    Empty body is kept when content_type is non-text (e.g. emotion) so the brain
    can still try CaptureMessageMedia on bridges that support it.
    """
    chat = str(getattr(event, "chat", "") or "").strip()
    unread = int(getattr(event, "unread", 0) or 0)
    added = list(getattr(event, "added", ()) or ())
    for message in added:
        kind = int(getattr(message, "kind", 1) or 1)
        if kind not in _TEXT_KINDS:
            continue
        seen = _seen_message(message, unread=unread)
        content_type = (seen.content_type or "text").strip().lower() or "text"
        if not seen.text and content_type == "text" and not seen.attachments:
            continue
        msg_chat = seen.chat or chat
        yield SeenMessage(
            chat=msg_chat,
            text=seen.text,
            sender=seen.sender,
            from_self=seen.from_self,
            preview=seen.preview,
            chat_type=seen.chat_type,
            unread=unread,
            message_id=seen.message_id,
            content_type=content_type,
            index=seen.index,
            stable_id=seen.stable_id,
            attachments=seen.attachments,
        )
