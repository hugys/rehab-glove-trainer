"""Trajectory maths from the team's MediaPipe script (SD_Test.py), made reusable.

Pure numpy - no camera, no MediaPipe - so both the real engine and the mock
engine share exactly the same pipeline:

  REFERENCE : 4 s take(s) -> keep key landmarks -> wrist = (0,0,0) -> divide by
              hand size -> rotate hand upright -> mirror -> re-index to progress
              0-100 % -> mean of takes + tolerance band
  SESSION   : progress = elapsed / duration (duration from clinician speed)
              ERROR  (diagnostic): per finger, along the movement's own axis
                                   (0 % = start pose, 100 % = end pose of the
                                   reference): the part of the movement the
                                   patient has not reached, whatever the timing
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
# Speed-aware stall detection (used when the reference reach curve is given):
STALL_EPS_FLOOR = 0.01         # never call less movement than tracking jitter "moving"
STALL_EPS_FRACTION = 0.5       # "stopped" = under half of what the reference moves in t
STALL_LAG = 0.05               # ...and at least 5 % of the movement behind the reference
MIN_REACH_SPAN = 0.02          # fingers the reference barely moves: no lag check
# Error along the movement axis:
MIN_AXIS_SPAN = 0.15           # fingers the reference moves less than this (hand units) have no
                               # usable axis: they should stay put, and their error is the
                               # distance from the reference relative to STILL_ERR_SCALE
STILL_ERR_SCALE = 0.25         # a still finger 1/4 hand length away from the reference = 100 %
COMPLETION_MEDIAN = 5          # completion = median of the last results, so one jittery frame
                               # cannot count as "the furthest the finger got"
# Per-finger completion (used with complete_tol_move):
COMPLETE_TOL_FLOOR = 0.02      # never demand more precision than tracking can deliver
STILL_FINGER_SPAN = 0.05       # reference moves the finger less than this = finger should
                               # stay put; it keeps the absolute `complete_tol` instead


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

def palm_length(pts21: np.ndarray, use_z: bool = False) -> float:
    """Hand size as the camera sees it: wrist -> middle-finger knuckle, in pixels. In the
    image plane by default; use_z=True is the original script's 3-D length (noisier, kept
    for references that were recorded with it)."""
    d = np.asarray(pts21[MIDDLE_FINGER_MCP], dtype=float) - np.asarray(pts21[WRIST], dtype=float)
    return float(np.linalg.norm(d if use_z else d[:2]))


def normalize_pose(pts21: np.ndarray, geom: Geometry, mirror: bool, scale: bool,
                   align: bool = False, size: float | None = None) -> np.ndarray:
    """(21, 3) pixel-scaled landmarks -> (K, 3) wrist-relative, optionally hand-size
    normalised, rotated upright and x-mirrored, reduced to the key landmarks.

    align (added for the app): rotate in the image plane so wrist -> middle-finger knuckle
    points straight up (-y). A hand held at a slight angle then still lines up with the
    reference, so overlay and error compare finger movement, not how the hand was tilted."""
    pts = np.array(pts21, dtype=float, copy=True)
    pts -= pts[WRIST]
    if scale:
        # size: hand size to divide by (pixels); None = this frame's own 3-D palm length
        pts /= max(np.linalg.norm(pts[MIDDLE_FINGER_MCP]) if size is None else size, 1e-6)
    if align:
        x, y = pts[MIDDLE_FINGER_MCP, :2]
        a = np.arctan2(x, -y)                    # angle of the knuckle away from straight up
        c, s = np.cos(a), np.sin(a)
        pts[:, :2] = pts[:, :2] @ np.array([[c, -s], [s, c]])
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

class MovementAxis:
    """Per finger, the straight line from the reference's start pose to its end pose
    (all of the finger's tracked points together). Positions along it are the finger's
    completion: 0 = start, 1 = end (e.g. fully stretched when opening a fist). Only this
    one axis counts; sideways deviation is ignored.

    Image plane only (x, y): MediaPipe's depth is far too noisy for this. Replaying real
    recordings, a closed fist read as ~50 % open with depth included and ~0-20 % without.

    completion(pose) -> (5,) 0-1 along the movement, NaN for still fingers
    drift(pose)      -> (5,) for fingers the reference barely moves (< MIN_AXIS_SPAN): how far
                        the finger is from the little path the reference takes (anywhere
                        between its start and end pose, so timing doesn't count), relative
                        to STILL_ERR_SCALE; NaN for moving fingers
    """

    def __init__(self, ref_data: np.ndarray, geom: Geometry):
        self.geom = geom
        self._rows = [list(r) for r in geom.finger_rows]
        self._start = [ref_data[0, r, :2].ravel() for r in self._rows]
        self._vec = [ref_data[-1, r, :2].ravel() - st for r, st in zip(self._rows, self._start)]
        self.span = np.array([np.linalg.norm(v) for v in self._vec])
        self.moving = self.span >= MIN_AXIS_SPAN

    def completion(self, pose: np.ndarray) -> np.ndarray:
        out = np.full(len(self._rows), np.nan)
        for i, (r, st, v) in enumerate(zip(self._rows, self._start, self._vec)):
            if self.moving[i]:
                out[i] = float(np.dot(pose[r, :2].ravel() - st, v) / self.span[i] ** 2)
        return out

    def drift(self, pose: np.ndarray) -> np.ndarray:
        out = np.full(len(self._rows), np.nan)
        for i, (r, st, v) in enumerate(zip(self._rows, self._start, self._vec)):
            if not self.moving[i]:
                d = pose[r, :2].ravel() - st
                t = np.clip(np.dot(d, v) / max(self.span[i] ** 2, 1e-12), 0.0, 1.0)
                off = (d - t * v).reshape(-1, 2)            # per tracked point of the finger
                out[i] = float(np.linalg.norm(off, axis=1).mean()) / STILL_ERR_SCALE
        return out


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

    update(cur_pose, target_pose, now, progress=None, force=False) -> (cmds, states)
      force  : the movement time is over (added for the app) - every finger that has not
               reached the end position is assisted now, stalled or not
      cmds   : 5 ints CW/CCW/HOLD (FINGERS order)
      states : 5 of "idle" (filling the window) / "moving" / "assisting" / "done"

    Speed awareness (added for the app): with `ref_reach` (P, 5 fingertip reach along the
    reference) and `duration_s` (repetition length at the clinician's speed), a finger
    only counts as stalled if
      1. it moved less than min(move_eps, STALL_EPS_FRACTION x what the reference moves
         in `window_s` at this speed) - so at 25 % speed the bar drops with the reference,
         never below STALL_EPS_FLOOR (tracking jitter), and
      2. it is more than STALL_LAG of the movement behind where the reference is now
         (`progress` 0-1) - a patient keeping pace with a slow reference is never "stalled".
    Without `ref_reach` the original script's rule applies (fixed move_eps, no lag check).

    Per-finger completion (added for the app): with `ref_reach` and `complete_tol_move`
    (fraction), a finger the reference moves is "done" once it is within
    complete_tol_move x its own movement of the end position - or past it in the
    movement's direction (closing a fist tighter than the reference counts as done; it is
    never driven back). Floor COMPLETE_TOL_FLOOR. A flat tolerance was half of a small
    movement (precision-grip pinky) but under 7 % of a full fist. Fingers the reference
    barely moves (< STILL_FINGER_SPAN) keep the absolute `complete_tol` both ways.
    """

    def __init__(self, geom: Geometry, window_s: float = STALL_WINDOW_S,
                 move_eps: float = STALL_MOVE_EPS, complete_tol: float = COMPLETE_TOL,
                 start_grace_s: float = 0.0, ref_reach: np.ndarray | None = None,
                 duration_s: float | None = None, complete_tol_move: float | None = None):
        self.geom = geom
        self.window_s = window_s
        # Added for the app: no stall is judged before `start_grace_s` into the rep, so a
        # slow start (movements ease in, especially at low speed) isn't mistaken for a stop.
        # 0 = original script behaviour.
        self.start_grace_s = start_grace_s
        self.move_eps = move_eps
        self.complete_tol = complete_tol
        n = len(FINGERS)
        self._ref_reach = None if ref_reach is None else np.asarray(ref_reach, dtype=float)
        if self._ref_reach is not None and duration_s:
            self._start, span = self._ref_reach[0], self._ref_reach[-1] - self._ref_reach[0]
            self._span = span
            expected = np.abs(span) * window_s / duration_s     # reference motion within t
            self.eps = np.minimum(move_eps, np.maximum(STALL_EPS_FLOOR,
                                                       STALL_EPS_FRACTION * expected))
        else:
            self._ref_reach = None
            self.eps = np.full(n, move_eps)
        # per finger: completion tolerance and the direction that counts as "past the end"
        self.tol = np.full(n, complete_tol)
        self._dir = np.zeros(n)                  # 0 = symmetric (original rule)
        if self._ref_reach is not None and complete_tol_move is not None:
            moving = np.abs(self._span) >= STILL_FINGER_SPAN
            self.tol = np.where(moving, np.maximum(COMPLETE_TOL_FLOOR,
                                                   complete_tol_move * np.abs(self._span)),
                                complete_tol)
            self._dir = np.where(moving, np.sign(self._span), 0.0)
        self.reset()

    def _complete(self, i: int, remaining: float) -> bool:
        if self._dir[i] == 0:
            return abs(remaining) <= self.tol[i]
        return remaining * self._dir[i] <= self.tol[i]      # within tol, or overshot

    def _behind(self, i: int, reach: float, progress: float | None) -> bool:
        """Finger i is clearly behind the reference at this progress (always True
        without a reference curve, i.e. the original rule)."""
        if self._ref_reach is None or progress is None or abs(self._span[i]) < MIN_REACH_SPAN:
            return True
        ref_now = interp_rows(self._ref_reach, progress)[i]
        pos_cur = (reach - self._start[i]) / self._span[i]
        pos_ref = (ref_now - self._start[i]) / self._span[i]
        return pos_cur < pos_ref - STALL_LAG

    def reset(self):
        self._history = [[] for _ in FINGERS]
        self._assisting = [False] * len(FINGERS)
        self._done = [False] * len(FINGERS)
        self._t_start = None

    def update(self, cur_pose, target_pose, now: float, progress: float | None = None,
               force: bool = False):
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
            if self._done[i] or self._complete(i, remaining):
                self._done[i] = True
                self._assisting[i] = False
                cmds.append(HOLD)
                states.append("done")
                continue

            if not self._assisting[i]:
                window_full = bool(hist) and (now - hist[0][0]) >= self.window_s * 0.95
                spread = (max(r for _, r in hist) - min(r for _, r in hist)) if hist else 0.0
                stopped = window_full and spread < self.eps[i]
                if force or (stopped and not in_grace
                             and self._behind(i, reach_cur[i], progress)):
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
