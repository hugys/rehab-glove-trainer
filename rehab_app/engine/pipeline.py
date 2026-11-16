"""Shared record/run logic (the team script's `record_reference` / `run_impaired`
loops, split into per-frame calls driven by the UI worker).

Subclasses only provide `_measure()`: grab a frame and (maybe) a new set of 21
landmarks. Everything else - normalisation, mirroring, progress re-indexing,
multi-take tolerance, error, stall assist - happens here, identically for the
real camera and the mock.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

import numpy as np

from .. import config
from ..hardware import servo_motion
from ..config import FINGERS, LandmarkSet
from . import tracking_core as tc
from .interface import EngineError, FrameResult, ReferenceTrajectory, RehabEngine, SessionParams

MIN_TAKE_FRAMES = 10
HAND_VOTES = 15                # handedness = majority of the last results...
HAND_MIN_VOTES = 5             # ...once there are at least this many
HAND_MIN_SCORE = 0.6           # results the tracker is unsure about don't vote


@dataclass
class Measurement:
    image: np.ndarray | None             # BGR frame
    detected: bool                       # a hand is currently tracked (for overlay)
    overlay_px: np.ndarray | None        # (21, 2) normalised image coords of latest result
    confidence: float = 0.0
    hand: str | None = None              # "left" / "right" (the patient's real hand)
    # Fresh landmark result (may lag the frame in LIVE_STREAM mode):
    new_pts21: np.ndarray | None = None  # (21, 3) pixel-scale, image y-down
    new_t: float | None = None           # capture time of that result, seconds since t0


class PipelineEngine(RehabEngine):
    def __init__(self, landmark_set: LandmarkSet):
        super().__init__(landmark_set)
        self.geom = tc.Geometry.from_landmark_set(landmark_set)
        self._rows = list(self.geom.key_landmarks)
        self._hand_mm = config.HAND_SIZE_DEFAULT_MM
        # reference state
        self._movement_id = ""
        self._duration = config.REF_DURATION_S
        self._source_hand = "right"
        self._takes: list[np.ndarray] = []
        self._take_times: list[float] = []
        self._take_poses: list[np.ndarray] = []
        # session state
        self._ref: ReferenceTrajectory | None = None
        self._params: SessionParams | None = None
        self._assist: tc.StallAssist | None = None
        self._target = None
        self._reach_start = self._reach_end = None
        self._last: dict = {}
        self._align = True                         # rotate hands upright (see normalize_pose)
        self._axis: tc.MovementAxis | None = None
        self._hand_votes: deque = deque(maxlen=HAND_VOTES)

    # ---- to implement ----------------------------------------------------------
    def _measure(self, elapsed_s: float | None) -> Measurement:
        """Grab one frame. `elapsed_s` is None during preview; otherwise the
        recording clock, so implementations can stamp capture times."""
        raise NotImplementedError

    def _on_phase_start(self) -> None:
        """Hook: a take or repetition is starting (reset capture clocks)."""

    # ---- helpers -----------------------------------------------------------------
    def _display(self, pose_hand_units: np.ndarray) -> np.ndarray:
        """Hand-size units, image y-down -> mm, y-up (display frame)."""
        out = pose_hand_units * self._hand_mm
        out[:, 1] *= -1
        return out

    def _hand(self, m: Measurement) -> str | None:
        """Smoothed handedness: one wrong label from the tracker must not flash a warning."""
        if not m.detected:
            self._hand_votes.clear()
            return None
        if m.new_pts21 is not None and m.hand and m.confidence >= HAND_MIN_SCORE:
            self._hand_votes.append(m.hand)
        if len(self._hand_votes) < HAND_MIN_VOTES:
            return None
        return Counter(self._hand_votes).most_common(1)[0][0]

    def _base_frame(self, m: Measurement, progress: float, new: bool) -> FrameResult:
        px = m.overlay_px[self._rows] if (m.overlay_px is not None and m.detected) else None
        return FrameResult(image=m.image, hand_detected=m.detected, confidence=m.confidence,
                           hand=self._hand(m), progress=progress, new_measurement=new,
                           landmarks_px=px)

    # ---- preview -------------------------------------------------------------
    def preview_frame(self) -> FrameResult:
        m = self._measure(None)
        fr = self._base_frame(m, 0.0, False)
        if m.new_pts21 is not None:
            fr.landmarks = self._display(tc.normalize_pose(m.new_pts21, self.geom, False, True,
                                                           align=True))
        return fr

    # ---- reference -----------------------------------------------------------
    def start_reference(self, movement_id, duration_s, source_hand, hand_size_mm):
        self._movement_id = movement_id
        self._duration = duration_s
        self._source_hand = source_hand
        self._hand_mm = hand_size_mm or config.HAND_SIZE_DEFAULT_MM   # drawing only
        self._takes = []

    def start_take(self, take_index: int):
        self._take_times, self._take_poses = [], []
        self._hand_votes.clear()
        self._on_phase_start()

    def reference_frame(self, elapsed_s: float) -> FrameResult:
        m = self._measure(elapsed_s)
        new = m.new_pts21 is not None and m.new_t is not None and m.new_t >= 0
        if new:
            # healthy hand -> upright, mirrored onto the affected side, hand-size normalised
            self._take_poses.append(tc.normalize_pose(m.new_pts21, self.geom, True, True,
                                                      align=True))
            self._take_times.append(m.new_t)
        fr = self._base_frame(m, min(100.0, 100.0 * elapsed_s / self._duration), new)
        if m.new_pts21 is not None:
            fr.landmarks = self._display(tc.normalize_pose(m.new_pts21, self.geom, False, True,
                                                           align=True))
        return fr

    def end_take(self) -> dict:
        n = len(self._take_poses)
        if n < MIN_TAKE_FRAMES:
            raise EngineError(f"Hand only detected in {n} frames - keep the whole hand in view "
                              "and try again.")
        times = self._take_times
        warning = None
        if times[0] > 0.1 * self._duration or times[-1] < 0.9 * self._duration:
            warning = (f"Hand only tracked from {times[0]:.1f}s to {times[-1]:.1f}s of "
                       f"{self._duration:.1f}s; gaps were filled with the nearest pose.")
        self._takes.append(tc.resample_to_progress(times, self._take_poses, self._duration))
        return {"frames": n, "fps": n / self._duration, "warning": warning}

    def finish_reference(self) -> ReferenceTrajectory:
        if not self._takes:
            raise EngineError("No takes recorded")
        mean, tol, rom = tc.build_reference(self._takes, self.geom)
        return ReferenceTrajectory(
            movement_id=self._movement_id, landmark_preset=self.landmark_set.preset,
            landmark_ids=list(self.geom.key_landmarks),
            progress=np.linspace(0, 100, len(mean)), data=mean, tolerance=tol, rom=rom,
            n_takes=len(self._takes), units="hand", source_hand=self._source_hand,
            meta={"engine": type(self).__name__, "mirrored": True, "scale_normalized": True,
                  "aligned": True, "duration_s": self._duration})

    # ---- session -------------------------------------------------------------
    def start_session(self, reference: ReferenceTrajectory, params: SessionParams):
        if list(reference.landmark_ids) != list(self.geom.key_landmarks):
            raise EngineError("This reference was recorded with a different landmark set - "
                              "re-record it.")
        if reference.units != "hand":
            raise EngineError("Reference units not supported - re-record it.")
        self._ref = reference
        self._params = params
        self._hand_mm = params.hand_size_mm or config.HAND_SIZE_DEFAULT_MM  # drawing only
        self._duration = config.session_duration_s(params.speed_pct)
        tips = list(self.geom.tip_rows)
        ref_reach = np.linalg.norm(reference.data[:, tips, :], axis=-1)   # (P, 5)
        self._assist = tc.StallAssist(self.geom, window_s=params.stall_window_s,
                                      move_eps=params.stall_move_eps_pct / 100.0,
                                      start_grace_s=params.start_grace_s,
                                      ref_reach=ref_reach, duration_s=self._duration,
                                      complete_tol_move=params.complete_tol_move_pct / 100.0)
        # References recorded before hands were rotated upright are compared unrotated
        self._align = bool(reference.meta.get("aligned", False))
        self._axis = tc.MovementAxis(reference.data, self.geom)
        self._best = np.zeros(len(FINGERS))        # furthest each finger got this repetition
        self._target = reference.data[-1]          # fully completed pose of the exercise
        self._reach_start, self._reach_end = ref_reach[0], ref_reach[-1]
        self._last = {}
        self._hand_votes.clear()
        self._on_phase_start()

    def session_frame(self, elapsed_s: float) -> FrameResult:
        assert self._ref is not None and self._assist is not None and self._params is not None
        m = self._measure(elapsed_s)
        progress_pct = min(100.0, 100.0 * elapsed_s / self._duration)
        new = False
        if m.new_pts21 is not None and m.new_t is not None:
            p = m.new_t / self._duration
            if 0.0 <= p <= 1.0:
                new = True
                # live hand: no mirror
                pose = tc.normalize_pose(m.new_pts21, self.geom, False, True, align=self._align)
                ref_pose = tc.interp_rows(self._ref.data, p)
                # error = the part of the movement not reached so far (timing doesn't count:
                # fully stretched when opening a fist = 0 %, halfway = 50 %)
                completion = self._axis.completion(pose)
                self._best = np.fmax(self._best, np.clip(completion, 0.0, 1.0))
                err = np.where(self._axis.moving, 1.0 - self._best, self._axis.drift(pose))
                completion = np.where(self._axis.moving, self._best, np.nan)
                cmds, states = self._assist.update(pose, self._target, m.new_t, p)
                err_pct = {f: float(e * 100.0) for f, e in zip(FINGERS, err)}
                reach = np.linalg.norm(pose[list(self.geom.tip_rows)], axis=1)
                moves = {f: servo_motion.plan_move(self._ref.movement_id, f, reach[i],
                                                   self._reach_start[i], self._reach_end[i])
                         for i, f in enumerate(FINGERS) if states[i] == "assisting"}
                self._last = dict(
                    landmarks=self._display(pose), reference_landmarks=self._display(ref_pose),
                    finger_error_pct=err_pct,
                    finger_completion_pct={f: (None if np.isnan(c) else float(c * 100.0))
                                           for f, c in zip(FINGERS, completion)},
                    overall_error_pct=float(np.mean(list(err_pct.values()))),
                    correction={f: e > self._params.threshold_pct for f, e in err_pct.items()},
                    assist_state=dict(zip(FINGERS, states)),
                    assist_cmd=dict(zip(FINGERS, cmds)),
                    servo_moves=moves,
                )
        fr = self._base_frame(m, progress_pct, new)
        for k, v in self._last.items():
            setattr(fr, k, v)
        if not m.detected:
            fr.landmarks = None
        return fr

    def finish_session(self) -> None:
        self._last = {}
