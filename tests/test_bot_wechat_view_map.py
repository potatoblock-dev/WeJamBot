"""Coordinate mapping helpers for the interactive WeChat view."""

from __future__ import annotations

from bot.admin.wechat_view import map_label_to_frame, png_size


def test_png_size_reads_ihdr() -> None:
    """PNG size reader uses IHDR big-endian width/height at bytes 16..24."""
    ihdr = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (1280).to_bytes(4, "big")
        + (900).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
    )
    assert png_size(ihdr) == (1280, 900)


def test_map_label_centers_keep_aspect() -> None:
    """Clicks map through letterboxed KeepAspectRatio layout."""
    # Frame 100x100 shown in 200x100 label → scale 1, x offset 50.
    assert map_label_to_frame(50, 0, 200, 100, 100, 100) == (0, 0)
    assert map_label_to_frame(149, 99, 200, 100, 100, 100) == (99, 99)
    assert map_label_to_frame(10, 50, 200, 100, 100, 100) is None
