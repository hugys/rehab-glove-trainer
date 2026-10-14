"""The contract between the UI and the processing engine.

The UI only ever talks to a `RehabEngine`. Two implementations exist:
  * MediaPipeEngine - real camera + the team's MediaPipe pipeline
  * MockEngine      - synthetic hand, same pipeline maths (demo / testing)

Conventions
-----------
* Landmark arrays are (N, 3) with N = landmark_set.count (12 or 15), rows in
  `config.LANDMARK_PRESETS[preset]` order.
* Everything returned in a FrameResult is in **millimetres in a y-up display
  frame** (wrist at origin, fingers point +y). The engine converts from its
  internal hand-size units using the patient's hand size.
* ReferenceTrajectory.data is stored in the engine's native units
  (`units`: "hand" = multiples of wrist->middle-MCP distance).
* Progress is 0-100 %. The UI worker owns the clock: it passes `elapsed_s`
  (seconds since the take / repetition started) into every frame call and stops
  once the intended duration is reached.
* ERROR vs ASSIST: error (distance to the reference at equal progress) is
  diagnostic. What drives the glove motors is the stall assist: a finger that
  has not moved for `stall_window_s` and has not reached the target is driven
  toward it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from ..config import LandmarkSet


@dataclass
class FrameResult:
    """Everything the UI needs to render one camera frame."""

    image: np.ndarray | None                 # BGR uint8 (H, W, 3) raw camera frame
    hand_detected: bool
    confidence: float = 0.0                  # 0-1 handedness score of the tracked hand
    progress: float = 0.0                    # 0-100 %
    new_measurement: bool = False            # a fresh landmark result arrived with this frame
    landmarks: np.ndarray | None = None      # (N, 3) mm, display frame (current hand)
    landmarks_px: np.ndarray | None = None   # (N, 2) normalised 0-1 image coords (overlay)
    reference_landmarks: np.ndarray | None = None  # (N, 3) expected pose at this progress
    finger_error_mm: dict[str, float] | None = None
    overall_error_mm: float | None = None
    correction: dict[str, bool] | None = None      # error above the clinician threshold
    assist_state: dict[str, str] | None = None     # idle / moving / assisting / done
    assist_cmd: dict[str, int] | None = None       # +1 CW (flex) / -1 CCW (extend) / 0 hold


@dataclass
class ReferenceTrajectory:
    """Processed, progress-indexed reference movement from the unaffected hand."""

    movement_id: str
    landmark_preset: str
    landmark_ids: list[int]                  # MediaPipe indices, row order
    progress: np.ndarray                     # (P,) 0-100
    data: np.ndarray                         # (P, N, 3) mean trajectory, mirrored, `units`
    tolerance: np.ndarray | None = None      # (P, 5) per-finger tolerance band, `units`
    rom: np.ndarray | None = None            # (5,) fingertip range of motion, `units`
    n_takes: int = 1
    units: str = "hand"
    source_hand: str = "right"
    meta: dict = field(default_factory=dict)


@dataclass
class SessionParams:
    speed_pct: float
    threshold_mm: float
    hand_size_mm: float
    stall_window_s: float                    # clinician-set time t before assist engages
    stall_move_eps_pct: float                # % of hand size
    complete_tol_pct: float                  # % of hand size
    start_grace_s: float = 0.0               # no stall judged in the first seconds of a rep


class RehabEngine(ABC):
    """Abstract engine. One instance lives for the whole app run; all calls come
    from the worker thread."""

    def __init__(self, landmark_set: LandmarkSet):
        self.landmark_set = landmark_set

    # ---- camera ------------------------------------------------------------
    @abstractmethod
    def open_camera(self, source: int | str | None = None) -> None:
        """Open a camera index, a video file path, or None = auto-detect."""

    @abstractmethod
    def close_camera(self) -> None: ...

    @abstractmethod
    def preview_frame(self) -> FrameResult:
        """Live frame (with tracking overlay) - no recording, no comparison."""

    # ---- reference recording (unaffected hand) -------------------------------
    @abstractmethod
    def start_reference(self, movement_id: str, duration_s: float, source_hand: str,
                        hand_size_mm: float) -> None: ...

    @abstractmethod
    def start_take(self, take_index: int) -> None: ...

    @abstractmethod
    def reference_frame(self, elapsed_s: float) -> FrameResult: ...

    @abstractmethod
    def end_take(self) -> dict:
        """Close the take; returns info such as {"frames": n, "warning": str|None}.
        Raise EngineError if the hand was not tracked well enough."""

    @abstractmethod
    def finish_reference(self) -> ReferenceTrajectory:
        """Combine takes into the mean trajectory + tolerance band."""

    # ---- rehabilitation session (affected hand) ------------------------------
    @abstractmethod
    def start_session(self, reference: ReferenceTrajectory, params: SessionParams) -> None: ...

    @abstractmethod
    def session_frame(self, elapsed_s: float) -> FrameResult: ...

    @abstractmethod
    def finish_session(self) -> None: ...

    def close(self) -> None:
        """Release models etc. at app exit."""


class EngineError(RuntimeError):
    """User-facing engine problems (camera missing, model not found, hand lost, ...)."""
