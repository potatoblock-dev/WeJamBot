"""Host-side WeChat hands/eyes contract.

The brain imports only this module. Implementations (WeJam gRPC, a fake)
translate their own driver types into the dataclasses below. This module
does not import protobuf, grpc, or wejam.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Login phases. Stable strings, not proto enum integers.
PHASE_UNSPECIFIED = "unspecified"
PHASE_STARTING = "starting"
PHASE_QR_READY = "qr_ready"
PHASE_ONE_CLICK_READY = "one_click_ready"
PHASE_WAIT_PHONE = "wait_phone"
PHASE_LOGGED_IN = "logged_in"
PHASE_UNKNOWN = "unknown"


class HandsError(Exception):
    """A hands/eyes call failed. The message is safe to log."""


class HandsUnavailable(HandsError):
    """The WeChat process or its transport cannot be reached."""


class HandsRejected(HandsError):
    """The driver refused the call (send failed, or the UI precondition failed)."""


@dataclass(frozen=True)
class Conversation:
    """One session-list row. preview is the last-message snippet."""

    name: str
    preview: str = ""
    unread: int = 0


@dataclass(frozen=True)
class MediaAttachment:
    """Local media file path returned by Callus capture (optional on SeenMessage)."""

    kind: str
    path: str
    sha256: str = ""
    sampled: bool = False
    frame_index: int = 0


@dataclass(frozen=True)
class SeenMessage:
    """One text line the eyes observed.

    preview is true when text may be truncated (global watch).
    from_self is true when this process sent the line.
    chat_type is friend/group/official/service when the driver knows it;
    global preview often leaves it empty, and the brain then treats a
    non-empty sender as a group.
    """

    chat: str
    text: str
    sender: str = ""
    from_self: bool = False
    preview: bool = False
    chat_type: str = ""
    unread: int = 0
    message_id: str = ""
    content_type: str = "text"
    index: int = 0
    stable_id: str = ""
    attachments: tuple[MediaAttachment, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LoginStatus:
    """Current login phase as one of the PHASE_* strings, plus driver detail."""

    phase: str
    detail: str = ""


@dataclass(frozen=True)
class LoginQr:
    """Decoded QR contents. An empty payload means there is nothing to show."""

    payload: str
    revision: str = ""


@runtime_checkable
class WeChatEyes(Protocol):
    """Read and write WeChat. Swap the implementation; the brain stays put."""

    def list_conversations(self) -> list[Conversation]:
        """Return the session list. Raises HandsUnavailable if the driver is down."""

    def subscribe(self, chat: str = "") -> Iterator[SeenMessage]:
        """Yield new text lines. Empty chat is the global preview watch.

        Blocks until the stream ends. Raises HandsUnavailable when the
        transport drops. Time and system rows never appear here.
        """

    def send_text(self, chat: str, text: str) -> None:
        """Send one text bubble.

        Raises HandsRejected when the driver reports failure, and
        HandsUnavailable when the transport is down.
        """

    def login_status(self) -> LoginStatus:
        """Return the current login phase. Raises HandsUnavailable if unreachable."""

    def qr_code(self) -> LoginQr:
        """Return the current QR payload. Empty payload is not an error."""

    def close(self) -> None:
        """Release the underlying connection."""
