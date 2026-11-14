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
        patch("subprocess.run") as sub_run,
    ):
        docker_control.start_session()
    run_docker.assert_called_once_with(["start", "callus-session"], timeout=120)
    sub_run.assert_not_called()
