"""Read/update project .env for the admin API tab (secrets stay masked)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bot.admin.bot_control import project_root

PUBLIC_KEYS: tuple[str, ...] = (
    "WEJAM_TARGET",
    "BOT_NAME",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
)

SECRET_KEYS: tuple[str, ...] = ("LLM_API_KEY",)

ALL_FORM_KEYS: tuple[str, ...] = (
    "WEJAM_TARGET",
    "BOT_NAME",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
)

OPTIONAL_KEEP_IF_BLANK: frozenset[str] = frozenset(
    {"LLM_TIMEOUT_SECONDS", "WEJAM_TARGET", "BOT_NAME"}
)

_MODEL_ALIASES: dict[str, str] = {
    "DeepSeek-V4.1-Flash": "deepseek-flash",
    "deepseek-v4.1-flash": "deepseek-flash",
    "deepseek-chat": "deepseek-flash",
}


def normalize_model_id(value: str) -> str:
    """Map known display names to DeepSeek API model IDs."""
    raw = (value or "").strip()
    if not raw:
        return ""
    return _MODEL_ALIASES.get(raw, raw)


@dataclass(frozen=True)
class ApiSettingsView:
    """UI-safe API settings: secrets are never returned in full."""

    values: dict[str, str]
    secret_masks: dict[str, str]
    secret_set: dict[str, bool]
    path: str


def env_file_path() -> Path:
    """Return the project .env path used by load_settings()."""
    return project_root() / ".env"


def mask_secret(value: str) -> str:
    """Redact a secret for display (prefix hint + last 4, or ****)."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "****"
    prefix = raw[:4]
    if raw.startswith("sk-"):
        prefix = "sk-"
    return f"{prefix}…{raw[-4:]}"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE pairs from .env text; ignores comments and blank lines."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        out[key] = value
    return out


def read_api_settings(path: Path | None = None) -> ApiSettingsView:
    """Load .env into a UI view with secrets masked and never returned raw."""
    target = path or env_file_path()
    raw_map: dict[str, str] = {}
    if target.is_file():
        raw_map = parse_env_file(target.read_text(encoding="utf-8"))
    values: dict[str, str] = {}
    secret_masks: dict[str, str] = {}
    secret_set: dict[str, bool] = {}
    for key in PUBLIC_KEYS:
        values[key] = raw_map.get(key, "")
    for key in SECRET_KEYS:
        secret = raw_map.get(key, "")
        secret_set[key] = bool(secret.strip())
        secret_masks[key] = mask_secret(secret) if secret.strip() else ""
        values[key] = ""
    return ApiSettingsView(
        values=values,
        secret_masks=secret_masks,
        secret_set=secret_set,
        path=str(target),
    )


def save_api_settings(
    updates: dict[str, str],
    *,
    path: Path | None = None,
    clear_secrets: frozenset[str] | None = None,
) -> Path:
    """Update selected keys in .env while preserving comments and unrelated keys."""
    target = path or env_file_path()
    existing: dict[str, str] = {}
    original = ""
    if target.is_file():
        original = target.read_text(encoding="utf-8")
        existing = parse_env_file(original)

    clear = clear_secrets or frozenset()
    merged = dict(existing)
    for key in ALL_FORM_KEYS:
        if key not in updates and key not in clear:
            continue
        if key in SECRET_KEYS:
            if key in clear:
                merged[key] = ""
                continue
            incoming = (updates.get(key) or "").strip()
            if not incoming:
                continue
            if _looks_like_mask(incoming, existing.get(key, "")):
                continue
            merged[key] = incoming
            continue
        incoming = (updates.get(key) or "").strip()
        if not incoming and key in OPTIONAL_KEEP_IF_BLANK:
            continue
        if key == "LLM_MODEL":
            incoming = normalize_model_id(incoming)
        merged[key] = incoming

    new_text = render_env_update(original, merged, ALL_FORM_KEYS)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_text, encoding="utf-8")
    return target


def render_env_update(original: str, merged: dict[str, str], managed_keys: tuple[str, ...]) -> str:
    """Rewrite .env text: update managed keys in place, append any missing ones."""
    managed = set(managed_keys)
    seen: set[str] = set()
    lines_out: list[str] = []
    source_lines = original.splitlines() if original else []

    for line in source_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines_out.append(line)
            continue
        work = stripped
        prefix = ""
        if work.startswith("export "):
            prefix = "export "
            work = work[7:].strip()
        key, _, _old = work.partition("=")
        key = key.strip()
        if key not in managed:
            lines_out.append(line)
            continue
        seen.add(key)
        lines_out.append(f"{prefix}{key}={_format_env_value(merged.get(key, ''))}")

    missing = [key for key in managed_keys if key not in seen]
    if missing:
        if lines_out and lines_out[-1].strip():
            lines_out.append("")
        if not original.strip():
            lines_out.append("# Written by WeJam 闲聊控制台 · API 页")
        for key in missing:
            lines_out.append(f"{key}={_format_env_value(merged.get(key, ''))}")

    text = "\n".join(lines_out)
    if text and not text.endswith("\n"):
        text += "\n"
    return text or "\n".join(f"{k}=" for k in managed_keys) + "\n"


def _format_env_value(value: str) -> str:
    """Quote values that contain spaces or # so the .env stays parseable."""
    if value == "":
        return ""
    if any(ch in value for ch in ' \t#"\'\\'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _looks_like_mask(incoming: str, real: str) -> bool:
    """True when the user left the masked display text instead of a new secret."""
    if not incoming:
        return False
    if "…" in incoming or "..." in incoming:
        return True
    if incoming == "****":
        return True
    if real and incoming == mask_secret(real):
        return True
    return False
