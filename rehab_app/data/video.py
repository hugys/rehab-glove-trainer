"""MP4 recording of reference takes and session repetitions.

Frames are written at a fixed VIDEO_FPS; if the camera delivers fewer frames,
the last frame is repeated (and extra frames are dropped) so that the video's
duration always matches wall-clock time. That keeps video time == recording
time, which matters when lining a video up with the logged data.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from .. import config

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_CODECS = ("avc1", "mp4v")      # H.264 when available (plays in QuickTime), else MPEG-4


def video_path(patient_id: int, stem: str) -> Path:
    folder = config.VIDEO_DIR / f"patient_{patient_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stem}_{datetime.now():%Y%m%d-%H%M%S}.mp4"


class VideoRecorder:
    def __init__(self, path: Path | str, fps: float = config.VIDEO_FPS):
        self.path = Path(path)
        self.fps = fps
        self._writer = None
        self._written = 0
        self._last = None

    def write(self, frame: np.ndarray | None, elapsed_s: float) -> None:
        if cv2 is None or frame is None:
            return
        if self._writer is None:
            h, w = frame.shape[:2]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            for codec in _CODECS:
                wr = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*codec), self.fps, (w, h))
                if wr.isOpened():
                    self._writer = wr
                    break
                wr.release()
            if self._writer is None:
                raise RuntimeError(f"Could not open a video writer for {self.path}")
        target = int(round(elapsed_s * self.fps)) + 1      # frames that should exist by now
        while self._written < target:
            self._writer.write(frame)
            self._written += 1
        self._last = frame

    def close(self, end_elapsed_s: float | None = None) -> Path | None:
        if self._writer is None:
            return None
        if end_elapsed_s is not None and self._last is not None:
            self.write(self._last, end_elapsed_s)
        self._writer.release()
        self._writer = None
        return self.path

    @property
    def frames(self) -> int:
        return self._written
