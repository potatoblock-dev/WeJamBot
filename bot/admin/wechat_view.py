"""Grab / drive the WeChat Xvfb screen inside the session container."""

from __future__ import annotations

import json
import subprocess
import time

from bot.admin.docker_control import (
    _resolve_docker_bin,
    _subprocess_env,
    session_container_name,
)

_CAPTURE_TIMEOUT = 8
_INPUT_TIMEOUT = 5
# Match Callus agent/chat.py POPUP_MIN_AREA: ignore tiny tooltips.
_POPUP_MIN_AREA = 120 * 80

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

_CLICK_PY = r"""
import sys, time
from Xlib import X, display
from Xlib.ext import xtest

x, y, button = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
d = display.Display()
root = d.screen().root
geom = root.get_geometry()
w, h = geom.width, geom.height
x = max(0, min(w - 1, x))
y = max(0, min(h - 1, y))
root.warp_pointer(x, y)
d.sync()
time.sleep(0.04)
xtest.fake_input(d, X.ButtonPress, button)
d.sync()
time.sleep(0.04)
xtest.fake_input(d, X.ButtonRelease, button)
d.sync()
"""

_KEY_PY = r"""
import sys, time
from Xlib import X, XK, display
from Xlib.ext import xtest

name = sys.argv[1]
d = display.Display()
ks = XK.string_to_keysym(name)
if ks == 0:
    sys.stderr.write(f"unknown key {name}\n")
    sys.exit(1)
kc = d.keysym_to_keycode(ks)
if kc == 0:
    sys.stderr.write(f"no keycode for {name}\n")
    sys.exit(1)
xtest.fake_input(d, X.KeyPress, kc)
d.sync()
time.sleep(0.03)
xtest.fake_input(d, X.KeyRelease, kc)
d.sync()
"""

# List viewable top-level windows; extras = non-largest with area >= min_area.
_EXTRA_WINDOWS_PY = r"""
import json, sys
from Xlib import X, display

min_area = int(sys.argv[1])
d = display.Display()
root = d.screen().root
wins = []
for w in root.query_tree().children:
    try:
        if w.get_attributes().map_state != X.IsViewable:
            continue
        g = w.get_geometry()
        if g.width <= 1 or g.height <= 1:
            continue
        area = int(g.width) * int(g.height)
        wins.append({"w": int(g.width), "h": int(g.height), "area": area})
    except Exception:
        continue
if not wins:
    print(json.dumps({"main_area": 0, "extras": []}))
    raise SystemExit(0)
main = max(wins, key=lambda row: row["area"])
extras = [
    row for row in wins
    if row is not main and row["area"] >= min_area
]
print(json.dumps({"main_area": main["area"], "extras": extras}))
"""


def capture_png(container: str | None = None) -> tuple[bytes | None, str]:
    """Return a PNG of the container root window, or (None, reason).

    Skips a full docker_snapshot on the hot path so the view timer cannot
    pile up behind repeated `docker inspect` calls.
    """
    name = container or session_container_name()
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
    except subprocess.TimeoutExpired:
        return None, "抓图超时"
    except OSError as exc:
        return None, f"抓图失败：{exc}"
    if proc.returncode != 0 or not proc.stdout.startswith(b"\x89PNG"):
        err = proc.stderr.decode("utf-8", "replace").strip().replace("\n", " ")
        low = err.lower()
        if "no such container" in low or "is not running" in low:
            return None, "容器未开启"
        if len(err) > 180:
            err = err[:179] + "…"
        return None, err or "抓图失败"
    return proc.stdout, ""


def png_size(png: bytes) -> tuple[int, int]:
    """Read width/height from a PNG without full decode when possible."""
    if len(png) < 24 or not png.startswith(b"\x89PNG"):
        return 0, 0
    # IHDR: bytes 16..24 are width/height big-endian
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return width, height


def map_label_to_frame(
    label_x: float,
    label_y: float,
    label_w: int,
    label_h: int,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int] | None:
    """Map a click on a KeepAspectRatio-centered label onto Xvfb root pixels."""
    if label_w < 2 or label_h < 2 or frame_w < 1 or frame_h < 1:
        return None
    scale = min(label_w / frame_w, label_h / frame_h)
    disp_w = frame_w * scale
    disp_h = frame_h * scale
    ox = (label_w - disp_w) / 2.0
    oy = (label_h - disp_h) / 2.0
    if (
        label_x < ox
        or label_y < oy
        or label_x > ox + disp_w
        or label_y > oy + disp_h
    ):
        return None
    fx = int((label_x - ox) / scale)
    fy = int((label_y - oy) / scale)
    fx = max(0, min(frame_w - 1, fx))
    fy = max(0, min(frame_h - 1, fy))
    return fx, fy


def click_at(
    x: int,
    y: int,
    *,
    button: int = 1,
    container: str | None = None,
) -> str:
    """Inject a mouse click at Xvfb root coordinates. Empty string on success."""
    name = container or session_container_name()
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
                _CLICK_PY,
                str(int(x)),
                str(int(y)),
                str(int(button)),
            ],
            capture_output=True,
            timeout=_INPUT_TIMEOUT,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return "点击超时"
    except OSError as exc:
        return f"点击失败：{exc}"
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().replace("\n", " ")
        low = err.lower()
        if "no such container" in low or "is not running" in low:
            return "容器未开启"
        if len(err) > 180:
            err = err[:179] + "…"
        return err or "点击失败"
    return ""


def tap_key(keysym_name: str, *, container: str | None = None) -> str:
    """Inject one key tap (X keysym name, e.g. Escape / Return). Empty on success."""
    name = container or session_container_name()
    key = (keysym_name or "").strip()
    if not key:
        return "空键名"
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
                _KEY_PY,
                key,
            ],
            capture_output=True,
            timeout=_INPUT_TIMEOUT,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return "按键超时"
    except OSError as exc:
        return f"按键失败：{exc}"
    if proc.returncode != 0:
        return _format_docker_err(proc.stderr, "按键失败")
    return ""


def extra_popup_count(container: str | None = None) -> tuple[int, str]:
    """Count non-main X11 windows large enough to be popups.

    Returns ``(count, "")`` on success, or ``(-1, reason)`` on failure.
    """
    name = container or session_container_name()
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
                _EXTRA_WINDOWS_PY,
                str(_POPUP_MIN_AREA),
            ],
            capture_output=True,
            timeout=_INPUT_TIMEOUT,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return -1, "检测弹层超时"
    except OSError as exc:
        return -1, f"检测弹层失败：{exc}"
    if proc.returncode != 0:
        return -1, _format_docker_err(proc.stderr, "检测弹层失败")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        return -1, "检测弹层返回无效 JSON"
    extras = data.get("extras") if isinstance(data, dict) else None
    if not isinstance(extras, list):
        return -1, "检测弹层结果格式错误"
    return len(extras), ""


def dismiss_popups(
    *,
    max_rounds: int = 3,
    settle: float = 0.35,
    container: str | None = None,
) -> str:
    """Press Escape until no large extra windows remain.

    Returns an empty string when clear (or already clear). Otherwise a short
    Chinese reason for the UI status line.
    """
    rounds = max(1, int(max_rounds))
    pause = max(0.05, float(settle))
    count, err = extra_popup_count(container=container)
    if err:
        return err
    if count <= 0:
        return ""
    for _ in range(rounds):
        key_err = tap_key("Escape", container=container)
        if key_err:
            return key_err
        time.sleep(pause)
        count, err = extra_popup_count(container=container)
        if err:
            return err
        if count <= 0:
            return ""
    return f"仍有弹层（约 {count} 个），可再点「关闭弹层」或 Esc"


def _format_docker_err(stderr: bytes | str, fallback: str) -> str:
    """Normalize docker/exec stderr into a short UI-facing Chinese message."""
    if isinstance(stderr, bytes):
        err = stderr.decode("utf-8", "replace").strip().replace("\n", " ")
    else:
        err = (stderr or "").strip().replace("\n", " ")
    low = err.lower()
    if "no such container" in low or "is not running" in low:
        return "容器未开启"
    if len(err) > 180:
        err = err[:179] + "…"
    return err or fallback
