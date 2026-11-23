"""Copies of recorded videos with the tracked hand skeleton drawn on top.

Recordings are saved clean (no overlay). For showing a reference in the interface, a
smaller copy "<name>_overlay.mp4" is made by running the hand tracker over the saved
video once, in a background thread.
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from .. import config
from ..config import FINGERS, LandmarkSet

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

OVERLAY_HEIGHT = 480           # px; the clips are shown small
FINGER_BGR = {"thumb": (59, 116, 232), "index": (235, 111, 31), "middle": (121, 169, 25),
              "ring": (207, 94, 148), "pinky": (109, 69, 214)}      # ui.theme.FINGER_COLORS


def overlay_path(video: str | Path) -> Path:
    video = Path(video)
    return video.with_name(f"{video.stem}_overlay.mp4")


def draw_skeleton(frame: np.ndarray, pts_px: np.ndarray, landmark_set: LandmarkSet) -> None:
    """Draw the app's skeleton (same points and colours as the live camera view).
    pts_px: (N, 2) pixel coordinates in landmark_set row order."""
    scale = frame.shape[0] / 480
    pts = [tuple(int(round(v)) for v in p) for p in pts_px]
    for a, b in landmark_set.skeleton_edges():
        cv2.line(frame, pts[a], pts[b], (255, 255, 255), max(2, round(3 * scale)), cv2.LINE_AA)
    for pt, name in zip(pts, landmark_set.names):
        color = FINGER_BGR.get(name.split("_")[0], (255, 255, 255))
        cv2.circle(frame, pt, max(4, round(6 * scale)), color, -1, cv2.LINE_AA)


def make_overlay(src: str | Path, landmark_set: LandmarkSet, model_path: str) -> Path:
    """Write the overlay copy of `src` and return its path. Frames in which no hand is
    found are copied without a skeleton."""
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    src, dst = Path(src), overlay_path(src)
    tmp = dst.with_suffix(".part.mp4")
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or config.VIDEO_FPS
    rows = landmark_set.mediapipe_indices
    landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO, num_hands=1))
    writer = None
    try:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            rgb = np.ascontiguousarray(frame[:, :, ::-1])
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int(i * 1000 / fps))
            i += 1
            out_w = max(2, int(round(w * OVERLAY_HEIGHT / h / 2)) * 2)
            small = cv2.resize(frame, (out_w, OVERLAY_HEIGHT), interpolation=cv2.INTER_AREA)
            if result.hand_landmarks:
                lm = result.hand_landmarks[0]
                pts = np.array([[lm[r].x * out_w, lm[r].y * OVERLAY_HEIGHT] for r in rows])
                draw_skeleton(small, pts, landmark_set)
            if writer is None:
                for codec in ("avc1", "mp4v"):
                    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*codec), fps,
                                             (out_w, OVERLAY_HEIGHT))
                    if writer.isOpened():
                        break
                    writer.release()
                    writer = None
                if writer is None:
                    raise RuntimeError(f"Could not open a video writer for {tmp}")
            writer.write(small)
    finally:
        cap.release()
        landmarker.close()
        if writer is not None:
            writer.release()
    if writer is None:
        raise RuntimeError(f"No frames in {src}")
    os.replace(tmp, dst)             # only complete files ever carry the final name
    return dst


class OverlayBuilder(QObject):
    """Makes overlay copies one at a time in a background thread.
    request(video) -> `ready(video, overlay)` once the copy exists."""

    ready = Signal(str, str)

    def __init__(self, landmark_set: LandmarkSet, model_path: str | None, parent=None):
        super().__init__(parent)
        self.landmark_set = landmark_set
        self.model_path = model_path           # None = tracker unavailable: nothing is built
        self._jobs: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._thread: threading.Thread | None = None

    def request(self, video: str | Path) -> None:
        video = str(video)
        if self.model_path is None or cv2 is None or video in self._pending:
            return
        self._pending.add(video)
        self._jobs.put(video)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            try:
                video = self._jobs.get(timeout=1.0)
            except queue.Empty:
                return
            try:
                out = make_overlay(video, self.landmark_set, self.model_path)
                self.ready.emit(video, str(out))
            except Exception as e:      # a missing clip must never take the app down
                print(f"[overlay] {video}: {e}", flush=True)
            finally:
                self._pending.discard(video)
