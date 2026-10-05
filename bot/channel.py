"""Map wejam WatchMessages events onto channel-agnostic IncomingTurn values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingTurn:
    """One inbound WeChat line the chat brain can handle."""

    chat: str
    sender: str
    text: str
    preview_only: bool = False
    from_self: bool = False
    chat_type: str = ""
    msg_id: str = ""
    unread: int = 0

    @property
    def is_group(self) -> bool:
        """True for group chats; friend/official/service are private."""
        kind = (self.chat_type or "").strip().lower()
        if kind == "group":
            return True
        if kind in {"friend", "official", "service"}:
            return False
        return bool(self.sender.strip())

    @property
    def session_id(self) -> str:
        """Memory key: chat name, plus sender when the preview names one."""
        chat = self.chat.strip()
        sender = self.sender.strip()
        if sender:
            return f"{chat}:{sender}"
        return chat

    @property
    def impression_key(self) -> str:
        """Impression file key: same as session_id (no stable WeChat user id)."""
        return self.session_id

    @property
    def user_text(self) -> str:
        """Body text passed to the model."""
        return self.text

    @property
    def username(self) -> str:
        """Display name: sender in groups, otherwise the chat title."""
        return self.sender.strip() or self.chat.strip()

    @property
    def group_id(self) -> str:
        """Chat name used as the group id for unmentioned rate limits."""
        return self.chat.strip() if self.is_group else ""

    @property
    def quotes_bot(self) -> bool:
        """WeChat global watch has no quote event; always false in v1."""
        return False


def turns_from_event(event: object) -> list[IncomingTurn]:
    """Convert a wejam MessageEvent (or test double) into IncomingTurn rows."""
    chat = str(getattr(event, "chat", "") or "").strip()
    unread = int(getattr(event, "unread", 0) or 0)
    added = list(getattr(event, "added", ()) or ())
    turns: list[IncomingTurn] = []
    for message in added:
        kind = int(getattr(message, "kind", 1) or 1)
        if kind not in (0, 1):
            continue
        text = str(getattr(message, "text", "") or "").strip()
        if not text:
            continue
        msg_chat = str(getattr(message, "chat", "") or "").strip() or chat
        turns.append(
            IncomingTurn(
                chat=msg_chat,
                sender=str(getattr(message, "sender", "") or "").strip(),
                text=text,
                preview_only=bool(getattr(message, "preview_only", False)),
                from_self=bool(getattr(message, "from_self", False)),
                chat_type=str(getattr(message, "chat_type", "") or ""),
                msg_id=str(getattr(message, "id", "") or ""),
                unread=unread,
            )
        )
    return turns


def should_handle(turn: IncomingTurn, allowed: set[str]) -> bool:
    """True when the runner should pass this turn to the chat brain."""
    if turn.from_self:
        return False
    if not turn.text.strip():
        return False
    if not allowed:
        return False
    return turn.chat in allowed
