"""Small looping, silent video (e.g. the patient's recorded reference with the tracked
skeleton drawn on it). Decodes only while visible."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

HOLD_FRAMES = 20          # pause on the last frame before the loop restarts


class VideoClip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: str | None = None
        self._cap = None
        self._image: QImage | None = None
        self._hold = 0
        self.caption = ""
        self.placeholder = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next)
        self.setMinimumSize(120, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_video(self, path: str | Path | None, caption: str = ""):
        path = str(path) if path else None
        self.caption = caption
        if path == self._path:
            self.update()
            return
        self._close()
        self._path, self._image = path, None
        if self.isVisible():
            self._open()
        self.update()

    def _open(self):
        if self._path and cv2 is not None and Path(self._path).exists():
            self._cap = cv2.VideoCapture(self._path)
            fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._hold = 0
            self._next()
            self._timer.start(int(1000 / fps))

    def _close(self):
        self._timer.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def showEvent(self, e):
        super().showEvent(e)
        if self._cap is None:
            self._open()

    def hideEvent(self, e):
        self._close()
        super().hideEvent(e)

    def _next(self):
        if self._cap is None:
            return
        if self._hold:
            self._hold -= 1
            if self._hold == 0:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            return
        ok, frame = self._cap.read()
        if not ok:
            self._hold = HOLD_FRAMES if self._image is not None else 0
            if self._image is None:           # unreadable file: stop trying
                self._close()
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        self._image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cap_h = 20 if self.caption else 0
        r = QRectF(self.rect()).adjusted(0, 0, 0, -cap_h)
        if self._image is not None:
            aspect = self._image.width() / self._image.height()
            w = min(r.width(), r.height() * aspect)
            h = w / aspect
            p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), self._image)
        else:
            p.fillRect(r, QColor("#eef1f6"))
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, self.placeholder)
        if self.caption:
            p.setPen(QColor(theme.MUTED))
            p.setFont(QFont(self.font().family(), 12))
            p.drawText(QRectF(0, self.height() - cap_h, self.width(), cap_h), Qt.AlignCenter,
                       self.caption)
        p.end()
