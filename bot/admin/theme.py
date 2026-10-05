"""Neutral dark-gray Qt stylesheet for the admin desktop."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

DARK_STYLESHEET = """
QWidget {
    background-color: #2d2d2d;
    color: #e8e8e8;
}
QMainWindow, QDialog {
    background-color: #2d2d2d;
}
QGroupBox {
    border: 1px solid #505050;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
    color: #d0d0d0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QPlainTextEdit, QTextEdit, QLineEdit, QListWidget {
    background-color: #252525;
    color: #e8e8e8;
    border: 1px solid #505050;
    selection-background-color: #555555;
}
QTextEdit#logView {
    background-color: #1a1a1a;
    border: 1px solid #454545;
}
QTabWidget::pane {
    border: 1px solid #505050;
    background: #2d2d2d;
}
QTabBar::tab {
    background: #383838;
    color: #c0c0c0;
    padding: 6px 14px;
    border: 1px solid #505050;
    border-bottom: none;
}
QTabBar::tab:selected {
    background: #454545;
    color: #f0f0f0;
}
QPushButton {
    background-color: #404040;
    color: #e8e8e8;
    border: 1px solid #555555;
    padding: 5px 12px;
    border-radius: 3px;
}
QPushButton:hover {
    background-color: #4a4a4a;
}
QPushButton:pressed {
    background-color: #353535;
}
QPushButton:disabled {
    background-color: #333333;
    color: #777777;
}
QPushButton:checked {
    background-color: #5a5a5a;
    color: #ffffff;
    border: 1px solid #888888;
}
QPushButton#dangerConfirm {
    background-color: #c0392b;
    color: #ffffff;
    border: 1px solid #a93226;
}
QPushButton#dangerConfirm:hover {
    background-color: #e74c3c;
}
QPushButton#dangerConfirm:pressed {
    background-color: #922b21;
}
QToolButton {
    background: transparent;
    border: none;
    color: #c0c0c0;
    padding: 2px 4px;
}
QToolButton:hover {
    color: #f0f0f0;
}
QMenuBar {
    background-color: #2d2d2d;
    color: #e8e8e8;
}
QMenuBar::item:selected {
    background: #404040;
}
QLabel {
    background: transparent;
    color: #e0e0e0;
}
QLabel[role="warning"] {
    color: #d4a574;
}
QLabel[role="muted"] {
    color: #9a9a9a;
}
QLabel[role="accent"] {
    color: #c8c8c8;
}
QSplitter::handle {
    background: #505050;
}
QScrollArea {
    background-color: #2d2d2d;
    border: none;
}
QScrollBar:vertical {
    background: #2d2d2d;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #555555;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #666666;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Apply the neutral gray dark palette to the admin application."""
    app.setStyleSheet(DARK_STYLESHEET)
