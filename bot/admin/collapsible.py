"""Collapsible section header with a triangle toggle for the admin desktop."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _ClickableLabel(QLabel):
    """Label that emits clicked on left mouse press."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """Treat a left click like pressing the fold toggle."""
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CollapsibleSection(QWidget):
    """Section title with a triangle; hides or shows the wrapped editor below."""

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = True,
        content_min_height: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._content = content
        self._expanded = expanded
        self._content_min_height = max(0, content_min_height)
        if self._content_min_height:
            self._content.setMinimumHeight(self._content_min_height)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._header = QWidget()
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self._toggle = QToolButton()
        self._toggle.setAutoRaise(True)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.clicked.connect(self.toggle)
        header.addWidget(self._toggle)

        self._title = _ClickableLabel(title)
        self._title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title.clicked.connect(self.toggle)
        header.addWidget(self._title)
        header.addStretch()
        layout.addWidget(self._header)
        layout.addWidget(content)

        self._apply_state()

    @property
    def is_expanded(self) -> bool:
        """True when the section body is visible."""
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide content without emitting toggled when state is unchanged."""
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._apply_state()
        self.toggled.emit(self._expanded)

    def toggle(self) -> None:
        """Expand or collapse the section content."""
        self.set_expanded(not self._expanded)

    def _apply_state(self) -> None:
        """Sync arrow direction and content visibility with expanded state."""
        arrow = Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow
        self._toggle.setArrowType(arrow)
        if self._expanded:
            self._content.setVisible(True)
            if self._content_min_height:
                self._content.setMinimumHeight(self._content_min_height)
            self._content.setMaximumHeight(16777215)
            policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        else:
            self._content.setMinimumHeight(0)
            self._content.setMaximumHeight(0)
            self._content.setVisible(False)
            policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._content.setSizePolicy(policy)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, policy.verticalPolicy())
        if not self._expanded:
            self.setMaximumHeight(self._header.sizeHint().height())
        else:
            self.setMaximumHeight(16777215)
        self.updateGeometry()
