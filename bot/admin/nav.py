"""Primary / secondary navigation shell for the admin console."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class NavPage:
    """One secondary page under a primary section."""

    key: str
    title: str


@dataclass(frozen=True)
class NavSection:
    """One primary section and its secondary pages (empty = single page)."""

    key: str
    title: str
    pages: tuple[NavPage, ...] = ()


class ConsoleNav(QWidget):
    """Left primary list, optional secondary segment buttons, and a page stack."""

    section_changed = pyqtSignal(str)
    page_changed = pyqtSignal(str, str)

    def __init__(self, sections: tuple[NavSection, ...], parent: QWidget | None = None) -> None:
        """Build the shell; call ``register`` for each page before showing."""
        super().__init__(parent)
        if not sections:
            raise ValueError("ConsoleNav needs at least one section")
        self._sections = {s.key: s for s in sections}
        self._order = [s.key for s in sections]
        self._page_index: dict[tuple[str, str], int] = {}
        self._footers: dict[str, QWidget] = {}
        self._current_section = sections[0].key
        self._current_page: dict[str, str] = {
            s.key: (s.pages[0].key if s.pages else s.key) for s in sections
        }

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav_list = QListWidget()
        self._nav_list.setObjectName("consoleNav")
        self._nav_list.setFixedWidth(108)
        self._nav_list.setSpacing(2)
        for section in sections:
            item = QListWidgetItem(section.title)
            item.setData(Qt.ItemDataRole.UserRole, section.key)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._nav_list.addItem(item)
        self._nav_list.setCurrentRow(0)
        self._nav_list.currentItemChanged.connect(self._on_section_item)
        root.addWidget(self._nav_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 6, 8, 6)
        right_layout.setSpacing(6)

        self._segment_bar = QWidget()
        self._segment_bar.setObjectName("consoleSegmentBar")
        self._segment_layout = QHBoxLayout(self._segment_bar)
        self._segment_layout.setContentsMargins(0, 0, 0, 0)
        self._segment_layout.setSpacing(4)
        self._segment_group = QButtonGroup(self)
        self._segment_group.setExclusive(True)
        self._segment_group.idClicked.connect(self._on_segment_id)
        self._segment_buttons: list[QPushButton] = []
        right_layout.addWidget(self._segment_bar)

        self._stack = QStackedWidget()
        right_layout.addWidget(self._stack, stretch=1)

        self._footer_host = QWidget()
        self._footer_layout = QVBoxLayout(self._footer_host)
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_layout.setSpacing(0)
        self._footer_host.hide()
        right_layout.addWidget(self._footer_host)

        root.addWidget(right, stretch=1)

        self._rebuild_segments()
        self._sync_footer()

    def register(self, section_key: str, page_key: str, widget: QWidget) -> None:
        """Add a page widget for ``(section_key, page_key)``."""
        if section_key not in self._sections:
            raise KeyError(f"unknown section {section_key!r}")
        section = self._sections[section_key]
        if section.pages:
            if page_key not in {p.key for p in section.pages}:
                raise KeyError(f"unknown page {page_key!r} in {section_key}")
        elif page_key != section_key:
            raise KeyError(f"single-page section {section_key} expects page_key={section_key}")
        key = (section_key, page_key)
        if key in self._page_index:
            raise KeyError(f"page already registered: {key}")
        index = self._stack.addWidget(widget)
        self._page_index[key] = index

    def set_section_footer(self, section_key: str, widget: QWidget) -> None:
        """Attach a footer shown only while ``section_key`` is active."""
        if section_key not in self._sections:
            raise KeyError(f"unknown section {section_key!r}")
        old = self._footers.pop(section_key, None)
        if old is not None:
            self._footer_layout.removeWidget(old)
            old.hide()
            old.setParent(None)
        self._footers[section_key] = widget
        self._footer_layout.addWidget(widget)
        widget.hide()
        self._sync_footer()

    def current_section(self) -> str:
        """Return the active primary section key."""
        return self._current_section

    def current_page(self) -> str:
        """Return the active secondary page key for the current section."""
        return self._current_page[self._current_section]

    def show_page(self, section_key: str, page_key: str | None = None) -> None:
        """Select a section and optional page without emitting user signals twice."""
        if section_key not in self._sections:
            raise KeyError(section_key)
        section = self._sections[section_key]
        target = page_key or self._current_page[section_key]
        if section.pages:
            if target not in {p.key for p in section.pages}:
                target = section.pages[0].key
        else:
            target = section_key
        self._current_section = section_key
        self._current_page[section_key] = target
        for row in range(self._nav_list.count()):
            item = self._nav_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == section_key:
                self._nav_list.blockSignals(True)
                self._nav_list.setCurrentRow(row)
                self._nav_list.blockSignals(False)
                break
        self._rebuild_segments()
        self._apply_stack()
        self._sync_footer()

    def _on_section_item(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        """Switch primary section when the left list selection changes."""
        if current is None:
            return
        key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if not key or key == self._current_section:
            return
        self._current_section = key
        self._rebuild_segments()
        self._apply_stack()
        self._sync_footer()
        self.section_changed.emit(key)
        self.page_changed.emit(key, self._current_page[key])

    def _on_segment_id(self, button_id: int) -> None:
        """Switch secondary page from a segment button id."""
        section = self._sections[self._current_section]
        if not section.pages or button_id < 0 or button_id >= len(section.pages):
            return
        page_key = section.pages[button_id].key
        if page_key == self._current_page[self._current_section]:
            return
        self._current_page[self._current_section] = page_key
        self._apply_stack()
        self.page_changed.emit(self._current_section, page_key)

    def _rebuild_segments(self) -> None:
        """Rebuild the secondary segment row for the current section."""
        while self._segment_buttons:
            btn = self._segment_buttons.pop()
            self._segment_group.removeButton(btn)
            btn.deleteLater()
        section = self._sections[self._current_section]
        if not section.pages:
            self._segment_bar.hide()
            return
        self._segment_bar.show()
        current = self._current_page[self._current_section]
        for index, page in enumerate(section.pages):
            btn = QPushButton(page.title)
            btn.setCheckable(True)
            btn.setObjectName("consoleSegment")
            btn.setChecked(page.key == current)
            self._segment_group.addButton(btn, index)
            self._segment_layout.addWidget(btn)
            self._segment_buttons.append(btn)
        self._segment_layout.addStretch(1)

    def _apply_stack(self) -> None:
        """Show the stack page for the current section/page pair."""
        page_key = self._current_page[self._current_section]
        index = self._page_index.get((self._current_section, page_key))
        if index is not None:
            self._stack.setCurrentIndex(index)

    def _sync_footer(self) -> None:
        """Show only the footer that belongs to the active primary section."""
        active = self._current_section
        any_visible = False
        for key, widget in self._footers.items():
            show = key == active
            widget.setVisible(show)
            any_visible = any_visible or show
        self._footer_host.setVisible(any_visible)
