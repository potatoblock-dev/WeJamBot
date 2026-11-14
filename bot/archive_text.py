"""Shared markers and prompt fragments for archive writers (reply vs observe)."""

from __future__ import annotations

# Sent to writers on the observe path; never a fabricated bot reply.
OBSERVE_NO_REPLY = "（旁听未回复）"


def is_observe_turn(assistant_text: str) -> bool:
    """True when archive writers should treat this as listen-only (no bot reply)."""
    text = (assistant_text or "").strip()
    return not text or text == OBSERVE_NO_REPLY


def user_bot_prompt_block(
    user_text: str,
    assistant_text: str,
    *,
    user_limit: int = 800,
    assistant_limit: int = 400,
) -> str:
    """Format the turn facts for archive prompts (reply visible or observe-only)."""
    user = (user_text or "")[:user_limit]
    if is_observe_turn(assistant_text):
        return f"用户说：{user}\n（本轮 bot 未回复，仅旁听）\n"
    bot = (assistant_text or "")[:assistant_limit]
    return f"用户说：{user}\nbot 刚回：{bot}\n"
