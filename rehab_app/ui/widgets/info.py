"""Small "i" info icon, label-with-info helper and a collapsible "Advanced" section."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QToolButton, QToolTip, QVBoxLayout, QWidget)

from .. import theme
from ..info_text import INFO


class InfoIcon(QToolButton):
    """Circled "i": hover or click to read the explanation for the number next to it."""

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._tip = INFO[key]
        self.setText("i")
        self.setToolTip(self._tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(16, 16)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            f"QToolButton {{ border: 1.5px solid {theme.MUTED}; border-radius: 8px; "
            f"color: {theme.MUTED}; font: italic bold 10px 'Georgia'; padding: 0; "
            f"background: transparent; }}"
            f"QToolButton:hover {{ border-color: {theme.ACCENT}; color: {theme.ACCENT}; }}")
        self.clicked.connect(self._show)

    def _show(self):
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height() + 2)), self._tip, self)


def with_info(label: str | QWidget, key: str) -> QWidget:
    """A label with the "i" icon at its top-right corner (like a superscript)."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    lay.addWidget(QLabel(label) if isinstance(label, str) else label)
    lay.addWidget(InfoIcon(key), 0, Qt.AlignTop)
    lay.addStretch()
    w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    return w


def header_info(table: QTableWidget, col: int, text: str, key: str) -> None:
    """Table column header with an ⓘ marker; hover shows the explanation."""
    item = QTableWidgetItem(f"{text}  ⓘ")
    item.setToolTip(INFO[key])
    table.setHorizontalHeaderItem(col, item)


class Collapsible(QWidget):
    """"▸ Advanced" toggle that shows/hides its content (collapsed by default)."""

    def __init__(self, title: str = "Advanced", parent=None):
        super().__init__(parent)
        self._title = title
        self.toggle = QToolButton()
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setText(title)
        self.toggle.setStyleSheet(f"QToolButton {{ border: none; color: {theme.MUTED}; "
                                  f"font-weight: 600; }}")
        self.toggle.toggled.connect(self._toggled)
        self.content = QWidget()
        self.content.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toggle, 0, Qt.AlignLeft)
        lay.addWidget(self.content)

    def _toggled(self, on: bool):
        self.toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.content.setVisible(on)
