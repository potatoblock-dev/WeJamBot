"""Hot-reloadable reply gates: who/what to answer, and how often."""

from __future__ import annotations

import logging
import re
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.channel import IncomingTurn

_log = logging.getLogger(__name__)
_BEIJING = ZoneInfo("Asia/Shanghai")

SPEAK_AUTO = "auto"
SPEAK_ALL = "all"
SPEAK_MENTION_QUOTE = "mention_quote"
SPEAK_MODES = (SPEAK_AUTO, SPEAK_ALL, SPEAK_MENTION_QUOTE)
SPEAK_MODE_LABELS = {
    SPEAK_AUTO: "自动",
    SPEAK_ALL: "全部尝试回复",
    SPEAK_MENTION_QUOTE: "仅@和引用回复",
}
_SPEAK_ASSIGN = re.compile(r'(?m)^(\s*speak_mode\s*=\s*)(["\']).*?\2')
_CONTEXT_LIMIT_ASSIGN = re.compile(r"(?m)^(\s*context_message_limit\s*=\s*)(\d+)")
_EVOLUTION_AUTO_ASSIGN = re.compile(
    r"(?m)^(\s*prompt_evolution_auto\s*=\s*)(true|false)", re.IGNORECASE
)
_EVOLUTION_MESSAGES_ASSIGN = re.compile(
    r"(?m)^(\s*prompt_evolution_min_messages\s*=\s*)(\d+)"
)
# Legacy time-based key; still rewritten away when saving the new message threshold.
_EVOLUTION_INTERVAL_ASSIGN = re.compile(
    r"(?m)^(\s*prompt_evolution_min_interval_seconds\s*=\s*)(\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class ReplySettings:
    """Reply switches and rate limits. Edit reply_policy.toml; reload is automatic."""

    enabled: bool = True
    c2c: bool = True
    group: bool = True
    on_text: bool = True
    on_image: bool = True
    on_voice: bool = True
    min_interval_seconds: float = 12.0
    max_per_session_per_minute: int = 3
    require_keywords: tuple[str, ...] = ()
    skip_keywords: tuple[str, ...] = ()
    group_unmentioned: bool = True
    unmentioned_cooldown_seconds: float = 5.0
    unmentioned_need_hook: bool = True
    bot_names: tuple[str, ...] = ()
    unmentioned_thread_minutes: float = 30.0
    unmentioned_off_peak_only: bool = True
    speak_mode: str = SPEAK_AUTO
    group_culture_auto: bool = True
    knowledge_auto: bool = True
    # Auto-draft per-group prompt evolution (pending until console approve).
    prompt_evolution_auto: bool = False
    # Min inbound messages the bot must receive in a group before the next proposal.
    prompt_evolution_min_messages: int = 50
    # How many chat bubbles to scroll-load / take from transcript before a reply.
    context_message_limit: int = 30
    # Within that window, mark the newest N turns as higher-priority for the LLM.
    context_recent_focus: int = 20
    # Min seconds between observe-path archive digests per impression / group key.
    observe_min_interval_seconds: float = 120.0
    # Local inbound ring size per chat display name (SQLite inbound_transcript).
    transcript_max_messages: int = 1000
    # Observe digest also fires when this many new lines accumulate (OR interval).
    observe_digest_min_messages: int = 5
    anti_injection: tuple[str, ...] = ()
    stay_on_prompt: tuple[str, ...] = ()


def default_settings() -> ReplySettings:
    """Conservative defaults so the bot is not noisy before a toml exists."""
    return ReplySettings()


def load_reply_settings(path: Path) -> ReplySettings:
    """Parse reply_policy.toml; missing file or keys fall back to defaults."""
    base = default_settings()
    if not path.is_file():
        return base
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ReplySettings(
        enabled=bool(data.get("enabled", base.enabled)),
        c2c=bool(data.get("c2c", base.c2c)),
        group=bool(data.get("group", base.group)),
        on_text=bool(data.get("on_text", base.on_text)),
        on_image=bool(data.get("on_image", base.on_image)),
        on_voice=bool(data.get("on_voice", base.on_voice)),
        min_interval_seconds=float(
            data.get("min_interval_seconds", base.min_interval_seconds)
        ),
        max_per_session_per_minute=int(
            data.get("max_per_session_per_minute", base.max_per_session_per_minute)
        ),
        require_keywords=_string_tuple(data.get("require_keywords")),
        skip_keywords=_string_tuple(data.get("skip_keywords")),
        group_unmentioned=bool(data.get("group_unmentioned", base.group_unmentioned)),
        unmentioned_cooldown_seconds=float(
            data.get(
                "unmentioned_cooldown_seconds",
                base.unmentioned_cooldown_seconds,
            )
        ),
        unmentioned_need_hook=bool(
            data.get("unmentioned_need_hook", base.unmentioned_need_hook)
        ),
        bot_names=_string_tuple(data.get("bot_names")),
        unmentioned_thread_minutes=float(
            data.get("unmentioned_thread_minutes", base.unmentioned_thread_minutes)
        ),
        unmentioned_off_peak_only=bool(
            data.get("unmentioned_off_peak_only", base.unmentioned_off_peak_only)
        ),
        speak_mode=normalize_speak_mode(data.get("speak_mode", base.speak_mode)),
        group_culture_auto=bool(
            data.get("group_culture_auto", base.group_culture_auto)
        ),
        knowledge_auto=bool(data.get("knowledge_auto", base.knowledge_auto)),
        prompt_evolution_auto=bool(
            data.get("prompt_evolution_auto", base.prompt_evolution_auto)
        ),
        prompt_evolution_min_messages=max(
            0,
            int(
                data.get(
                    "prompt_evolution_min_messages",
                    base.prompt_evolution_min_messages,
                )
            ),
        ),
        context_message_limit=max(
            0,
            int(data.get("context_message_limit", base.context_message_limit)),
        ),
        context_recent_focus=max(
            0,
            int(data.get("context_recent_focus", base.context_recent_focus)),
        ),
        observe_min_interval_seconds=float(
            data.get(
                "observe_min_interval_seconds",
                base.observe_min_interval_seconds,
            )
        ),
        transcript_max_messages=max(
            10,
            int(data.get("transcript_max_messages", base.transcript_max_messages)),
        ),
        observe_digest_min_messages=max(
            1,
            int(
                data.get(
                    "observe_digest_min_messages",
                    base.observe_digest_min_messages,
                )
            ),
        ),
        anti_injection=_string_tuple(data.get("anti_injection")),
        stay_on_prompt=_string_tuple(data.get("stay_on_prompt")),
    )


def normalize_speak_mode(value: object) -> str:
    """Map a toml/UI value to auto / all / mention_quote."""
    key = str(value or "").strip().lower()
    if key in SPEAK_MODES:
        return key
    return SPEAK_AUTO


def next_speak_mode(current: str) -> str:
    """Return the next mode in the console cycle: auto → all → mention_quote."""
    key = normalize_speak_mode(current)
    index = SPEAK_MODES.index(key)
    return SPEAK_MODES[(index + 1) % len(SPEAK_MODES)]


def speak_mode_label(mode: str) -> str:
    """Chinese label for the console speak-mode button."""
    return SPEAK_MODE_LABELS[normalize_speak_mode(mode)]


def upsert_speak_mode_text(text: str, mode: str) -> str:
    """Replace or append speak_mode in reply_policy.toml without dropping comments."""
    mode = normalize_speak_mode(mode)
    raw = text or ""
    if _SPEAK_ASSIGN.search(raw):
        return _SPEAK_ASSIGN.sub(rf'\1"{mode}"', raw, count=1)
    line = f'speak_mode = "{mode}"'
    if not raw.strip():
        return line + "\n"
    return raw.rstrip() + (
        "\n\n# 群发言模式：auto=自动 / all=全部尝试回复 / mention_quote=仅@和引用\n"
        f"{line}\n"
    )


def upsert_context_message_limit_text(text: str, limit: int) -> str:
    """Replace or append context_message_limit without dropping comments."""
    value = max(0, int(limit))
    raw = text or ""
    if _CONTEXT_LIMIT_ASSIGN.search(raw):
        return _CONTEXT_LIMIT_ASSIGN.sub(rf"\g<1>{value}", raw, count=1)
    line = f"context_message_limit = {value}"
    if not raw.strip():
        return line + "\n"
    return raw.rstrip() + (
        "\n\n# 回复前滚动读取的上下文条数（Callus；下一条消息生效）\n"
        f"{line}\n"
    )


# Scalar fields the console form edits. Prompt-evolution keys stay on the 进化 page.
_POLICY_BOOL_KEYS = (
    "enabled",
    "c2c",
    "group",
    "on_text",
    "on_image",
    "on_voice",
    "group_unmentioned",
    "unmentioned_need_hook",
    "unmentioned_off_peak_only",
    "group_culture_auto",
    "knowledge_auto",
)
_POLICY_INT_KEYS = (
    "min_interval_seconds",
    "max_per_session_per_minute",
    "unmentioned_cooldown_seconds",
    "unmentioned_thread_minutes",
    "context_message_limit",
    "context_recent_focus",
    "observe_min_interval_seconds",
    "observe_digest_min_messages",
    "transcript_max_messages",
)
_POLICY_LIST_KEYS = ("require_keywords", "skip_keywords", "bot_names")
# Loader clamps; the form uses the same floors so a save matches what the bot reads.
_POLICY_INT_MIN = {
    "transcript_max_messages": 10,
    "observe_digest_min_messages": 1,
}


def read_policy_form(text: str) -> dict[str, object]:
    """Read console form values from a policy body. Missing keys use defaults.

    Invalid toml yields defaults so the form can still open; the caller keeps
    the original text and only rewrites assignment lines on save.
    """
    base = default_settings()
    data = _toml_dict(text)
    values: dict[str, object] = {}
    for key in _POLICY_BOOL_KEYS:
        values[key] = bool(data[key]) if key in data else bool(getattr(base, key))
    for key in _POLICY_INT_KEYS:
        if key in data:
            values[key] = int(float(data[key]))
        else:
            values[key] = int(getattr(base, key))
    for key in _POLICY_LIST_KEYS:
        if key in data:
            values[key] = list(_string_tuple(data.get(key)))
        else:
            values[key] = list(getattr(base, key))
    if "speak_mode" in data:
        values["speak_mode"] = normalize_speak_mode(data.get("speak_mode"))
    else:
        values["speak_mode"] = base.speak_mode
    return values


def apply_policy_form(text: str, values: dict[str, object]) -> str:
    """Upsert console fields into toml text. Comments and other keys stay."""
    raw = text or ""
    for key in _POLICY_BOOL_KEYS:
        if key not in values:
            continue
        raw = upsert_toml_assignment(raw, key, "true" if values[key] else "false")
    for key in _POLICY_INT_KEYS:
        if key not in values:
            continue
        raw_number = values[key]
        if isinstance(raw_number, bool) or not isinstance(raw_number, (int, float, str)):
            number = 0
        else:
            number = int(float(raw_number))
        number = max(_POLICY_INT_MIN.get(key, 0), number)
        raw = upsert_toml_assignment(raw, key, str(number))
    for key in _POLICY_LIST_KEYS:
        if key not in values:
            continue
        raw = upsert_toml_assignment(
            raw, key, _render_inline_string_list(values[key])
        )
    if "speak_mode" in values:
        raw = upsert_speak_mode_text(raw, str(values["speak_mode"]))
    return raw


def upsert_toml_assignment(text: str, key: str, rendered: str) -> str:
    """Replace one top-level `key = ...` line, or append it.

    The key must match exactly, so `group` does not rewrite `group_unmentioned`.
    A trailing same-line comment is kept when the value itself contains no `#`.
    """
    raw = text or ""
    pattern = re.compile(rf"(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)(.*)$")
    match = pattern.search(raw)
    if match is None:
        line = f"{key} = {rendered}"
        if not raw.strip():
            return line + "\n"
        return raw.rstrip() + "\n" + line + "\n"
    rest = match.group(2)
    comment = ""
    hash_at = rest.find(" #")
    if hash_at >= 0 and "[" not in rest[:hash_at]:
        comment = rest[hash_at:]
    return raw[: match.start(2)] + rendered + comment + raw[match.end(2) :]


def _render_inline_string_list(value: object) -> str:
    """Render a one-line TOML string array, escaping quotes and backslashes."""
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    if not items:
        return "[]"
    parts: list[str] = []
    for item in items:
        escaped = item.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{escaped}"')
    return "[" + ", ".join(parts) + "]"


def _toml_dict(text: str) -> dict[str, object]:
    """Parse toml into a dict. Empty or invalid text yields an empty dict."""
    raw = text or ""
    if not raw.strip():
        return {}
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def upsert_prompt_evolution_auto_text(text: str, enabled: bool) -> str:
    """Replace or append prompt_evolution_auto without dropping comments."""
    flag = "true" if enabled else "false"
    raw = text or ""
    if _EVOLUTION_AUTO_ASSIGN.search(raw):
        return _EVOLUTION_AUTO_ASSIGN.sub(rf"\g<1>{flag}", raw, count=1)
    line = f"prompt_evolution_auto = {flag}"
    if not raw.strip():
        return line + "\n"
    return raw.rstrip() + (
        "\n\n# 群聊成功回复后自动起草「提示词进化」提案（须控制台审批才生效）\n"
        f"{line}\n"
    )


def upsert_prompt_evolution_min_messages_text(text: str, min_messages: int) -> str:
    """Replace or append prompt_evolution_min_messages; drop legacy seconds key."""
    value = max(0, int(min_messages))
    rendered = str(value)
    raw = text or ""
    if _EVOLUTION_INTERVAL_ASSIGN.search(raw):
        # Remove legacy time-based throttle line when migrating.
        raw = _EVOLUTION_INTERVAL_ASSIGN.sub("", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
    if _EVOLUTION_MESSAGES_ASSIGN.search(raw):
        return _EVOLUTION_MESSAGES_ASSIGN.sub(rf"\g<1>{rendered}", raw, count=1)
    line = f"prompt_evolution_min_messages = {rendered}"
    if not raw.strip():
        return line + "\n"
    return raw.rstrip() + (
        "\n\n# 同群两次进化提案之间，bot 至少再收到这么多条消息；有未审 pending 时不提案\n"
        f"{line}\n"
    )


# Back-compat alias for older admin imports.
def upsert_prompt_evolution_interval_text(text: str, seconds: float) -> str:
    """Deprecated: treat former seconds value as message count when rewriting."""
    return upsert_prompt_evolution_min_messages_text(text, int(seconds))


def policy_guard_lists(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read anti_injection / stay_on_prompt from raw reply_policy.toml text."""
    raw = text or ""
    if not raw.strip():
        return (), ()
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return (), ()
    if not isinstance(data, dict):
        return (), ()
    return (
        _string_tuple(data.get("anti_injection")),
        _string_tuple(data.get("stay_on_prompt")),
    )


def strip_policy_guards(text: str) -> str:
    """Remove anti_injection / stay_on_prompt arrays (and their nearby comments)."""
    body = text or ""
    for key in ("anti_injection", "stay_on_prompt"):
        body = _remove_toml_array(body, key)
    return body.rstrip() + ("\n" if body.strip() else "")


def merge_policy_guards(
    body: str,
    anti_injection: tuple[str, ...] | list[str],
    stay_on_prompt: tuple[str, ...] | list[str],
) -> str:
    """Append guard arrays to a switch/frequency body for reply_policy.toml."""
    clean = strip_policy_guards(body).rstrip()
    anti = tuple(str(item).strip() for item in anti_injection if str(item).strip())
    stay = tuple(str(item).strip() for item in stay_on_prompt if str(item).strip())
    blocks = [
        _format_string_array(
            "anti_injection",
            anti,
            comment="# 系统提示：防注入。保存后下一条消息生效。",
        ),
        _format_string_array(
            "stay_on_prompt",
            stay,
            comment="# 系统提示：平台规则与 bot 能力（在人设之前注入）。保存后下一条消息生效。",
        ),
    ]
    if not clean:
        return "\n\n".join(blocks) + "\n"
    return clean + "\n\n" + "\n\n".join(blocks) + "\n"


def lines_to_guard_list(text: str) -> tuple[str, ...]:
    """Split a multiline editor buffer into non-empty guard rules."""
    return tuple(
        line.strip()
        for line in (text or "").replace("\r\n", "\n").split("\n")
        if line.strip()
    )


def guard_list_to_lines(items: tuple[str, ...] | list[str]) -> str:
    """Join guard rules for a plain-text editor (one rule per line)."""
    return "\n".join(str(item).strip() for item in items if str(item).strip())


def prompt_guard_chunks(
    anti_injection: tuple[str, ...],
    stay_on_prompt: tuple[str, ...],
) -> list[str]:
    """Format reply-policy safety lists as headed system-prompt sections."""
    chunks: list[str] = []
    anti = _bullet_lines(anti_injection)
    if anti:
        chunks.append("【防注入】\n" + anti)
    stay = _bullet_lines(stay_on_prompt)
    if stay:
        chunks.append("【不得脱离提示词】\n" + stay)
    return chunks


def _bullet_lines(items: tuple[str, ...]) -> str:
    """Join non-empty lines as markdown bullets."""
    bullets: list[str] = []
    for item in items:
        line = item.strip()
        if not line:
            continue
        if line.startswith("- "):
            bullets.append(line)
        else:
            bullets.append(f"- {line}")
    return "\n".join(bullets)


def _string_tuple(value: object) -> tuple[str, ...]:
    """Normalize a toml array of strings."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _format_string_array(
    key: str,
    items: tuple[str, ...],
    *,
    comment: str = "",
) -> str:
    """Render a TOML string array, escaping quotes and backslashes."""
    lines: list[str] = []
    if comment:
        lines.append(comment)
    if not items:
        lines.append(f"{key} = []")
        return "\n".join(lines)
    lines.append(f"{key} = [")
    for item in items:
        escaped = item.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  "{escaped}",')
    lines.append("]")
    return "\n".join(lines)


def _remove_toml_array(text: str, key: str) -> str:
    """Delete one top-level `key = [...]` assignment, keeping other comments."""
    match = re.search(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*\[", text)
    if not match:
        return text
    start = match.start()
    # Drop immediately preceding blank/comment lines that belong to this block.
    prefix = text[:start]
    while True:
        cut = prefix.rstrip("\n")
        if not cut:
            start = 0
            break
        line_start = cut.rfind("\n") + 1
        line = cut[line_start:]
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            prefix = cut[:line_start]
            start = line_start
            continue
        break
    end = _toml_array_end(text, match.end() - 1)
    if end < 0:
        return text
    while end < len(text) and text[end] in " \t":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def _toml_array_end(text: str, open_bracket: int) -> int:
    """Return index after the matching ']' for a TOML array, or -1."""
    depth = 0
    i = open_bracket
    in_str = False
    escape = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


class _SlotReservation:
    """One decide() rate-limit reservation that can be confirmed or abandoned."""

    __slots__ = ("hit_keys", "unat_gid", "unat_prev", "unat_had")

    def __init__(
        self,
        hit_keys: list[str],
        *,
        unat_gid: str = "",
        unat_prev: float | None = None,
        unat_had: bool = False,
    ) -> None:
        self.hit_keys = list(hit_keys)
        self.unat_gid = unat_gid
        self.unat_prev = unat_prev
        self.unat_had = unat_had


class ReplyGate:
    """Decide whether to reply; re-reads toml when the file mtime changes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime = -1.0
        self._settings = default_settings()
        self._hits: dict[str, list[float]] = {}
        self._unat_at: dict[str, float] = {}
        self._last_bot_reply: dict[str, float] = {}
        # Per-thread stack of decide() reservations (coalescer runs one session
        # per thread; cross-session must not clobber a single global pending).
        self._tls = threading.local()
        self._lock = threading.RLock()

    def note_bot_reply(self, session_id: str, now: float | None = None) -> None:
        """Remember that the bot replied in this session (for un-@ thread follow-ups)."""
        with self._lock:
            self._touch_thread(session_id, now)

    def note_unmentioned_reply(self, group_id: str, now: float | None = None) -> None:
        """Refresh un-@ cooldown timestamp after a delivered reply (hit already reserved in decide)."""
        stamp = time.time() if now is None else now
        gid = group_id.strip()
        if not gid:
            return
        with self._lock:
            self._unat_at[gid] = stamp

    def note_user_engagement(self, session_id: str, now: float | None = None) -> None:
        """Remember @ or name-call so un-@ follow-ups work before the bot finishes replying."""
        with self._lock:
            self._touch_thread(session_id, now)

    def note_inbound_engagement(self, message: IncomingTurn, now: float | None = None) -> None:
        """Open the un-@ thread window when the user names the bot."""
        if not message.is_group:
            return
        policy = self.current()
        if is_addressed(message, policy.bot_names) or message.quotes_bot:
            self.note_user_engagement(message.session_id, now)

    def _touch_thread(self, session_id: str, now: float | None) -> None:
        """Refresh the active conversation window for a user-in-group session."""
        stamp = time.time() if now is None else now
        if session_id.strip():
            self._last_bot_reply[session_id] = stamp

    def current(self) -> ReplySettings:
        """Return the latest policy, reloading from disk when needed."""
        with self._lock:
            self._reload_if_changed()
            return self._settings

    def decide(self, message: IncomingTurn, now: float | None = None) -> str | None:
        """Return a skip reason, or None if the bot should reply."""
        with self._lock:
            self._reload_if_changed()
            policy = self._settings
            if not policy.enabled:
                return "disabled"
            if message.is_group and not policy.group:
                return "group_off"
            if not message.is_group and not policy.c2c:
                return "c2c_off"
            user_text = message.user_text
            content_type = (message.content_type or "text").strip().lower() or "text"
            # Voice is gated by on_voice; empty/placeholder STT never reaches the LLM.
            if content_type == "voice":
                if not policy.on_voice:
                    return "kind_off"
                if _is_empty_voice_body(message):
                    return "voice_empty"
                if not policy.on_text:
                    return "kind_off"
            elif content_type in {"image", "emotion"}:
                # Capture may still fill image_paths after decide; only gate on_image.
                if not policy.on_image:
                    return "kind_off"
            else:
                has_media = bool(message.image_paths) or content_type != "text"
                if not user_text.strip() and not has_media:
                    return "kind_off"
                if user_text.strip() and not policy.on_text:
                    return "kind_off"
                if not user_text.strip() and has_media and not policy.on_image:
                    return "kind_off"
            if (
                policy.require_keywords
                and user_text.strip()
                and not _contains_any(user_text, policy.require_keywords)
            ):
                return "require_keywords"
            if policy.skip_keywords and user_text.strip() and _contains_any(
                user_text, policy.skip_keywords
            ):
                return "skip_keywords"
            stamp = time.time() if now is None else now
            addressed = is_addressed(message, policy.bot_names) or message.quotes_bot
            if addressed:
                reason = self._rate_limit(message.session_id, policy, stamp)
                if reason:
                    return reason
                # One WeChat line can arrive twice: preview with empty sender
                # (session=chat) then again with nickname (session=chat:nick).
                # Session cooldown alone cannot stop that double reply.
                hit_keys = [message.session_id]
                dup = self._duplicate_inbound(message, policy, stamp)
                if dup is not None:
                    return dup
                hit_keys.extend(self._dedupe_keys(message))
                self._begin_reservation(hit_keys=hit_keys)
                for key in hit_keys:
                    if key:
                        self._record_hit(key, stamp)
                return None
            mode = normalize_speak_mode(policy.speak_mode)
            if mode == SPEAK_MENTION_QUOTE:
                return "unmentioned_off"
            if mode != SPEAK_ALL:
                if policy.unmentioned_off_peak_only and is_beijing_unmentioned_peak(stamp):
                    return "unmentioned_peak_hours"
                reason = _unmentioned_skip(message, policy, self, stamp)
                if reason:
                    return reason
            group_id = message.group_id
            last = self._unat_at.get(group_id, 0.0)
            if last > 0 and stamp - last < policy.unmentioned_cooldown_seconds:
                return "unmentioned_cooldown"
            cap = self._minute_cap(f"unat:{group_id}", policy, stamp)
            if cap:
                return cap
            if group_id:
                self._begin_reservation(
                    hit_keys=[f"unat:{group_id}"],
                    unat_gid=group_id,
                    unat_prev=self._unat_at.get(group_id),
                    unat_had=group_id in self._unat_at,
                )
                self._unat_at[group_id] = stamp
                self._record_hit(f"unat:{group_id}", stamp)
            return None

    def confirm_reply_slot(self) -> None:
        """Keep decide() rate-limit hits after a reply was actually delivered."""
        with self._lock:
            self._pop_reservation()

    def abandon_reply_slot(self) -> None:
        """Undo decide() hits/cooldown when handle aborts before a delivered reply."""
        with self._lock:
            pending = self._pop_reservation()
            if pending is None:
                return
            for key in pending.hit_keys:
                hits = self._hits.get(key)
                if hits:
                    hits.pop()
                    if not hits:
                        del self._hits[key]
            gid = pending.unat_gid
            if gid:
                if pending.unat_had:
                    prev = pending.unat_prev
                    if prev is None:
                        self._unat_at.pop(gid, None)
                    else:
                        self._unat_at[gid] = prev
                else:
                    self._unat_at.pop(gid, None)

    def _reservation_stack(self) -> list[_SlotReservation]:
        """Return this thread's pending decide() reservations."""
        stack = getattr(self._tls, "stack", None)
        if stack is None:
            stack = []
            self._tls.stack = stack
        return stack

    def _begin_reservation(
        self,
        *,
        hit_keys: list[str],
        unat_gid: str = "",
        unat_prev: float | None = None,
        unat_had: bool = False,
    ) -> None:
        """Push a decide() reservation for this thread (rolled back on abandon)."""
        self._reservation_stack().append(
            _SlotReservation(
                hit_keys,
                unat_gid=unat_gid,
                unat_prev=unat_prev,
                unat_had=unat_had,
            )
        )

    def _pop_reservation(self) -> _SlotReservation | None:
        """Pop this thread's newest reservation, or None when empty."""
        stack = self._reservation_stack()
        if not stack:
            return None
        return stack.pop()

    def _reload_if_changed(self) -> None:
        """Reload toml when it appears or its mtime changes."""
        if not self._path.is_file():
            self._settings = default_settings()
            self._mtime = -1.0
            return
        mtime = self._path.stat().st_mtime
        if mtime == self._mtime:
            return
        try:
            self._settings = load_reply_settings(self._path)
            self._mtime = mtime
            _log.info("loaded reply policy from %s", self._path)
        except Exception:
            _log.exception("failed to load %s; keeping previous policy", self._path)

    def _rate_limit(self, session_id: str, policy: ReplySettings, now: float) -> str | None:
        """Enforce per-session cooldown and per-minute cap for @ / DM replies."""
        hits = [t for t in self._hits.get(session_id, []) if now - t < 60.0]
        self._hits[session_id] = hits
        if hits and now - hits[-1] < policy.min_interval_seconds:
            return "min_interval"
        if len(hits) >= max(1, policy.max_per_session_per_minute):
            return "max_per_minute"
        return None

    def _minute_cap(self, key: str, policy: ReplySettings, now: float) -> str | None:
        """Cap un-@ replies per group per minute so a busy chat cannot burn tokens."""
        hits = [t for t in self._hits.get(key, []) if now - t < 60.0]
        self._hits[key] = hits
        if len(hits) >= max(1, policy.max_per_session_per_minute):
            return "max_per_minute"
        return None

    def _record_hit(self, session_id: str, now: float) -> None:
        """Record that we are going to reply in this session."""
        hits = self._hits.setdefault(session_id, [])
        hits.append(now)

    def _dedupe_window(self, policy: ReplySettings) -> float:
        """Seconds to treat a second Watch/preview of the same chat line as a duplicate."""
        # Long enough to cover preview→full re-delivery; short enough for two real @s.
        return min(30.0, max(8.0, float(policy.min_interval_seconds) * 0.25))

    def _dedupe_keys(self, message: IncomingTurn) -> list[str]:
        """Keys that identify the same inbound across empty/filled sender splits."""
        keys: list[str] = []
        chat = message.chat.strip()
        if chat:
            keys.append(f"chat:{chat}")
        mid = (message.msg_id or "").strip()
        if mid:
            keys.append(f"msgid:{mid}")
        return keys

    def _interval_hit(self, key: str, now: float, window: float) -> bool:
        """True when *key* was recorded within *window* seconds."""
        hits = [t for t in self._hits.get(key, []) if now - t < max(window, 60.0)]
        self._hits[key] = hits
        return bool(hits and now - hits[-1] < window)

    def _duplicate_inbound(
        self, message: IncomingTurn, policy: ReplySettings, now: float
    ) -> str | None:
        """Skip when the same chat line is already being answered under another session_id."""
        window = self._dedupe_window(policy)
        for key in self._dedupe_keys(message):
            if self._interval_hit(key, now, window):
                return "dup_inbound"
        return None


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword appears in text (case-insensitive)."""
    lowered = text.lower()
    return any(key.lower() in lowered for key in keywords)


def _mentions_bot_name(text: str, names: tuple[str, ...]) -> bool:
    """True when user text contains a configured bot name."""
    lowered = text.lower()
    return any(name.lower() in lowered for name in names if name.strip())


def _has_recent_thread(gate: ReplyGate, session_id: str, now: float, ttl_seconds: float) -> bool:
    """True when the bot replied in this session within the thread window."""
    last = gate._last_bot_reply.get(session_id, 0.0)
    return last > 0 and now - last <= ttl_seconds


def _looks_like_group_broadcast(text: str) -> bool:
    """True when the line is clearly addressed to the whole group, not the bot."""
    stripped = text.strip()
    if not stripped:
        return False
    if any(word in stripped for word in ("大家", "各位", "全员", "@all", "@所有人")):
        return True
    lowered = stripped.casefold()
    for phrase in ("我先下班", "我走了", "我先撤", "大家晚安", "各位注意"):
        if phrase.casefold() in lowered:
            return True
    return False


_VOICE_PLACEHOLDERS = frozenset({"", "[语音]", "[消息]"})
_IMAGE_PLACEHOLDERS = frozenset(
    {"", "[图片]", "[动画表情]", "[表情]", "[消息]"}
)


def is_image_placeholder_text(text: str) -> bool:
    """True when *text* is a WeChat image/emotion session-list placeholder."""
    return (text or "").strip() in _IMAGE_PLACEHOLDERS


def _is_empty_voice_body(message: IncomingTurn) -> bool:
    """True when voice STT left only a placeholder or blank body."""
    raw = (message.text or "").strip()
    if raw in _VOICE_PLACEHOLDERS:
        return True
    return message.user_text.strip() in _VOICE_PLACEHOLDERS


def _is_empty_image_body(message: IncomingTurn) -> bool:
    """True when an image/emotion turn has only a WeChat placeholder body."""
    if is_image_placeholder_text(message.text or ""):
        return True
    return is_image_placeholder_text(message.user_text)


def is_empty_image_turn(message: IncomingTurn) -> bool:
    """True when image/emotion capture left nothing for the vision model."""
    content_type = (message.content_type or "text").strip().lower() or "text"
    if content_type not in {"image", "emotion"}:
        return False
    if message.image_paths:
        return False
    return _is_empty_image_body(message)


def is_addressed(message: IncomingTurn, names: tuple[str, ...]) -> bool:
    """Private chats always count as addressed; groups need a bot name in the text.

    Use the current bubble body only — quoted cite text must not count as @.
    """
    if not message.is_group:
        return True
    return _mentions_bot_name(message.text, names)


def _has_media_body(message: IncomingTurn) -> bool:
    """True when the turn is sticker/image/non-text even if user_text is empty."""
    content_type = (message.content_type or "text").strip().lower() or "text"
    return bool(message.image_paths) or content_type != "text"


def _unmentioned_skip(
    message: IncomingTurn,
    policy: ReplySettings,
    gate: ReplyGate,
    now: float,
) -> str | None:
    """Skip un-@ group lines unless bot is named or the user is in an active thread."""
    if not policy.group_unmentioned:
        return "unmentioned_off"
    if not policy.unmentioned_need_hook:
        return None
    text = message.user_text.strip()
    ttl = max(1.0, policy.unmentioned_thread_minutes) * 60.0
    if not text:
        # Empty stickers/emotions have no name hook; allow only inside an open thread.
        if _has_media_body(message) and _has_recent_thread(
            gate, message.session_id, now, ttl
        ):
            return None
        return "unmentioned_no_hook"
    if _mentions_bot_name(text, policy.bot_names):
        return None
    if _has_recent_thread(gate, message.session_id, now, ttl):
        if _looks_like_group_broadcast(text):
            return "unmentioned_no_hook"
        return None
    return "unmentioned_no_hook"


def is_beijing_unmentioned_peak(stamp: float) -> bool:
    """True on Mon-Fri 09:00-12:00 and 14:00-18:00 Beijing time (no un-@ replies)."""
    dt = datetime.fromtimestamp(stamp, tz=_BEIJING)
    if dt.weekday() >= 5:
        return False
    minute_of_day = dt.hour * 60 + dt.minute
    morning = 9 * 60 <= minute_of_day < 12 * 60
    afternoon = 14 * 60 <= minute_of_day < 18 * 60
    return morning or afternoon
