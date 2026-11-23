"""Probe wejam gRPC for login/status used by the desktop console."""

from __future__ import annotations

import time
from dataclasses import dataclass
from io import BytesIO

from wejam.client import PHASE_NAME, Client


@dataclass(frozen=True)
class WejamView:
    """Snapshot of the WeChat driver for the admin window."""

    reachable: bool
    ready: bool
    phase: str
    version: str
    detail: str
    error: str = ""


@dataclass(frozen=True)
class AccountView:
    """Account fields shown on the login tab after a successful login."""

    logged_in: bool
    phase: str
    version: str
    detail: str
    since_unix_ms: int
    uptime_ms: int
    chat_count: int
    unread_chats: int
    unread_messages: int
    account_hint: str
    error: str = ""


def probe(target: str) -> WejamView:
    """Call GetStatus; connection errors become reachable=False."""
    try:
        with Client(target, timeout=3) as client:
            status = client.status()
            phase = PHASE_NAME.get(status.login_phase, str(status.login_phase))
            return WejamView(
                reachable=True,
                ready=bool(status.ready),
                phase=phase,
                version=str(status.wechat_version or ""),
                detail="",
            )
    except Exception as exc:  # noqa: BLE001 — show any driver error in the UI
        return WejamView(
            reachable=False,
            ready=False,
            phase="",
            version="",
            detail="",
            error=str(exc),
        )


def _is_connect_error(exc: BaseException) -> bool:
    """True when the failure looks like gRPC not up yet after a container bounce."""
    text = str(exc).lower()
    needles = (
        "unavailable",
        "connection refused",
        "failed to connect",
        "socket closed",
        "statuscode.unavailable",
        "dns resolution failed",
    )
    return any(n in text for n in needles)


def fetch_qr_png(target: str) -> tuple[bytes, str]:
    """Return login QR PNG bytes plus revision; prefer cached payload, then refresh.

    Retries briefly on connect errors so a just-restarted agent can finish binding.
    """
    import qrcode

    last_exc: BaseException | None = None
    for attempt in range(3):
        try:
            with Client(target, timeout=8) as client:
                qr = client.qr_code(refresh=False)
                payload = str(qr.payload or "").strip()
                revision = str(qr.revision or "")
                if not payload:
                    qr = client.qr_code(refresh=True)
                    payload = str(qr.payload or "").strip()
                    revision = str(qr.revision or "") or revision
                if not payload:
                    return b"", revision
                image = qrcode.make(payload)
                buf = BytesIO()
                image.save(buf, format="PNG")
                return buf.getvalue(), revision
        except Exception as exc:  # noqa: BLE001 — retry connect blips, else raise
            last_exc = exc
            if attempt < 2 and _is_connect_error(exc):
                time.sleep(1.0)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def submit_login(target: str) -> str:
    """Trigger one-click login when the driver is in that phase."""
    with Client(target, timeout=8) as client:
        state = client.submit_login()
        return PHASE_NAME.get(state.phase, str(state.phase))


def logout(target: str) -> str:
    """Sign out via WeJam Logout; return the post-logout phase label."""
    with Client(target, timeout=60) as client:
        state = client.logout()
        return PHASE_NAME.get(state.phase, str(state.phase))


def account_summary(target: str) -> AccountView:
    """Fetch account summary for the login tab; failures stay in error."""
    try:
        with Client(target, timeout=8) as client:
            summary = client.account_summary()
            return AccountView(
                logged_in=bool(summary.logged_in),
                phase=PHASE_NAME.get(summary.login_phase, str(summary.login_phase)),
                version=str(summary.wechat_version or ""),
                detail=str(summary.login_detail or ""),
                since_unix_ms=int(summary.login_since_unix_ms or 0),
                uptime_ms=int(summary.uptime_ms or 0),
                chat_count=int(summary.chat_count or 0),
                unread_chats=int(summary.unread_chats or 0),
                unread_messages=int(summary.unread_messages or 0),
                account_hint=str(summary.account_hint or ""),
            )
    except Exception as exc:  # noqa: BLE001 — show any driver error in the UI
        return AccountView(
            logged_in=False,
            phase="",
            version="",
            detail="",
            since_unix_ms=0,
            uptime_ms=0,
            chat_count=0,
            unread_chats=0,
            unread_messages=0,
            account_hint="",
            error=str(exc),
        )


def login_phase_wants_qr(phase: str) -> bool:
    """True when the login panel should auto-pull a QR for this phase label."""
    token = (phase or "").strip().upper()
    if not token or token == "LOGGED_IN":
        return False
    return token in {
        "STARTING",
        "QR_READY",
        "ONE_CLICK_READY",
        "WAIT_PHONE",
        "UNKNOWN",
        "UNSPECIFIED",
    }
