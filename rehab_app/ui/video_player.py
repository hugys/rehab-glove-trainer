"""Simple in-app MP4 player (OpenCV decode -> QLabel), with play/pause, seek, speed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSlider, QVBoxLayout)

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def reveal_in_finder(path: str | Path) -> None:
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", path])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


class VideoPlayer(QDialog):
    def __init__(self, path: str | Path, title: str = "", parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(title or self.path.name)
        self.resize(900, 640)
        self._cap = cv2.VideoCapture(str(self.path)) if cv2 else None
        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video {self.path}")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._n = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self._pos = 0

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background: #11151c;")
        self.view.setMinimumSize(480, 360)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, self._n - 1))
        self.slider.sliderMoved.connect(self._seek)
        self.time = QLabel()
        self.btn = QPushButton("Pause")
        self.btn.clicked.connect(self._toggle)
        self.rate = QComboBox()
        for r in ("0.25×", "0.5×", "1×", "2×"):
            self.rate.addItem(r, float(r[:-1]))
        self.rate.setCurrentIndex(2)
        self.rate.currentIndexChanged.connect(self._apply_rate)
        reveal = QPushButton("Show file")
        reveal.clicked.connect(lambda: reveal_in_finder(self.path))

        row = QHBoxLayout()
        row.addWidget(self.btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        row.addWidget(self.rate)
        row.addWidget(reveal)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next)
        self._apply_rate()
        self._show_frame()

    def _apply_rate(self):
        self._timer.start(int(1000 / (self._fps * self.rate.currentData())))

    def _toggle(self):
        if self._timer.isActive():
            self._timer.stop()
            self.btn.setText("Play")
        else:
            if self._pos >= self._n - 1:
                self._seek(0)
            self._apply_rate()
            self.btn.setText("Pause")

    def _seek(self, frame: int):
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        self._pos = frame
        self._show_frame()

    def _next(self):
        if not self._show_frame():
            self._timer.stop()
            self.btn.setText("Play")

    def _show_frame(self) -> bool:
        ok, frame = self._cap.read()
        if not ok:
            return False
        self._pos = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.view.setPixmap(QPixmap.fromImage(img).scaled(
            self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.slider.blockSignals(True)
        self.slider.setValue(self._pos)
        self.slider.blockSignals(False)
        self.time.setText(f"{self._pos / self._fps:5.1f} / {self._n / self._fps:.1f} s")
        return True

    def closeEvent(self, e):
        self._timer.stop()
        self._cap.release()
        super().closeEvent(e)


def play_video(path: str | Path, title: str = "", parent=None) -> None:
    if not Path(path).exists():
        QMessageBox.warning(parent, "Video missing", f"The file no longer exists:\n{path}")
        return
    try:
        VideoPlayer(path, title, parent).exec()
    except RuntimeError as e:
        QMessageBox.warning(parent, "Cannot play video", str(e))
