"""Live camera feed with landmark skeleton overlay, countdown and status banners."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...config import LandmarkSet
from .. import theme


class CameraView(QWidget):
    def __init__(self, landmark_set: LandmarkSet, parent=None):
        super().__init__(parent)
        self.landmark_set = landmark_set
        self._edges = landmark_set.skeleton_edges()
        self._image: QImage | None = None
        self._pts: np.ndarray | None = None
        self._detected = True
        self._countdown: int | None = None
        self._banner: str | None = None
        self._recording = False
        self._active = False
        self.mirror = True          # selfie view: what patients expect to see
        self.placeholder = "Camera off"
        self.setMinimumSize(360, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ---- API -----------------------------------------------------------------
    def set_frame(self, frame, recording: bool = False):
        self._active = True
        self._recording = recording
        self._detected = frame.hand_detected
        self._pts = frame.landmarks_px if frame.hand_detected else None
        if self._pts is not None and self.mirror:
            self._pts = np.column_stack([1.0 - self._pts[:, 0], self._pts[:, 1]])
        img = frame.image
        if img is not None:
            h, w = img.shape[:2]
            rgb = img[:, ::-1, ::-1] if self.mirror else img[:, :, ::-1]
            rgb = np.ascontiguousarray(rgb)
            self._image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        else:
            self._image = None
        self.update()

    def set_countdown(self, n: int | None):
        self._countdown = n
        self.update()

    def set_banner(self, text: str | None):
        self._banner = text
        self.update()

    def clear(self):
        self._active = False
        self._image = None
        self._pts = None
        self._countdown = None
        self.update()

    # ---- painting ------------------------------------------------------------
    def _target_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        aspect = (self._image.width() / self._image.height()) if self._image else 4 / 3
        if w / h > aspect:
            tw, th = h * aspect, h
        else:
            tw, th = w, w / aspect
        return QRectF((w - tw) / 2, (h - th) / 2, tw, th)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#11151c"))
        r = self._target_rect()
        if self._image is not None:
            p.drawImage(r, self._image)
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#232a36"))
            g.setColorAt(1, QColor("#161b23"))
            p.fillRect(r, g)
            p.setPen(QColor("#5c6678"))
            p.setFont(QFont(self.font().family(), 11))
            label = "No image" if self._active else self.placeholder
            p.drawText(r.adjusted(10, 8, -10, -8), Qt.AlignTop | Qt.AlignLeft, label)

        # skeleton
        if self._pts is not None:
            pts = [QPointF(r.left() + x * r.width(), r.top() + y * r.height()) for x, y in self._pts]
            p.setPen(QPen(QColor(255, 255, 255, 200), 3))
            for a, b in self._edges:
                p.drawLine(pts[a], pts[b])
            p.setPen(Qt.NoPen)
            for i, pt in enumerate(pts):
                name = self.landmark_set.names[i]
                color = theme.FINGER_COLORS.get(name.split("_")[0], "#ffffff")
                p.setBrush(QColor(color))
                p.drawEllipse(pt, 6, 6)

        # recording indicator
        if self._recording:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BAD))
            p.drawEllipse(QPointF(r.right() - 22, r.top() + 22), 8, 8)
            p.setPen(QColor("white"))
            p.setFont(QFont(self.font().family(), 11, QFont.Bold))
            p.drawText(QRectF(r.right() - 90, r.top() + 12, 60, 20), Qt.AlignRight, "REC")

        if self._active and not self._detected:
            self._draw_banner(p, r, "Hand not detected - keep the hand in view", theme.WARN)
        elif self._banner:
            self._draw_banner(p, r, self._banner, theme.ACCENT)

        if self._countdown is not None:
            p.fillRect(r, QColor(0, 0, 0, 110))
            p.setPen(QColor("white"))
            size = max(24, int(r.height() * 0.35))
            p.setFont(QFont(self.font().family(), size, QFont.Bold))
            p.drawText(r, Qt.AlignCenter, str(self._countdown) if self._countdown > 0 else "GO")
        p.end()

    def _draw_banner(self, p: QPainter, r: QRectF, text: str, color: str):
        band = QRectF(r.left(), r.bottom() - 40, r.width(), 40)
        p.fillRect(band, theme.qcolor(color, 220))
        p.setPen(QColor("white"))
        p.setFont(QFont(self.font().family(), 12, QFont.Bold))
        p.drawText(band, Qt.AlignCenter, text)
