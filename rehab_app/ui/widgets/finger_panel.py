"""Per-finger error bars with threshold marker and stall-assist state."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...config import FINGERS
from .. import theme

# assist state -> (label, colour)
ASSIST_STYLE = {
    "idle": ("–", "#a0a8b5"),
    "moving": ("moving", theme.OK),
    "assisting": ("ASSIST", theme.ASSIST),
    "done": ("✓ done", theme.ACCENT),
}


class FingerErrorPanel(QWidget):
    """Horizontal bars + assist chip (clinician) or large traffic-light dots (patient,
    compact=True). Error is diagnostic; the chip shows what the glove is doing."""

    def __init__(self, threshold_mm: float = 10.0, compact: bool = False, parent=None):
        super().__init__(parent)
        self.threshold = threshold_mm
        self.compact = compact
        self._err: dict[str, float] | None = None
        self._state: dict[str, str] | None = None
        self.setMinimumHeight(140 if compact else 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_threshold(self, mm: float):
        self.threshold = mm
        self.update()

    def set_values(self, err: dict[str, float] | None, assist_state: dict[str, str] | None = None):
        self._err, self._state = err, assist_state
        self.update()

    def clear(self):
        self.set_values(None, None)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.compact:
            self._paint_dots(p)
        else:
            self._paint_bars(p)
        p.end()

    def _paint_dots(self, p: QPainter):
        n = len(FINGERS)
        w, h = self.width(), self.height()
        cell = w / n
        rad = min(cell * 0.3, (h - 52) * 0.45)
        for i, f in enumerate(FINGERS):
            cx, cy = cell * (i + 0.5), (h - 50) / 2 + 2
            e = self._err.get(f) if self._err else None
            st = (self._state or {}).get(f)
            if st == "assisting":
                p.setPen(QPen(QColor(theme.ASSIST), 6))
            else:
                p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.error_color(e, self.threshold)))
            p.drawEllipse(QPointF(cx, cy), rad, rad)
            p.setPen(QColor(theme.TEXT))
            p.setFont(QFont(self.font().family(), 18, QFont.Bold))
            p.drawText(QRectF(cx - cell / 2, h - 50, cell, 26), Qt.AlignCenter, f.capitalize())
            if st == "assisting":
                p.setPen(QColor(theme.ASSIST))
                p.setFont(QFont(self.font().family(), 14, QFont.Bold))
                p.drawText(QRectF(cx - cell / 2, h - 24, cell, 22), Qt.AlignCenter, "glove helping")

    def _paint_bars(self, p: QPainter):
        n = len(FINGERS)
        label_w, value_w, chip_w = 70, 86, 74
        w, h = self.width(), self.height()
        row_h = h / n
        bar_left = label_w
        bar_w = max(10, w - label_w - value_w - chip_w - 8)
        scale_max = max(self.threshold * 2.5, 1.0)
        thr_x = bar_left + bar_w * self.threshold / scale_max
        font = QFont(self.font().family(), 12)
        for i, f in enumerate(FINGERS):
            y = i * row_h
            e = self._err.get(f) if self._err else None
            p.setFont(font)
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(0, y, label_w - 6, row_h), Qt.AlignVCenter | Qt.AlignLeft, f.capitalize())
            track = QRectF(bar_left, y + row_h * 0.25, bar_w, row_h * 0.5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e3e8f0"))
            p.drawRoundedRect(track, 4, 4)
            if e is not None:
                fill = QRectF(track.left(), track.top(), bar_w * min(e, scale_max) / scale_max,
                              track.height())
                p.setBrush(QColor(theme.error_color(e, self.threshold)))
                p.drawRoundedRect(fill, 4, 4)
            p.setPen(QColor(theme.TEXT))
            txt = f"{e:.1f} mm" if e is not None else "--"
            p.drawText(QRectF(bar_left + bar_w + 4, y, value_w - 4, row_h),
                       Qt.AlignVCenter | Qt.AlignRight, txt)
            # assist chip
            label, color = ASSIST_STYLE.get((self._state or {}).get(f), ASSIST_STYLE["idle"])
            chip = QRectF(w - chip_w, y + row_h * 0.22, chip_w - 2, row_h * 0.56)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(color, 40 if label != "ASSIST" else 255))
            p.drawRoundedRect(chip, 5, 5)
            p.setPen(QColor("white") if label == "ASSIST" else QColor(color))
            p.setFont(QFont(self.font().family(), 10, QFont.Bold))
            p.drawText(chip, Qt.AlignCenter, label)
        p.setPen(QPen(QColor(theme.TEXT), 1.5, Qt.DashLine))
        p.drawLine(QPointF(thr_x, 2), QPointF(thr_x, h - 2))
