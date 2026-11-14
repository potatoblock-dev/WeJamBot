"""Map hands/eyes messages onto channel-agnostic IncomingTurn values."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.eyes import MediaAttachment, SeenMessage


@dataclass(frozen=True)
class IncomingTurn:
    """One inbound WeChat line (0x01 IncomingMessage analogue; text only)."""

    chat: str
    sender: str
    text: str
    preview_only: bool = False
    from_self: bool = False
    chat_type: str = ""
    msg_id: str = ""
    unread: int = 0
    content_type: str = "text"
    index: int = 0
    image_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_group(self) -> bool:
        """True for group chats; friend/single/official/service are private."""
        kind = (self.chat_type or "").strip().lower()
        if kind in {"group", "chatroom"}:
            return True
        if kind in {"friend", "single", "private", "c2c", "official", "service"}:
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
        """Legacy display-name key (``群名:昵称``); ChatBot maps this via IdentityDirectory.

        GUI has no wxid. Runtime storage uses host ``person_id``; renames need a
        manual merge in the admin console so aliases point at one person.
        """
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


def incoming_turn(message: SeenMessage) -> IncomingTurn:
    """Copy one plain SeenMessage into the brain's turn. No driver fields."""
    from bot.sender_name import is_plausible_sender_name, split_preview_sender

    paths = tuple(
        item.path
        for item in (message.attachments or ())
        if isinstance(item, MediaAttachment) and item.path
    )
    sender = message.sender.strip()
    text = message.text.strip()
    # Defense in depth: drop bridge-side false senders like ``[链接]…``.
    if sender and not is_plausible_sender_name(sender):
        sender = ""
    # If the bridge left sender empty, try splitting ``昵称: 正文`` from the body.
    if not sender and text:
        parsed_sender, parsed_text = split_preview_sender(text)
        if parsed_sender:
            sender, text = parsed_sender, parsed_text
    return IncomingTurn(
        chat=message.chat.strip(),
        sender=sender,
        text=text,
        preview_only=message.preview,
        from_self=message.from_self,
        chat_type=message.chat_type,
        msg_id=message.message_id,
        unread=message.unread,
        content_type=(message.content_type or "text").strip() or "text",
        index=int(message.index or 0),
        image_paths=paths,
    )


def should_handle(turn: IncomingTurn, allowed: set[str]) -> bool:
    """True when the runner should pass this turn to the chat brain."""
    if turn.from_self:
        return False
    content_type = (turn.content_type or "text").strip().lower() or "text"
    has_body = bool(turn.text.strip()) or bool(turn.image_paths) or content_type != "text"
    if not has_body:
        return False
    if not allowed:
        return False
    return turn.chat in allowed


def should_observe(
    turn: IncomingTurn, observe: set[str], allowed: set[str]
) -> bool:
    """True when the runner should archive-only (not reply) for this turn.

    Chats also on the reply allowlist are never observed here — reply path wins.
    """
    if turn.from_self:
        return False
    content_type = (turn.content_type or "text").strip().lower() or "text"
    has_body = bool(turn.text.strip()) or bool(turn.image_paths) or content_type != "text"
    if not has_body:
        return False
    chat = turn.chat.strip()
    if not chat or not observe or chat not in observe:
        return False
    if chat in allowed:
        return False
    return True
