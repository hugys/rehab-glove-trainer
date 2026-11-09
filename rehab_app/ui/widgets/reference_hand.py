"""The patient's own recorded reference (healthy hand, mirrored onto the affected side),
drawn as a hand that performs the movement - the visual the patient follows.

During a repetition it is driven by the repetition's progress, so it moves at the
clinician-chosen speed in step with the session. Between repetitions it loops on its
own at the same speed (movement, 1 s hold) as a preview.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...config import FINGERS, LandmarkSet
from ...engine import tracking_core as tc
from .. import theme


class ReferenceHandView(QWidget):
    def __init__(self, landmark_set: LandmarkSet, parent=None):
        super().__init__(parent)
        self.landmark_set = landmark_set
        self._edges = landmark_set.skeleton_edges()
        groups = landmark_set.finger_groups()
        self._finger_of_row = {r: f for f in FINGERS for r in groups[f]}
        self._data: np.ndarray | None = None     # (P, N, 3) hand units, image y-down, mirrored
        self._duration = 4.0
        self._progress: float | None = None      # 0-100 driven externally; None = loop
        self._assisting: set[str] = set()
        self.mirror = False                      # match a mirrored camera preview
        self.caption = "Your movement to follow"
        self._t0 = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_reference(self, reference, duration_s: float):
        self._data = None if reference is None else np.asarray(reference.data, dtype=float)
        self._duration = duration_s
        self._progress = None
        self._assisting = set()
        self._t0 = time.perf_counter()
        self.update()

    def set_duration(self, duration_s: float):
        """Change the looping speed (e.g. the clinician moved the speed slider)."""
        self._duration = duration_s
        self._t0 = time.perf_counter()

    def set_progress(self, progress_pct: float | None, assisting: set[str] | None = None):
        """Follow a running repetition (None = go back to looping)."""
        self._progress = progress_pct
        self._assisting = assisting or set()
        if progress_pct is None:
            self._t0 = time.perf_counter()

    def _current_progress(self) -> float:
        if self._progress is not None:
            return self._progress / 100.0
        t = (time.perf_counter() - self._t0) % (self._duration + 1.0)
        return min(1.0, t / self._duration)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE))
        r = QRectF(self.rect()).adjusted(12, 12, -12, -28)
        if self._data is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "No reference recorded")
            p.end()
            return
        prog = self._current_progress()
        pose = tc.interp_rows(self._data, prog)[:, :2].copy()
        if self.mirror:
            pose[:, 0] *= -1

        # Fixed scale over the whole movement so the hand doesn't jump while moving
        allpts = self._data[:, :, :2].reshape(-1, 2) * ([-1, 1] if self.mirror else [1, 1])
        lo, hi = allpts.min(axis=0), allpts.max(axis=0)
        s = min(r.width() / max(hi[0] - lo[0], 1e-6), r.height() / max(hi[1] - lo[1], 1e-6))
        ox = r.center().x() - (lo[0] + hi[0]) / 2 * s
        oy = r.center().y() - (lo[1] + hi[1]) / 2 * s
        pts = [QPointF(ox + x * s, oy + y * s) for x, y in pose]   # image y-down = widget y-down

        edges = [(a, b, self._finger_of_row.get(b) or self._finger_of_row.get(a))
                 for a, b in self._edges]
        # fingers the glove is driving get a glow underneath (same cue as the purple ring)
        glow = QPen(theme.qcolor(theme.ASSIST, 90), 22)
        glow.setCapStyle(Qt.RoundCap)
        p.setPen(glow)
        for a, b, finger in edges:
            if finger in self._assisting:
                p.drawLine(pts[a], pts[b])
        for a, b, finger in edges:
            pen = QPen(QColor(theme.FINGER_COLORS.get(finger, theme.TEXT)), 7)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(pts[a], pts[b])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        for pt in pts:
            p.drawEllipse(pt, 5, 5)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(self.font().family(), 12))
        p.drawText(QRectF(r.left(), r.bottom() + 6, r.width(), 18), Qt.AlignCenter,
                   f"{self.caption} · {prog * 100:.0f}%")
        p.end()
