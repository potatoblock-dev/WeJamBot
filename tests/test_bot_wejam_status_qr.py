"""Unit tests for console QR fetch (cache-first + connect retries)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.admin import wejam_status


def test_login_phase_wants_qr() -> None:
    """QR auto-pull is limited to pre-login phases."""
    assert wejam_status.login_phase_wants_qr("QR_READY") is True
    assert wejam_status.login_phase_wants_qr("ONE_CLICK_READY") is True
    assert wejam_status.login_phase_wants_qr("LOGGED_IN") is False
    assert wejam_status.login_phase_wants_qr("") is False


def test_fetch_qr_png_uses_cached_payload_without_refresh() -> None:
    """When the agent already has a payload, skip the expensive refresh decode."""
    qr = SimpleNamespace(payload="weixin://cached", revision="r1")
    client = MagicMock()
    client.qr_code.return_value = qr
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    fake_img = MagicMock()

    with (
        patch("bot.admin.wejam_status.Client", return_value=client),
        patch("qrcode.make", return_value=fake_img) as make,
    ):
        png, rev = wejam_status.fetch_qr_png("127.0.0.1:7701")

    client.qr_code.assert_called_once_with(refresh=False)
    make.assert_called_once_with("weixin://cached")
    fake_img.save.assert_called_once()
    assert rev == "r1"
    assert isinstance(png, (bytes, bytearray))


def test_fetch_qr_png_refreshes_when_cache_empty() -> None:
    """Empty cached payload triggers a screen re-decode."""
    empty = SimpleNamespace(payload="", revision="")
    filled = SimpleNamespace(payload="weixin://fresh", revision="r2")
    client = MagicMock()
    client.qr_code.side_effect = [empty, filled]
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    fake_img = MagicMock()

    with (
        patch("bot.admin.wejam_status.Client", return_value=client),
        patch("qrcode.make", return_value=fake_img),
    ):
        _png, rev = wejam_status.fetch_qr_png("127.0.0.1:7701")

    assert [c.kwargs.get("refresh") for c in client.qr_code.call_args_list] == [
        False,
        True,
    ]
    assert rev == "r2"


def test_fetch_qr_png_retries_connect_errors() -> None:
    """UNAVAILABLE after restart is retried before giving up."""
    ok = SimpleNamespace(payload="weixin://ok", revision="r3")
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.qr_code.side_effect = [
        RuntimeError("UNAVAILABLE: failed to connect"),
        ok,
    ]
    fake_img = MagicMock()

    with (
        patch("bot.admin.wejam_status.Client", return_value=client),
        patch("bot.admin.wejam_status.time.sleep"),
        patch("qrcode.make", return_value=fake_img),
    ):
        _png, rev = wejam_status.fetch_qr_png("127.0.0.1:7701")

    assert rev == "r3"
    assert client.qr_code.call_count >= 2
