"""Unit tests for host-side container media materialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bot import media_fetch


def test_materialize_local_file(tmp_path: Path) -> None:
    """Already-local readable files are returned unchanged."""
    src = tmp_path / "local.png"
    src.write_bytes(b"png")
    assert media_fetch.materialize_media_path(str(src)) == str(src.resolve())


def test_materialize_container_path_docker_cp(
    tmp_path: Path, monkeypatch
) -> None:
    """Container /data/media paths are copied into the host media dir."""
    host_dir = tmp_path / "media"
    host_dir.mkdir()
    monkeypatch.setattr(media_fetch, "_HOST_MEDIA", host_dir)

    dest = host_dir / "frame.png"

    def _run(cmd, **kwargs):
        # Simulate docker cp writing the dest file.
        dest.write_bytes(b"from-container")
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    with patch("bot.media_fetch.subprocess.run", side_effect=_run) as run:
        out = media_fetch.materialize_media_path(
            "/data/media/frame.png", container="callus-session"
        )

    assert out == str(dest.resolve())
    run.assert_called_once()
    assert run.call_args.args[0][:2] == ["docker", "cp"]
    assert run.call_args.args[0][2] == "callus-session:/data/media/frame.png"


def test_materialize_paths_dedupes(tmp_path: Path, monkeypatch) -> None:
    """Duplicate paths collapse after materialization."""
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    paths = media_fetch.materialize_media_paths([str(src), str(src)])
    assert paths == (str(src.resolve()),)
