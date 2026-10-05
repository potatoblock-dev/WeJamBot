"""Start/stop the host-side bot process via pid file and shell scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_PID_FILE = _ROOT / "data" / "bot.pid"
_START = _ROOT / "scripts" / "start-bot.sh"
_STOP = _ROOT / "scripts" / "stop-bot.sh"
_LOG = _ROOT / "data" / "bot.log"


def project_root() -> Path:
    """Return the WeJamBot repository root."""
    return _ROOT


def bot_log_path() -> Path:
    """Return the runner log file path."""
    return _LOG


def _read_pid() -> int | None:
    """Read bot pid from disk, or None if missing/invalid."""
    if not _PID_FILE.is_file():
        return None
    raw = _PID_FILE.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _pid_alive(pid: int) -> bool:
    """True when the process id still exists."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def bot_status() -> dict[str, object]:
    """Report whether the chat runner process is up."""
    pid = _read_pid()
    running = pid is not None and _pid_alive(pid)
    return {
        "running": running,
        "pid": pid if running else None,
        "log_path": str(_LOG),
    }


def start_bot() -> None:
    """Run start-bot.sh (no-op if already running)."""
    subprocess.run([str(_START)], check=True, cwd=str(_ROOT))


def stop_bot() -> None:
    """Run stop-bot.sh."""
    subprocess.run([str(_STOP)], check=True, cwd=str(_ROOT))
