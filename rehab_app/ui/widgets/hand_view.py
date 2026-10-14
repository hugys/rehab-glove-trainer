"""2-D (front view) plot of the current hand vs the reference pose, in mm."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...config import LandmarkSet
from .. import theme


class HandComparisonView(QWidget):
    def __init__(self, landmark_set: LandmarkSet, parent=None):
        super().__init__(parent)
        self.landmark_set = landmark_set
        self._edges = landmark_set.skeleton_edges()
        self._cur: np.ndarray | None = None
        self._ref: np.ndarray | None = None
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def reset_scale(self):
        self._extent = 0.0

    def set_poses(self, current: np.ndarray | None, reference: np.ndarray | None):
        self._cur, self._ref = current, reference
        self.update()

    def _map(self, pose: np.ndarray, r: QRectF) -> list[QPointF]:
        # Wrist-centred, y up. The scale only ever grows during a run (no jumping),
        # and fits hands in any orientation.
        cx, cy = r.center().x(), r.center().y()
        mx, my = self._mid
        return [QPointF(cx + (x - mx) * self._scale, cy - (y - my) * self._scale)
                for x, y, _ in pose]

    def _update_scale(self, r: QRectF):
        poses = [p[:, :2] for p in (self._cur, self._ref) if p is not None]
        if poses:
            pts = np.concatenate(poses)
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            self._mid = (lo + hi) / 2
            ext = float(np.abs(pts - self._mid).max())
        else:
            self._mid, ext = np.zeros(2), 100.0
        self._extent = max(getattr(self, "_extent", 0.0), ext, 40.0)
        self._scale = min(r.width(), r.height()) / (2 * self._extent * 1.1)

    def _draw(self, p: QPainter, pose, r, color: QColor, width: float, dashed: bool):
        pts = self._map(pose, r)
        pen = QPen(color, width, Qt.DashLine if dashed else Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for a, b in self._edges:
            p.drawLine(pts[a], pts[b])
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for pt in pts:
            p.drawEllipse(pt, width + 1.5, width + 1.5)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE))
        r = QRectF(self.rect()).adjusted(10, 28, -10, -6)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(self.font().family(), 11))
        p.drawText(QRectF(10, 4, self.width() - 20, 20), Qt.AlignLeft,
                   "Front view (mm)    orange dashed = reference    blue = patient")
        self._update_scale(r)
        if self._ref is not None:
            self._draw(p, self._ref, r, theme.qcolor("#e8743b", 200), 2.5, True)
        if self._cur is not None:
            self._draw(p, self._cur, r, QColor(theme.ACCENT), 3, False)
        if self._cur is None and self._ref is None:
            p.drawText(r, Qt.AlignCenter, "No hand data")
        p.end()
