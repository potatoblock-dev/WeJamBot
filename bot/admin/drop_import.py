"""Qt helpers: accept dragged text/json files and notify the admin window."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QWidget

_ACCEPT_SUFFIXES = {".txt", ".md", ".text", ".json"}


class FileDropFilter(QObject):
    """Event filter that accepts local text/json file drops onto a widget."""

    files_dropped = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Attach to ``parent`` so the filter lives as long as the widget."""
        super().__init__(parent)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Handle drag-enter / drop for local importable files."""
        etype = event.type()
        if etype == QEvent.Type.DragEnter and isinstance(event, QDragEnterEvent):
            if _urls_have_importable(event.mimeData().urls()):
                event.acceptProposedAction()
                return True
            return False
        if etype == QEvent.Type.DragMove and isinstance(event, QDragMoveEvent):
            if _urls_have_importable(event.mimeData().urls()):
                event.acceptProposedAction()
                return True
            return False
        if etype == QEvent.Type.Drop and isinstance(event, QDropEvent):
            paths = _paths_from_urls(event.mimeData().urls())
            if paths:
                event.acceptProposedAction()
                self.files_dropped.emit(paths)
                return True
            return False
        return super().eventFilter(watched, event)


def enable_file_drop(widget: QWidget, on_paths) -> FileDropFilter:
    """Turn on drops for ``widget`` and call ``on_paths(list[Path])`` when files arrive."""
    widget.setAcceptDrops(True)
    filt = FileDropFilter(widget)
    filt.files_dropped.connect(on_paths)
    widget.installEventFilter(filt)
    tip = widget.toolTip() or ""
    extra = "可拖入 .txt / .md / .json 导入。"
    if extra not in tip:
        widget.setToolTip((tip + " " + extra).strip())
    # Keep a Python ref on the widget so the filter is not GC'd.
    setattr(widget, "_wejam_drop_filter", filt)
    return filt


def _urls_have_importable(urls: list) -> bool:
    """True when at least one local URL looks importable."""
    return bool(_paths_from_urls(urls))


def _paths_from_urls(urls: list) -> list[Path]:
    """Convert QUrls to local Paths, filtering to importable suffixes or dirs."""
    out: list[Path] = []
    for url in urls:
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if path.is_dir():
            out.append(path)
            continue
        if path.suffix.lower() in _ACCEPT_SUFFIXES:
            out.append(path)
    return out
