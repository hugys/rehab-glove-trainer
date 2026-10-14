"""Trajectory maths from the team's MediaPipe script (SD_Test.py), made reusable.

Pure numpy - no camera, no MediaPipe - so both the real engine and the mock
engine share exactly the same pipeline:

  REFERENCE : 4 s take(s) -> keep key landmarks -> wrist = (0,0,0) -> divide by
              hand size -> mirror -> re-index to progress 0-100 % -> mean of takes
              + tolerance band
  SESSION   : progress = elapsed / duration (duration from clinician speed)
              ERROR  (diagnostic): per-finger distance to the reference at the
                                   same progress
              ASSIST (drives motors): StallAssist - finger has not moved for
                                   `stall_window_s` and is not at the target yet

The landmark selection comes from a `config.LandmarkSet` instead of the
script's module-level KEY_LANDMARKS, so the 12- and 15-point sets both work.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import FINGERS, LandmarkSet

WRIST = 0
MIDDLE_FINGER_MCP = 9          # hand-size reference for scale normalisation

N_PROGRESS = 101               # samples on the 0-100 % progress axis (1 % steps)
USE_Z = True                   # MediaPipe z is the noisiest axis; set False to test without it
TOL_FLOOR = 0.15               # min per-finger tolerance (hand-size units)
TOL_SIGMAS = 2.0               # tolerance = max(floor, TOL_SIGMAS * spread across takes)

CW, CCW, HOLD = +1, -1, 0      # servo direction codes

# Stall-assist defaults (hand-size units / seconds) - the clinician can override
STALL_WINDOW_S = 1.0
STALL_MOVE_EPS = 0.04
COMPLETE_TOL = 0.08


@dataclass(frozen=True)
class Geometry:
    """Row bookkeeping derived from a LandmarkSet (replaces the script's globals)."""

    key_landmarks: tuple[int, ...]
    finger_rows: tuple[tuple[int, ...], ...]   # per finger, in FINGERS order
    tip_rows: tuple[int, ...]                  # most distal kept point per finger

    @classmethod
    def from_landmark_set(cls, ls: LandmarkSet) -> "Geometry":
        groups = ls.finger_groups()
        rows = tuple(tuple(groups[f]) for f in FINGERS)
        assert all(rows), "landmark set must keep at least one landmark per finger"
        assert ls.mediapipe_indices[0] == WRIST, "landmark set must start with the wrist"
        return cls(tuple(ls.mediapipe_indices), rows, tuple(r[-1] for r in rows))


# =============================================================================
# 1. Coordinate transforms
# =============================================================================

def normalize_pose(pts21: np.ndarray, geom: Geometry, mirror: bool, scale: bool) -> np.ndarray:
    """(21, 3) pixel-scaled landmarks -> (K, 3) wrist-relative, optionally hand-size
    normalised and x-mirrored, reduced to the key landmarks."""
    pts = np.array(pts21, dtype=float, copy=True)
    pts -= pts[WRIST]
    if scale:
        size = np.linalg.norm(pts[MIDDLE_FINGER_MCP])
        pts /= max(size, 1e-6)
    if mirror:
        pts[:, 0] *= -1
    return pts[list(geom.key_landmarks)]


def landmarks_to_pixels(hand_landmarks, width: int, height: int) -> np.ndarray:
    """MediaPipe NormalizedLandmark list -> (21, 3) isotropic pixel-scale array.
    x/y scaled by frame size separately; z uses the width (MediaPipe convention)."""
    return np.array([[lm.x * width, lm.y * height, lm.z * width] for lm in hand_landmarks],
                    dtype=float)


def resample_to_progress(times, poses, duration: float, n: int = N_PROGRESS) -> np.ndarray:
    """Re-index a take from wall-clock time to progress 0-100 % -> (n, K, 3)."""
    times, poses = np.asarray(times, dtype=float), np.asarray(poses, dtype=float)
    progress = np.clip(times / duration, 0.0, 1.0)
    grid = np.linspace(0.0, 1.0, n)
    flat = poses.reshape(len(times), -1)
    out = np.stack([np.interp(grid, progress, flat[:, j]) for j in range(flat.shape[1])], axis=1)
    return out.reshape(n, *poses.shape[1:])


def interp_rows(arr: np.ndarray, progress: float) -> np.ndarray:
    """Linear interpolation of arr[P, ...] at fractional progress in [0, 1]."""
    p = float(np.clip(progress, 0.0, 1.0)) * (len(arr) - 1)
    i = int(np.floor(p))
    j = min(i + 1, len(arr) - 1)
    f = p - i
    return (1 - f) * arr[i] + f * arr[j]


# =============================================================================
# 2. Reference from one or more takes
# =============================================================================

def build_reference(takes: list[np.ndarray], geom: Geometry):
    """takes: list of (P, K, 3) progress-indexed arrays.

    Returns (mean (P,K,3), tolerance (P,5), rom (5,)).
    tolerance[p, finger] = max(TOL_FLOOR, TOL_SIGMAS * spread of takes around the mean);
    with one take the spread is 0 so the floor rules - record 3+ takes for a real band.
    """
    stack = np.stack(takes)
    mean = stack.mean(axis=0)
    dev = np.linalg.norm(stack - mean, axis=-1)                 # (N, P, K)
    sigma = np.sqrt((dev ** 2).mean(axis=0))                    # (P, K)
    finger_sigma = np.stack([sigma[:, list(rows)].mean(axis=1) for rows in geom.finger_rows], axis=1)
    tolerance = np.maximum(TOL_FLOOR, TOL_SIGMAS * finger_sigma)
    reach = np.linalg.norm(mean[:, list(geom.tip_rows), :], axis=-1)
    rom = reach.max(axis=0) - reach.min(axis=0)
    return mean, tolerance, rom


# =============================================================================
# 3. Error metric (diagnostic only - never drives the servos)
# =============================================================================

def finger_errors(ref_pose: np.ndarray, cur_pose: np.ndarray, geom: Geometry):
    """Both (K, 3). Returns err (5,) mean per-finger 3-D distance and direction (5,):
    +1 fingertip closer to wrist than reference (over-curled), -1 further (over-extended)."""
    d = cur_pose - ref_pose
    if not USE_Z:
        d = d.copy()
        d[:, 2] = 0.0
    per_landmark = np.linalg.norm(d, axis=1)
    err = np.array([per_landmark[list(rows)].mean() for rows in geom.finger_rows])
    tips = list(geom.tip_rows)
    reach_ref = np.linalg.norm(ref_pose[tips], axis=1)
    reach_cur = np.linalg.norm(cur_pose[tips], axis=1)
    return err, np.sign(reach_ref - reach_cur)


# =============================================================================
# 4. Stall-triggered assist - THIS drives the servos
# =============================================================================

class StallAssist:
    """Per finger, independently: if the fingertip's reach hasn't changed by more than
    `move_eps` over the last `window_s` seconds AND it hasn't reached the rep's final
    target (within `complete_tol`), assist: drive it the rest of the way. Once assisting,
    keep going until done (motor-induced motion must not be read as the patient resuming).

    update(cur_pose, target_pose, now) -> (cmds, states)
      cmds   : 5 ints CW/CCW/HOLD (FINGERS order)
      states : 5 of "idle" (filling the window) / "moving" / "assisting" / "done"
    """

    def __init__(self, geom: Geometry, window_s: float = STALL_WINDOW_S,
                 move_eps: float = STALL_MOVE_EPS, complete_tol: float = COMPLETE_TOL,
                 start_grace_s: float = 0.0):
        self.geom = geom
        self.window_s = window_s
        # Added for the app: no stall is judged before `start_grace_s` into the rep, so a
        # slow start (movements ease in, especially at low speed) isn't mistaken for a stop.
        # 0 = original script behaviour.
        self.start_grace_s = start_grace_s
        self.move_eps = move_eps
        self.complete_tol = complete_tol
        self.reset()

    def reset(self):
        self._history = [[] for _ in FINGERS]
        self._assisting = [False] * len(FINGERS)
        self._done = [False] * len(FINGERS)
        self._t_start = None

    def update(self, cur_pose, target_pose, now: float):
        if self._t_start is None:
            self._t_start = now
        in_grace = (now - self._t_start) < self.start_grace_s
        tips = list(self.geom.tip_rows)
        reach_cur = np.linalg.norm(cur_pose[tips], axis=1)
        reach_tgt = np.linalg.norm(target_pose[tips], axis=1)
        cmds, states = [], []
        for i in range(len(FINGERS)):
            hist = self._history[i]
            hist.append((now, reach_cur[i]))
            while hist and now - hist[0][0] > self.window_s:
                hist.pop(0)

            remaining = reach_tgt[i] - reach_cur[i]
            if self._done[i] or abs(remaining) <= self.complete_tol:
                self._done[i] = True
                self._assisting[i] = False
                cmds.append(HOLD)
                states.append("done")
                continue

            if not self._assisting[i]:
                window_full = bool(hist) and (now - hist[0][0]) >= self.window_s * 0.95
                spread = (max(r for _, r in hist) - min(r for _, r in hist)) if hist else 0.0
                if window_full and spread < self.move_eps and not in_grace:
                    self._assisting[i] = True
                else:
                    cmds.append(HOLD)
                    # (the original script labelled this state "stalled", but it is the
                    # finger still moving on its own - renamed so the UI reads correctly)
                    states.append("moving" if window_full else "idle")
                    continue

            # remaining > 0: target is further from the wrist -> EXTEND -> CCW (-1)
            # remaining < 0: target is closer to the wrist   -> FLEX   -> CW  (+1)
            # Matches the serial protocol documented in the original script
            # ("-1 = CCW (extend), 1 = CW (flex)"); the script itself had the sign
            # flipped here. Confirm against the firmware before driving real motors.
            cmds.append(CCW if remaining > 0 else CW)
            states.append("assisting")
        return cmds, states
