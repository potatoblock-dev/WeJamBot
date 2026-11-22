"""Map hands/eyes messages onto channel-agnostic IncomingTurn values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.eyes import MediaAttachment, SeenMessage
from bot.quote_parse import parse_quote_message

# Strip @mentions / zero-width joiners to see if the body is "only ping".
_AT_TOKEN = re.compile(r"@\S+", re.UNICODE)
_ZW = re.compile(r"[\u2005\u200b\u200c\u200d\ufeff]")


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
    quote_text: str = ""
    quote_sender: str = ""

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
        """Body text passed to the model, with an optional quote cite block first."""
        body = self.text.strip()
        cite = self.quote_text.strip()
        if not cite:
            return body
        who = self.quote_sender.strip()
        header = f"【引用·{who}】{cite}" if who else f"【引用】{cite}"
        if not body:
            return header
        return f"{header}\n{body}"

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
        """True when this turn cites someone via quote fields or quote content_type."""
        if (self.content_type or "").strip().lower() == "quote":
            return True
        return bool(self.quote_text.strip())

    def body_without_mentions(self) -> str:
        """Current bubble text with @tokens removed (for thin-body / quote fallback)."""
        text = _ZW.sub("", self.text or "")
        text = _AT_TOKEN.sub(" ", text)
        return " ".join(text.split())


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
    quote_text = str(getattr(message, "quote_text", "") or "").strip()
    quote_sender = str(getattr(message, "quote_sender", "") or "").strip()
    content_type = (message.content_type or "text").strip() or "text"
    # Defense in depth: drop bridge-side false senders like ``[链接]…``.
    if sender and not is_plausible_sender_name(sender):
        sender = ""
    # If the bridge left sender empty, try splitting ``昵称: 正文`` from the body.
    if not sender and text:
        parsed_sender, parsed_text = split_preview_sender(text)
        if parsed_sender:
            sender, text = parsed_sender, parsed_text
    # Host-side parse when the bridge only forwarded flat multi-line text.
    if not quote_text:
        parts = parse_quote_message(message.text) or parse_quote_message(text)
        if parts is not None:
            content_type = "quote"
            quote_text = parts.quote_text
            quote_sender = parts.quote_sender or quote_sender
            if parts.body.strip():
                text = parts.body.strip()
    return IncomingTurn(
        chat=message.chat.strip(),
        sender=sender,
        text=text,
        preview_only=message.preview,
        from_self=message.from_self,
        chat_type=message.chat_type,
        msg_id=message.message_id,
        unread=message.unread,
        content_type=content_type,
        index=int(message.index or 0),
        image_paths=paths,
        quote_text=quote_text,
        quote_sender=quote_sender,
    )


def should_handle(turn: IncomingTurn, allowed: set[str]) -> bool:
    """True when the runner should pass this turn to the chat brain."""
    if turn.from_self:
        return False
    content_type = (turn.content_type or "text").strip().lower() or "text"
    has_body = (
        bool(turn.text.strip())
        or bool(turn.quote_text.strip())
        or bool(turn.image_paths)
        or content_type != "text"
    )
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
    has_body = (
        bool(turn.text.strip())
        or bool(turn.quote_text.strip())
        or bool(turn.image_paths)
        or content_type != "text"
    )
    if not has_body:
        return False
    chat = turn.chat.strip()
    if not chat or not observe or chat not in observe:
        return False
    if chat in allowed:
        return False
    return True
