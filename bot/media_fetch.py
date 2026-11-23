"""Copy Callus container media paths onto the host so the LLM can read them."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from bot.config import project_root

_log = logging.getLogger(__name__)

_HOST_MEDIA = project_root() / "data" / "media"
_CONTAINER_PREFIXES = ("/data/media/", "/data/media")


def host_media_dir() -> Path:
    """Ensure and return the host-side media cache directory."""
    _HOST_MEDIA.mkdir(parents=True, exist_ok=True)
    return _HOST_MEDIA


def _looks_container_path(path: str) -> bool:
    """True when *path* is a Callus in-container media path."""
    text = (path or "").strip()
    return text.startswith("/data/media/")


def materialize_media_path(
    path: str,
    *,
    container: str | None = None,
) -> str:
    """Return a host-readable file path for one media attachment.

    Already-local readable files are returned as-is. Container paths under
    ``/data/media/`` are ``docker cp``'d into ``data/media/``.
    """
    text = (path or "").strip()
    if not text:
        return ""
    local = Path(text)
    if local.is_file():
        return str(local.resolve())
    if not _looks_container_path(text):
        _log.warning("media path not local and not container media: %s", text)
        return ""
    name = container
    if not name:
        from bot.admin.docker_control import session_container_name

        name = session_container_name()
    dest_dir = host_media_dir()
    dest = dest_dir / Path(text).name
    if dest.is_file() and dest.stat().st_size > 0:
        return str(dest.resolve())
    try:
        proc = subprocess.run(
            ["docker", "cp", f"{name}:{text}", str(dest)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("docker cp media failed path=%s err=%s", text, exc)
        return ""
    if proc.returncode != 0 or not dest.is_file():
        err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        _log.warning("docker cp media failed path=%s detail=%s", text, err[:200])
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return ""
    return str(dest.resolve())


def materialize_media_paths(
    paths: list[str] | tuple[str, ...],
    *,
    container: str | None = None,
) -> tuple[str, ...]:
    """Materialize many paths; drop empties and preserve order without duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        host = materialize_media_path(path, container=container)
        if not host or host in seen:
            continue
        seen.add(host)
        out.append(host)
    return tuple(out)


def copy_local_aside(path: str, *, label: str = "frame") -> str:
    """Copy an already-local file into data/media with a stable name (tests/helpers)."""
    src = Path(path)
    if not src.is_file():
        return ""
    dest = host_media_dir() / f"{label}_{src.name}"
    if not dest.exists():
        shutil.copy2(src, dest)
    return str(dest.resolve())
