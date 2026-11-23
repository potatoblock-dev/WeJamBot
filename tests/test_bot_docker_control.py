"""Unit tests for session container resume (login-preserving start)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.admin import docker_control


def test_session_container_name_maps_7701_to_callus(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEJAM_TARGET :7701 resolves to callus-session."""
    monkeypatch.delenv("WEJAM_NAME", raising=False)
    monkeypatch.setenv("WEJAM_TARGET", "127.0.0.1:7701")
    assert docker_control.session_container_name() == "callus-session"


def test_start_session_noop_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Already-running containers must not be recreated."""
    monkeypatch.setenv("WEJAM_NAME", "callus-session")
    with (
        patch.object(docker_control, "daemon_ok", return_value=(True, "")),
        patch.object(
            docker_control,
            "container_info",
            return_value={"exists": True, "running": True, "status": "running"},
        ),
        patch.object(docker_control, "_run_docker") as run_docker,
        patch("subprocess.run") as sub_run,
    ):
        docker_control.start_session()
    run_docker.assert_not_called()
    sub_run.assert_not_called()


def test_start_session_resumes_stopped_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped containers are docker start'd — no rm, no run.sh recreate."""
    monkeypatch.setenv("WEJAM_NAME", "callus-session")
    proc = MagicMock()
    proc.returncode = 0
    proc.stderr = ""
    proc.stdout = ""
    with (
        patch.object(docker_control, "daemon_ok", return_value=(True, "")),
        patch.object(
            docker_control,
            "container_info",
            return_value={"exists": True, "running": False, "status": "exited"},
        ),
        patch.object(docker_control, "_run_docker", return_value=proc) as run_docker,
        patch.object(docker_control, "wait_session_ready") as wait_ready,
        patch("subprocess.run") as sub_run,
    ):
        docker_control.start_session()
    run_docker.assert_called_once_with(["start", "callus-session"], timeout=120)
    wait_ready.assert_called_once()
    sub_run.assert_not_called()


def test_restart_session_calls_docker_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing containers use a single docker restart then wait for gRPC."""
    monkeypatch.setenv("WEJAM_NAME", "callus-session")
    proc = MagicMock()
    proc.returncode = 0
    proc.stderr = ""
    proc.stdout = ""
    with (
        patch.object(docker_control, "daemon_ok", return_value=(True, "")),
        patch.object(
            docker_control,
            "container_info",
            return_value={"exists": True, "running": True, "status": "running"},
        ),
        patch.object(docker_control, "_run_docker", return_value=proc) as run_docker,
        patch.object(docker_control, "wait_session_ready") as wait_ready,
        patch.object(docker_control, "start_session") as start,
    ):
        docker_control.restart_session()
    run_docker.assert_called_once_with(["restart", "callus-session"], timeout=120)
    wait_ready.assert_called_once()
    start.assert_not_called()


def test_wait_session_ready_succeeds_when_probe_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wait_session_ready returns as soon as container is up and probe works."""
    monkeypatch.setenv("WEJAM_NAME", "callus-session")
    monkeypatch.setenv("WEJAM_TARGET", "127.0.0.1:7701")
    view = MagicMock()
    view.reachable = True
    view.error = ""
    with (
        patch.object(
            docker_control,
            "container_info",
            return_value={"exists": True, "running": True, "status": "running"},
        ),
        patch("bot.admin.wejam_status.probe", return_value=view) as probe,
    ):
        docker_control.wait_session_ready(timeout=5)
    probe.assert_called()


def test_wait_session_ready_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreachable gRPC eventually raises RuntimeError."""
    monkeypatch.setenv("WEJAM_NAME", "callus-session")
    monkeypatch.setenv("WEJAM_TARGET", "127.0.0.1:7701")
    view = MagicMock()
    view.reachable = False
    view.error = "unavailable"
    with (
        patch.object(
            docker_control,
            "container_info",
            return_value={"exists": True, "running": True, "status": "running"},
        ),
        patch("bot.admin.wejam_status.probe", return_value=view),
        patch.object(docker_control, "_READY_POLL_SECONDS", 0.01),
        pytest.raises(RuntimeError, match="等待会话就绪超时"),
    ):
        docker_control.wait_session_ready(timeout=0.05)
