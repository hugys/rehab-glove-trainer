"""Looping demonstration of the intended movement.

If assets/movements/<movement.image> exists (PNG/JPG/GIF) it is shown instead;
otherwise an animated stick hand generated from the mock kinematics is drawn.
"""
from __future__ import annotations

import time

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QMovie, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...config import ASSETS_DIR, FINGERS, MOVEMENTS_BY_ID, REF_DURATION_S, MEDIAPIPE_CHAIN
from ...engine.mock_engine import _PROFILES, _smooth, hand_pose21
from .. import theme


class MovementDemo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._movement_id: str | None = None
        self._pixmap: QPixmap | None = None
        self._movie: QMovie | None = None
        self._t0 = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_movement(self, movement_id: str | None):
        self._movement_id = movement_id
        self._t0 = time.perf_counter()
        self._pixmap, self._movie = None, None
        if movement_id:
            path = ASSETS_DIR / "movements" / MOVEMENTS_BY_ID[movement_id].image
            gif = path.with_suffix(".gif")
            if gif.exists():
                self._movie = QMovie(str(gif))
                self._movie.frameChanged.connect(self.update)
                self._movie.start()
            elif path.exists():
                self._pixmap = QPixmap(str(path))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE))
        r = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        img = self._movie.currentPixmap() if self._movie else self._pixmap
        if img is not None and not img.isNull():
            scaled = img.scaled(r.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int(r.center().x() - scaled.width() / 2),
                         int(r.center().y() - scaled.height() / 2), scaled)
        elif self._movement_id:
            self._draw_animated(p, r)
        p.end()

    def _draw_animated(self, p: QPainter, r: QRectF):
        # 4 s movement, 1 s hold, repeat
        cycle = REF_DURATION_S + 1.0
        t = (time.perf_counter() - self._t0) % cycle
        prog = min(1.0, t / REF_DURATION_S)
        prof = _PROFILES[self._movement_id]
        flex = {f: prof[f][0] + (prof[f][1] - prof[f][0]) * _smooth(prog) for f in FINGERS}
        pose = hand_pose21(flex)

        # Oblique view so flexion is visible; fit the whole movement's extent into the panel
        def proj(pz):
            return np.stack([pz[:, 0] * 0.6 - pz[:, 2] * 0.8, pz[:, 1] + 0.35 * pz[:, 2]], axis=1)

        ends = np.concatenate([proj(hand_pose21({f: prof[f][k] for f in FINGERS})) for k in (0, 1)])
        lo, hi = ends.min(axis=0), ends.max(axis=0)
        box = r.adjusted(10, 10, -10, -26)
        s = min(box.width() / max(hi[0] - lo[0], 1), box.height() / max(hi[1] - lo[1], 1))
        ox = box.center().x() - (lo[0] + hi[0]) / 2 * s
        oy = box.center().y() + (lo[1] + hi[1]) / 2 * s
        pts = [QPointF(ox + x * s, oy - y * s) for x, y in proj(pose)]
        for finger, chain in MEDIAPIPE_CHAIN.items():
            pen = QPen(QColor(theme.FINGER_COLORS[finger]), 6)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for a, b in zip(chain, chain[1:]):
                p.drawLine(pts[a], pts[b])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(pts[0], 7, 7)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(self.font().family(), 11))
        p.drawText(QRectF(r.left(), r.bottom() - 16, r.width(), 16), Qt.AlignCenter,
                   f"Demo · {prog * 100:.0f}%")
