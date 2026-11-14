"""Logged-in account helpers: controlled logout and a light account summary.

Logout is a first-class action (not InvokeNode). The dangerous-button
blacklist still blocks blind clicks; this module opens Settings and
confirms 「退出登录」 on purpose.
"""

from __future__ import annotations

import time

from . import badges, chat, geometry, wechat_info
from . import input as inputmod
from .ui import UI_LOCK, synchronized


def _visible_texts() -> list[str]:
    """Collect visible accessibility names/texts for navigation checks."""
    from .accessibility import walk_all

    out: list[str] = []
    for node in walk_all(only_visible=True):
        for text in (node.get("name"), node.get("text")):
            if text and str(text).strip():
                out.append(str(text).strip())
    return out


def _click_label(role: str, name: str) -> bool:
    """Click a visible control by role + name substring."""
    rect = geometry.node_physical_rect(role, name)
    if rect is None:
        return False
    x, y, w, h = rect
    inputmod.click_at(x + w // 2, y + h // 2)
    return True


def _open_settings() -> bool:
    """Open the WeChat settings panel from the main chrome."""
    texts = _visible_texts()
    if any(t == "设置" for t in texts) or any("关于微信" in t for t in texts):
        return True
    if not any(t == "设置" for t in texts):
        if not _click_label("push button", "更多"):
            return False
        time.sleep(0.8)
    if not _click_label("push button", "设置"):
        return False
    time.sleep(1.2)
    return True


def logout() -> tuple[bool, str]:
    """Sign out of the current WeChat account via Settings → 退出登录 → 确定.

    Returns (ok, detail). Callers should re-read login phase afterwards.
    """
    with UI_LOCK:
        wechat_info._clear_confirm_dialogs()
        if not _open_settings():
            return False, "打不开设置面板，无法退出登录"
        # Exact intentional click; not available through InvokeNode.
        if not _click_label("push button", "退出登录"):
            for _ in range(2):
                inputmod.tap_key("Escape")
                time.sleep(0.4)
            return False, "设置里没有「退出登录」按钮"
        time.sleep(1.0)
        texts = _visible_texts()
        if "确定" in texts or "取消" in texts:
            if not _click_label("push button", "确定"):
                inputmod.tap_key("Escape")
                return False, "弹出了确认框但点不到「确定」"
            time.sleep(1.2)
        else:
            # Some builds dismiss without a second dialog.
            time.sleep(0.8)
        for _ in range(2):
            inputmod.tap_key("Escape")
            time.sleep(0.3)
        wechat_info._clear_confirm_dialogs()
        return True, "已确认退出登录"


@synchronized
def account_hint() -> str:
    """Best-effort display name from the open 「更多」 strip; may be empty."""
    texts = _visible_texts()
    # Prefer an explicit nickname label near the profile entry when present.
    skip = {
        "更多", "设置", "退出", "登录", "微信", "通讯录", "发现", "我",
        "搜索", "关于微信", "通用", "消息通知", "文件传输助手",
    }
    for text in texts:
        if text in skip or len(text) > 32:
            continue
        if any(bad in text for bad in ("版本", "设置", "退出", "取消", "确定")):
            continue
        # Heuristic: short non-nav labels near the more menu.
        if 1 < len(text) <= 24 and not text.isdigit():
            # Only trust when settings/more chrome is visible.
            if any(t in texts for t in ("设置", "更多", "关于微信")):
                return text
    return ""


def build_summary(*, phase: int, detail: str, since_ms: int, uptime_ms: int,
                  logged_in: bool) -> dict:
    """Collect host-facing account fields without opening Settings."""
    version, _source = wechat_info.detect()
    chats = []
    unread_chats = 0
    unread_messages = 0
    hint = ""
    if logged_in:
        try:
            chats = chat.list_chats()
        except Exception:
            chats = []
        try:
            _items, unread_chats, unread_messages = badges.scan_badges()
        except Exception:
            unread_chats = sum(1 for c in chats if int(c.get("unread_count") or 0) > 0)
            unread_messages = sum(int(c.get("unread_count") or 0) for c in chats)
        try:
            hint = account_hint()
        except Exception:
            hint = ""
    return {
        "logged_in": logged_in,
        "wechat_version": version or "",
        "login_phase": phase,
        "login_detail": detail or "",
        "login_since_unix_ms": since_ms,
        "uptime_ms": uptime_ms,
        "chat_count": len(chats),
        "unread_chats": unread_chats,
        "unread_messages": unread_messages,
        "account_hint": hint,
    }
