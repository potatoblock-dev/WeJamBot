"""Unit tests for WeChat view popup dismiss (mocked docker exec)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from bot.admin import wechat_view


def _proc(*, code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> SimpleNamespace:
    """Build a fake CompletedProcess-like object."""
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_dismiss_popups_already_clear(monkeypatch) -> None:
    """No extra windows → dismiss returns empty without tapping Escape."""
    calls: list[str] = []

    def fake_run(cmd, **_kwargs):
        script = " ".join(str(c) for c in cmd)
        if "extras" in script or _looks_like_extra_script(cmd):
            calls.append("count")
            body = json.dumps({"main_area": 1000000, "extras": []}).encode()
            return _proc(stdout=body)
        calls.append("other")
        return _proc()

    monkeypatch.setattr(wechat_view.subprocess, "run", fake_run)
    monkeypatch.setattr(wechat_view, "session_container_name", lambda: "c1")
    assert wechat_view.dismiss_popups() == ""
    assert calls == ["count"]


def test_dismiss_popups_clears_after_escape(monkeypatch) -> None:
    """One popup then Esc → recount empty → success."""
    state = {"n": 0}
    taps: list[str] = []

    def fake_run(cmd, **_kwargs):
        joined = " ".join(str(c) for c in cmd)
        if _is_key_script(cmd):
            taps.append(cmd[-1] if cmd else "")
            return _proc()
        if _is_extra_script(cmd):
            if state["n"] == 0:
                state["n"] = 1
                body = json.dumps(
                    {
                        "main_area": 900000,
                        "extras": [{"w": 400, "h": 300, "area": 120000}],
                    }
                ).encode()
            else:
                body = json.dumps({"main_area": 900000, "extras": []}).encode()
            return _proc(stdout=body)
        raise AssertionError(f"unexpected cmd: {joined}")

    monkeypatch.setattr(wechat_view.subprocess, "run", fake_run)
    monkeypatch.setattr(wechat_view, "session_container_name", lambda: "c1")
    monkeypatch.setattr(wechat_view.time, "sleep", lambda _s: None)
    assert wechat_view.dismiss_popups(settle=0.01) == ""
    assert taps == ["Escape"]


def test_dismiss_popups_still_present(monkeypatch) -> None:
    """Popup survives three Escape rounds → Chinese warning."""
    taps: list[str] = []

    def fake_run(cmd, **_kwargs):
        if _is_key_script(cmd):
            taps.append("Escape")
            return _proc()
        if _is_extra_script(cmd):
            body = json.dumps(
                {
                    "main_area": 900000,
                    "extras": [{"w": 400, "h": 300, "area": 120000}],
                }
            ).encode()
            return _proc(stdout=body)
        return _proc()

    monkeypatch.setattr(wechat_view.subprocess, "run", fake_run)
    monkeypatch.setattr(wechat_view, "session_container_name", lambda: "c1")
    monkeypatch.setattr(wechat_view.time, "sleep", lambda _s: None)
    msg = wechat_view.dismiss_popups(max_rounds=3, settle=0.01)
    assert "仍有弹层" in msg
    assert len(taps) == 3


def test_extra_popup_count_parses_json(monkeypatch) -> None:
    """Counter returns the length of the extras list."""

    def fake_run(cmd, **_kwargs):
        body = json.dumps(
            {
                "main_area": 1,
                "extras": [
                    {"w": 200, "h": 100, "area": 20000},
                    {"w": 200, "h": 100, "area": 20000},
                ],
            }
        ).encode()
        return _proc(stdout=body)

    monkeypatch.setattr(wechat_view.subprocess, "run", fake_run)
    monkeypatch.setattr(wechat_view, "session_container_name", lambda: "c1")
    count, err = wechat_view.extra_popup_count()
    assert err == ""
    assert count == 2


def _is_extra_script(cmd: list) -> bool:
    """True when docker exec runs the extra-windows Python snippet."""
    return any("main_area" in str(part) or "extras" in str(part) for part in cmd)


def _looks_like_extra_script(cmd: list) -> bool:
    """Alias for readability in the already-clear test."""
    return _is_extra_script(cmd)


def _is_key_script(cmd: list) -> bool:
    """True when docker exec runs the key-injection snippet."""
    return any("string_to_keysym" in str(part) for part in cmd)
