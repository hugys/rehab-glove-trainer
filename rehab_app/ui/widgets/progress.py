"""Large movement progress bar with elapsed/target time."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme


class MovementProgressBar(QWidget):
    def __init__(self, height: int = 44, parent=None):
        super().__init__(parent)
        self._progress = 0.0
        self._elapsed = 0.0
        self._duration = 0.0
        self._color = theme.ACCENT
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_progress(self, progress: float, elapsed: float = 0.0, duration: float = 0.0):
        self._progress = max(0.0, min(100.0, progress))
        self._elapsed = elapsed
        self._duration = duration
        self.update()

    def set_color(self, color: str):
        self._color = color
        self.update()

    def reset(self):
        self.set_progress(0, 0, 0)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e3e8f0"))
        p.drawRoundedRect(r, radius, radius)
        fill = QRectF(r.left(), r.top(), 0, r.height())
        if self._progress > 0:
            fill.setWidth(max(r.height(), r.width() * self._progress / 100))
            p.setBrush(QColor(self._color))
            p.drawRoundedRect(fill, radius, radius)
        text = f"{self._progress:.0f}%"
        if self._duration:
            text += f"    {self._elapsed:.1f} / {self._duration:.1f} s"
        p.setFont(QFont(self.font().family(), max(11, int(r.height() * 0.38)), QFont.Bold))
        p.setPen(QColor(theme.TEXT))
        p.drawText(r, Qt.AlignCenter, text)
        if fill.width() > 0:  # white text where it overlaps the filled part
            p.setClipRect(fill)
            p.setPen(QColor("white"))
            p.drawText(r, Qt.AlignCenter, text)
        p.end()
