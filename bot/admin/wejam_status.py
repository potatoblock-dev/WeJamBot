"""Probe wejam gRPC for login/status used by the desktop console."""

from __future__ import annotations

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


def fetch_qr_png(target: str) -> tuple[bytes, str]:
    """Return PNG bytes for the current login QR payload, plus revision."""
    with Client(target, timeout=8) as client:
        qr = client.qr_code()
        payload = str(qr.payload or "")
        if not payload:
            return b"", str(qr.revision or "")
        import qrcode

        image = qrcode.make(payload)
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue(), str(qr.revision or "")


def submit_login(target: str) -> str:
    """Trigger one-click login when the driver is in that phase."""
    with Client(target, timeout=8) as client:
        state = client.submit_login()
        return PHASE_NAME.get(state.phase, str(state.phase))
