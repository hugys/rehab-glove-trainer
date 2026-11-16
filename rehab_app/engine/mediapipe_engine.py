"""Real engine: camera + MediaPipe Hand Landmarker (LIVE_STREAM), from the team's script.

`HandStream`, the camera-opening helpers and the model lookup are carried over
from SD_Test.py with only cosmetic changes (no cv2 windows - the app renders the
video itself). The trajectory maths lives in `tracking_core.py` / `pipeline.py`.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .. import config
from ..config import LandmarkSet
from . import tracking_core as tc
from .interface import EngineError
from .pipeline import Measurement, PipelineEngine

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


# =============================================================================
# Model lookup
# =============================================================================

def find_model(path: str | os.PathLike | None = None) -> str:
    """Use `path` if it exists, else any hand_landmarker*.task in the usual places."""
    candidates = [Path(path)] if path else []
    candidates.append(config.MODEL_PATH)
    for c in candidates:
        if c.exists():
            return str(c)
    for folder in (config.MODEL_PATH.parent, config.PROJECT_ROOT, Path.cwd()):
        if folder.is_dir():
            for name in sorted(os.listdir(folder)):
                if name.lower().startswith("hand_landmarker") and name.lower().endswith(".task"):
                    return str(folder / name)
    raise EngineError(
        "No hand_landmarker .task model found. Download it to "
        f"{config.MODEL_PATH} from https://storage.googleapis.com/mediapipe-models/"
        "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task")


# =============================================================================
# MediaPipe LIVE_STREAM wrapper (from the team script)
# =============================================================================

class HandStream:
    """push(frame) sends a frame and remembers its capture time; poll_new() hands back
    each finished result exactly once, with the capture time of the frame it belongs to
    (the callback fires later on another thread, so arrival time would be biased)."""

    def __init__(self, model_path: str, num_hands: int = 1):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        self._mp = mp
        self._lock = threading.Lock()
        self._capture_times: dict[int, float] = {}
        self._latest = None
        self._last_returned = -1
        self._idx = 0
        self.last_result = None
        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.LIVE_STREAM,
            num_hands=num_hands,
            result_callback=self._on_result,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)

    def _on_result(self, result, output_image, timestamp_ms):
        with self._lock:
            t = self._capture_times.pop(timestamp_ms, time.monotonic())
            for k in [k for k in self._capture_times if k < timestamp_ms]:
                del self._capture_times[k]                   # dropped frames never call back
            self._latest = (timestamp_ms, t, result)
            self.last_result = result

    def push(self, frame_bgr):
        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        self._idx += 1                                       # strictly increasing timestamps
        with self._lock:
            self._capture_times[self._idx] = time.monotonic()
        self._landmarker.detect_async(mp_image, self._idx)

    def poll_new(self):
        with self._lock:
            if self._latest and self._latest[0] != self._last_returned:
                self._last_returned = self._latest[0]
                return self._latest
        return None

    def close(self):
        self._landmarker.close()


# =============================================================================
# Camera (from the team script; cv2.waitKey -> time.sleep since there is no cv2 window)
# =============================================================================

def _backends():
    return {"any": cv2.CAP_ANY, "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
            "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY)}


def _open_capture(index: int, backend: str = "auto"):
    """On Windows try DirectShow first (MSMF can take 30-60 s to enumerate formats)."""
    if backend == "auto":
        if os.name == "nt":
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if cap.isOpened():
                return cap
            cap.release()
        return cv2.VideoCapture(index)
    return cv2.VideoCapture(index, _backends()[backend])


CAMERA_PERMISSION_HINT = (
    "On macOS, allow camera access in System Settings > Privacy & Security > Camera for "
    "\"Rehab Glove Trainer\" (desktop app) or for Terminal (if started from a terminal), "
    "then restart the app.")
CAPTURE_SIZE = (1280, 720)   # full-HD frames halve the frame rate; 720p is plenty for tracking


def _configure(cap) -> None:
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_SIZE[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_SIZE[1])
    cap.set(cv2.CAP_PROP_FPS, 30)


def _usable(cap) -> bool:
    for _ in range(15):
        ok, frame = cap.read()
        if ok and frame is not None and frame.mean() > 1.0:
            return True
        time.sleep(0.03)
    return False


_ROTATE = {90: "ROTATE_90_CLOCKWISE", 180: "ROTATE_180", 270: "ROTATE_90_COUNTERCLOCKWISE"}


def rotate_frame(frame, degrees: int):
    """Rotate a camera frame clockwise by 0/90/180/270 degrees (e.g. a phone held upright)."""
    code = _ROTATE.get(int(degrees) % 360)
    return frame if code is None else cv2.rotate(frame, getattr(cv2, code))


def real_hand(label: str | None) -> str | None:
    """MediaPipe labels handedness as if the image were a mirrored selfie. Camera frames
    reach it unmirrored, so its "Left" is the patient's right hand and vice versa."""
    return {"left": "right", "right": "left"}.get((label or "").lower())


def request_camera_access() -> None:
    """macOS: make sure the camera-permission prompt can appear.

    The worker thread opens the camera, but OpenCV can only show macOS's permission prompt
    from the main thread ("can not spin main run loop from other thread"). From a
    Terminal the Terminal's permission applies, but the desktop app is its own app and
    starts unauthorised, so every camera then fails. Opening the camera once here, on the
    main thread at startup, shows the prompt the first time (and is a quick no-op after).
    """
    if sys.platform != "darwin" or cv2 is None:
        return
    cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
    cap.release()


def open_working_camera(index: int | None = None, max_index: int = 4, backend: str = "auto"):
    """index=None: scan 0..max_index-1 and use the first camera giving a non-black frame
    (skips Continuity Camera / virtual cams that open but deliver nothing)."""
    if index is not None:
        cap = _open_capture(index, backend)
        if not cap.isOpened():
            cap.release()
            raise EngineError(f"Camera {index} did not open. {CAMERA_PERMISSION_HINT}")
        _configure(cap)
        if _usable(cap):
            return cap
        cap.release()
        raise EngineError(f"Camera {index} opened but returned only black/empty frames.")
    for i in range(max_index):
        cap = _open_capture(i, backend)
        if not cap.isOpened():
            cap.release()
            continue
        _configure(cap)
        if _usable(cap):
            return cap
        cap.release()
    raise EngineError(f"No camera returned a usable image. Check the connection. "
                      f"{CAMERA_PERMISSION_HINT}")


def list_cameras(max_index: int = 6) -> list[int]:
    found = []
    for i in range(max_index):
        cap = _open_capture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append(i)
        cap.release()
    return found


# =============================================================================
# Engine
# =============================================================================

class MediaPipeEngine(PipelineEngine):
    def __init__(self, landmark_set: LandmarkSet, model_path: str | None = None):
        super().__init__(landmark_set)
        if cv2 is None:
            raise EngineError("opencv-python is not installed")
        if importlib.util.find_spec("mediapipe") is None:
            raise EngineError("mediapipe is not installed (pip install mediapipe)")
        self._model_path = find_model(model_path)
        self._stream: HandStream | None = None
        self._cap = None
        self._is_file = False
        self._file_fps = 30.0
        self._t0_mono: float | None = None
        self._last_frame = None

    # ---- camera ------------------------------------------------------------
    def open_camera(self, source=None) -> None:
        self.close_camera()
        if isinstance(source, str) and source and not source.isdigit():
            self._cap = cv2.VideoCapture(source)
            if not self._cap.isOpened():
                raise EngineError(f"Could not open video file {source}")
            self._is_file = True
            self._file_fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        else:
            idx = int(source) if source not in (None, "", "auto") else None
            self._cap = open_working_camera(index=idx)
            self._is_file = False
        if self._stream is None:
            self._stream = HandStream(self._model_path)

    def close_camera(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def close(self) -> None:
        self.close_camera()
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    # ---- measurement -------------------------------------------------------
    def _on_phase_start(self) -> None:
        self._t0_mono = None

    def _read(self):
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if (not ok or frame is None) and self._is_file:     # loop video files
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        # rotated before tracking, so landmarks, preview and recorded video all agree
        return rotate_frame(frame, self.rotation)

    def _measure(self, elapsed_s):
        frame = self._read()
        if frame is None:
            # transient camera hiccup: keep showing the last frame
            return Measurement(image=self._last_frame, detected=False, overlay_px=None)
        self._last_frame = frame
        h, w = frame.shape[:2]
        if elapsed_s is not None and self._t0_mono is None:
            self._t0_mono = time.monotonic() - elapsed_s
        self._stream.push(frame)

        m = Measurement(image=frame, detected=False, overlay_px=None)
        new = self._stream.poll_new()
        if new:
            _, t_capture, result = new
            if result.hand_landmarks:
                m.new_pts21 = tc.landmarks_to_pixels(result.hand_landmarks[0], w, h)
                m.new_t = (t_capture - self._t0_mono) if self._t0_mono is not None else None
        last = self._stream.last_result
        if last is not None and last.hand_landmarks:
            m.detected = True
            m.overlay_px = np.array([[lm.x, lm.y] for lm in last.hand_landmarks[0]])
            if last.handedness:
                m.confidence = float(last.handedness[0][0].score)
                m.hand = real_hand(last.handedness[0][0].category_name)
        return m
