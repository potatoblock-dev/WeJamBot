"""Probe Docker Desktop and start/stop the WeJam WeChat session container."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_RUN_SH = _ROOT / "docker" / "run.sh"
_DEFAULT_IMAGE = "wejam/wechat:latest"
_DEFAULT_NAME = "wejam-session"
_CMD_TIMEOUT = 8
_START_TIMEOUT = 120


@dataclass(frozen=True)
class DockerSnapshot:
    """Layered Docker/WeJam container status for the admin run tab."""

    docker_bin: str
    daemon_ok: bool
    daemon_error: str
    image: str
    image_present: bool
    image_id: str
    image_created: str
    container: str
    container_status: str
    container_running: bool
    detail: str


def project_root() -> Path:
    """Return the WeJamBot repository root."""
    return _ROOT


def _resolve_docker_bin() -> str:
    """Locate the docker CLI, preferring PATH then ~/.local/bin/docker."""
    found = shutil.which("docker")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "docker"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return "docker"


def _run_docker(
    args: list[str],
    *,
    timeout: int = _CMD_TIMEOUT,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a docker subcommand with a short timeout and captured text output."""
    bin_path = _resolve_docker_bin()
    return subprocess.run(
        [bin_path, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _short_err(exc: BaseException | str, limit: int = 180) -> str:
    """Trim stderr or exception text for UI labels."""
    text = str(exc).strip().replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _subprocess_env() -> dict[str, str]:
    """Build child env so bash scripts find docker (GUI PATH often lacks ~/.local/bin)."""
    env = os.environ.copy()
    extras: list[str] = []
    docker_bin = _resolve_docker_bin()
    docker_path = Path(docker_bin)
    if docker_path.is_file():
        extras.append(str(docker_path.resolve().parent))
    for candidate in (
        Path.home() / ".local" / "bin",
        Path("/usr/local/bin"),
        Path("/Applications/Docker.app/Contents/Resources/bin"),
    ):
        text = str(candidate)
        if text not in extras and candidate.is_dir():
            extras.append(text)
    old = env.get("PATH", "")
    env["PATH"] = ":".join([*extras, old] if old else extras)
    if docker_path.is_file():
        env["DOCKER_BIN"] = str(docker_path.resolve())
    return env


def daemon_ok() -> tuple[bool, str]:
    """Return whether the Docker engine answers `docker info`."""
    try:
        proc = _run_docker(["info", "--format", "{{.ServerVersion}}"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, _short_err(exc)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return False, _short_err(err)
    return True, ""


def image_info(name: str = _DEFAULT_IMAGE) -> dict[str, object]:
    """Inspect a local image; missing images return present=False."""
    try:
        proc = _run_docker(
            ["image", "inspect", name, "--format", "{{json .}}"],
            timeout=_CMD_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "present": False,
            "id": "",
            "created": "",
            "error": _short_err(exc),
        }
    if proc.returncode != 0:
        return {
            "present": False,
            "id": "",
            "created": "",
            "error": _short_err(proc.stderr or "image not found"),
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"present": False, "id": "", "created": "", "error": "bad inspect json"}
    raw_id = str(payload.get("Id") or "")
    short_id = raw_id.removeprefix("sha256:")[:12]
    return {
        "present": True,
        "id": short_id,
        "created": str(payload.get("Created") or "")[:19],
        "error": "",
    }


def container_info(name: str = _DEFAULT_NAME) -> dict[str, object]:
    """Inspect the WeJam session container status string."""
    try:
        proc = _run_docker(
            [
                "inspect",
                name,
                "--format",
                "{{.State.Status}}|{{.State.Running}}",
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "exists": False,
            "status": "unknown",
            "running": False,
            "error": _short_err(exc),
        }
    if proc.returncode != 0:
        return {
            "exists": False,
            "status": "absent",
            "running": False,
            "error": "",
        }
    raw = (proc.stdout or "").strip()
    status, _, running_raw = raw.partition("|")
    running = running_raw.strip().lower() in {"true", "1"}
    return {
        "exists": True,
        "status": status.strip() or "unknown",
        "running": running,
        "error": "",
    }


def docker_snapshot(
    *,
    image: str | None = None,
    container: str | None = None,
) -> DockerSnapshot:
    """Collect daemon, image, and container layers for the run-tab panel."""
    image_name = image or os.environ.get("WEJAM_IMAGE", _DEFAULT_IMAGE)
    container_name = container or os.environ.get("WEJAM_NAME", _DEFAULT_NAME)
    bin_path = _resolve_docker_bin()
    ok, daemon_error = daemon_ok()
    if not ok:
        return DockerSnapshot(
            docker_bin=bin_path,
            daemon_ok=False,
            daemon_error=daemon_error,
            image=image_name,
            image_present=False,
            image_id="",
            image_created="",
            container=container_name,
            container_status="unknown",
            container_running=False,
            detail=daemon_error,
        )
    img = image_info(image_name)
    ctr = container_info(container_name)
    detail_parts: list[str] = []
    if not img.get("present"):
        detail_parts.append("镜像缺失（需先本地构建 wejam/wechat）")
    if ctr.get("exists") and not ctr.get("running"):
        detail_parts.append(f"容器状态 {ctr.get('status')}")
    if not ctr.get("exists"):
        detail_parts.append("容器未创建")
    return DockerSnapshot(
        docker_bin=bin_path,
        daemon_ok=True,
        daemon_error="",
        image=image_name,
        image_present=bool(img.get("present")),
        image_id=str(img.get("id") or ""),
        image_created=str(img.get("created") or ""),
        container=container_name,
        container_status=str(ctr.get("status") or "absent"),
        container_running=bool(ctr.get("running")),
        detail="；".join(detail_parts),
    )


def start_session() -> None:
    """Start wejam-session via docker/run.sh (replaces any existing container)."""
    if not _RUN_SH.is_file():
        raise FileNotFoundError(f"缺少 {_RUN_SH}")
    ok, err = daemon_ok()
    if not ok:
        raise RuntimeError(f"Docker 引擎未就绪：{err or '请先打开 Docker Desktop'}")
    image_name = os.environ.get("WEJAM_IMAGE", _DEFAULT_IMAGE)
    img = image_info(image_name)
    if not img.get("present"):
        raise RuntimeError(
            f"本地没有镜像 {image_name}，请先按文档构建后再启动"
        )
    env = _subprocess_env()
    try:
        proc = subprocess.run(
            ["bash", str(_RUN_SH)],
            cwd=str(_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=_START_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("启动容器超时") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise RuntimeError(_short_err(msg, limit=400))


def stop_session() -> None:
    """Stop the WeJam session container; missing container is a no-op."""
    name = os.environ.get("WEJAM_NAME", _DEFAULT_NAME)
    ok, err = daemon_ok()
    if not ok:
        raise RuntimeError(f"Docker 引擎未就绪：{err or '请先打开 Docker Desktop'}")
    try:
        proc = _run_docker(["stop", name], timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("停止容器超时") from exc
    if proc.returncode != 0:
        combined = (proc.stderr or proc.stdout or "").lower()
        if "no such container" in combined or "is not running" in combined:
            return
        # Absent container: docker stop exits non-zero with "No such container"
        if "no such" in combined:
            return
        raise RuntimeError(_short_err(proc.stderr or proc.stdout or "stop failed"))


def open_docker_desktop() -> None:
    """Launch Docker Desktop (macOS) or best-effort open on Linux."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Docker"], check=True)
        return
    # Linux: try common desktop entry; do not invent a full service manager.
    for candidate in ("docker-desktop", "Docker Desktop"):
        try:
            subprocess.run(["xdg-open", candidate], check=False, timeout=5)
            return
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError("当前平台无法自动打开 Docker Desktop，请手动启动引擎")
