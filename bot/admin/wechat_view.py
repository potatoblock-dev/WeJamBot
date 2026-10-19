"""Grab a view-only PNG of the WeChat Xvfb screen inside the session container."""

from __future__ import annotations

import os
import subprocess

from bot.admin.docker_control import _resolve_docker_bin, _subprocess_env, docker_snapshot

_DEFAULT_NAME = "wejam-session"
_CAPTURE_TIMEOUT = 8

# Runs inside the container. Xlib + Pillow are already in the image.
_CAPTURE_PY = r"""
import sys
from Xlib import X, display
from PIL import Image

d = display.Display()
root = d.screen().root
geom = root.get_geometry()
width, height = geom.width, geom.height
raw = root.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF)
data = raw.data
expected = width * height * 4
if len(data) < expected:
    sys.stderr.write(f"short frame {len(data)} < {expected}\n")
    sys.exit(1)
image = Image.frombytes("RGB", (width, height), data[:expected], "raw", "BGRX")
image.save(sys.stdout.buffer, format="PNG")
"""


def capture_png(container: str | None = None) -> tuple[bytes | None, str]:
    """Return a PNG of the container root window, or (None, reason).

    The grab is read-only: nothing is sent back to the X server except the
    GetImage request. Failures stay as a short string for the view dialog.
    """
    name = container or os.environ.get("WEJAM_NAME", _DEFAULT_NAME)
    snap = docker_snapshot(container=name)
    if not snap.daemon_ok:
        return None, "Docker 引擎未就绪"
    if not snap.container_running:
        return None, "容器未开启"
    try:
        proc = subprocess.run(
            [
                _resolve_docker_bin(),
                "exec",
                "-e",
                "DISPLAY=:77",
                name,
                "python3",
                "-c",
                _CAPTURE_PY,
            ],
            capture_output=True,
            timeout=_CAPTURE_TIMEOUT,
            env=_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"抓图失败：{exc}"
    if proc.returncode != 0 or not proc.stdout.startswith(b"\x89PNG"):
        err = proc.stderr.decode("utf-8", "replace").strip().replace("\n", " ")
        if len(err) > 180:
            err = err[:179] + "…"
        return None, err or "抓图失败"
    return proc.stdout, ""
