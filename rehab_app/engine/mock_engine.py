"""Synthetic engine for demos and development without a camera.

Generates a kinematic 21-point hand, renders it into a fake camera image, and
feeds it through exactly the same pipeline as the real MediaPipe engine
(`PipelineEngine`). The simulated affected hand moves with per-finger lag and
then STOPS short of the target for some fingers, so the stall-triggered assist
can be demonstrated; while a finger is being assisted the "glove" pushes it on.
"""
from __future__ import annotations

import math
import time

import numpy as np

from ..config import FINGERS, MEDIAPIPE_CHAIN, LandmarkSet
from .pipeline import Measurement, PipelineEngine

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

# Bone lengths (mm) per finger: MCP->PIP, PIP->DIP, DIP->TIP (thumb: CMC->MCP, MCP->IP, IP->TIP)
_BONES = {
    "thumb": (38, 32, 28),
    "index": (42, 25, 20),
    "middle": (46, 28, 21),
    "ring": (43, 27, 20),
    "pinky": (34, 20, 18),
}
_BASE = {
    "thumb": np.array([25.0, 25.0, 0.0]),
    "index": np.array([22.0, 88.0, 0.0]),
    "middle": np.array([2.0, 92.0, 0.0]),
    "ring": np.array([-17.0, 86.0, 0.0]),
    "pinky": np.array([-34.0, 75.0, 0.0]),
}
_THUMB_MCP_OFFSET = np.array([18.0, 22.0, 0.0])
_MAX_FLEX_DEG = (80.0, 100.0, 70.0)

# Flexion (0 = straight, 1 = full curl) per finger, (start, end) across the movement.
_PROFILES: dict[str, dict[str, tuple[float, float]]] = {
    "open_fist": {f: (1.0, 0.0) for f in FINGERS},
    "close_fist": {f: (0.0, 1.0) for f in FINGERS},
    "point_index": {"thumb": (0.8, 0.8), "index": (1.0, 0.0),
                    "middle": (1.0, 1.0), "ring": (1.0, 1.0), "pinky": (1.0, 1.0)},
    "lateral_pinch": {"thumb": (0.0, 0.7), "index": (0.2, 0.75),
                      "middle": (0.2, 0.8), "ring": (0.2, 0.85), "pinky": (0.2, 0.9)},
    "precision_grip": {"thumb": (0.0, 0.65), "index": (0.0, 0.55),
                       "middle": (0.0, 0.3), "ring": (0.0, 0.3), "pinky": (0.0, 0.3)},
}

# Simulated impairment: (fraction of the movement the finger manages before it
# stops, lag in % progress).
_IMPAIRMENT = {"thumb": (0.92, 4.0), "index": (1.0, 2.0), "middle": (0.85, 6.0),
               "ring": (0.5, 10.0), "pinky": (0.65, 8.0)}
_ASSIST_RATE = 0.6        # fraction of the movement per second the "glove" adds when assisting

_IMG_W, _IMG_H, _PX_PER_MM = 640, 480, 1.7


def _smooth(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def _finger_chain(finger: str, flex: float) -> list[np.ndarray]:
    b1, b2, b3 = _BONES[finger]
    angles = [math.radians(a * flex) for a in _MAX_FLEX_DEG]
    if finger == "thumb":
        cmc = _BASE["thumb"]
        mcp = cmc + _THUMB_MCP_OFFSET * (1 - 0.3 * flex) + np.array([0, 0, -8 * flex])
        direction = np.array([0.55, 0.83, 0.0])
        pts, pos, cum = [cmc, mcp], mcp, 0.0
        for length, a in zip((b2, b3), angles[:2]):
            cum += a
            d = np.array([direction[0] * math.cos(cum) - 1.1 * math.sin(cum),
                          direction[1] * math.cos(cum), -0.6 * math.sin(cum)])
            d /= np.linalg.norm(d)
            pos = pos + d * length
            pts.append(pos)
        return pts
    pts, pos, cum = [_BASE[finger]], _BASE[finger], 0.0
    for length, a in zip((b1, b2, b3), angles):
        cum += a
        pos = pos + np.array([0.0, math.cos(cum), -math.sin(cum)]) * length
        pts.append(pos)
    return pts


def hand_pose21(flex: dict[str, float]) -> np.ndarray:
    """Full 21-point MediaPipe-ordered pose (mm, wrist at origin, y-up)."""
    out = np.zeros((21, 3))
    for i, finger in enumerate(FINGERS):
        out[1 + 4 * i: 5 + 4 * i] = _finger_chain(finger, flex[finger])
    return out


class MockEngine(PipelineEngine):
    def __init__(self, landmark_set: LandmarkSet):
        super().__init__(landmark_set)
        self._rng = np.random.default_rng()
        self._mode = "preview"
        self._boost = {f: 0.0 for f in FINGERS}
        self._last_t: float | None = None
        self._bg = self._make_background()

    # ---- camera ------------------------------------------------------------
    def open_camera(self, source=None) -> None:
        self._mode = "preview"

    def close_camera(self) -> None:
        pass

    # ---- phase tracking ----------------------------------------------------
    def start_take(self, take_index: int):
        super().start_take(take_index)
        self._mode = "reference"

    def start_session(self, reference, params):
        super().start_session(reference, params)
        self._mode = "session"
        self._boost = {f: 0.0 for f in FINGERS}
        self._last_t = None

    def end_take(self):
        self._mode = "preview"
        return super().end_take()

    def finish_session(self):
        super().finish_session()
        self._mode = "preview"

    # ---- synthetic hand ----------------------------------------------------
    def _flex(self, elapsed_s: float | None) -> dict[str, float]:
        mid = self._movement_id or "close_fist"
        if self._mode == "session" and self._ref is not None:
            mid = self._ref.movement_id
        prof = _PROFILES[mid]
        if elapsed_s is None or self._mode == "preview":
            p = 0.15 + 0.1 * math.sin(time.perf_counter() * 1.5)
            return {f: prof[f][0] + (prof[f][1] - prof[f][0]) * p for f in FINGERS}
        progress = 100.0 * elapsed_s / self._duration
        if self._mode == "reference":
            return {f: prof[f][0] + (prof[f][1] - prof[f][0]) * _smooth(progress / 100) for f in FINGERS}
        # session: impaired hand that stops early; the glove adds to it while assisting
        dt = 0.0 if self._last_t is None else max(0.0, elapsed_s - self._last_t)
        self._last_t = elapsed_s
        states = (self._last or {}).get("assist_state") or {}
        flex = {}
        for f in FINGERS:
            cap, lag = _IMPAIRMENT[f]
            if states.get(f) == "assisting":
                self._boost[f] += _ASSIST_RATE * dt
            achieved = min(1.0, min(_smooth((progress - lag) / 100), cap) + self._boost[f])
            flex[f] = prof[f][0] + (prof[f][1] - prof[f][0]) * achieved
        return flex

    def _to_pixels(self, pose_mm: np.ndarray) -> np.ndarray:
        """mm y-up -> pixel-scale y-down, as MediaPipe landmarks * frame size."""
        out = np.empty_like(pose_mm)
        out[:, 0] = _IMG_W / 2 + pose_mm[:, 0] * _PX_PER_MM
        out[:, 1] = _IMG_H * 0.88 - pose_mm[:, 1] * _PX_PER_MM
        out[:, 2] = pose_mm[:, 2] * _PX_PER_MM
        return out

    def _make_background(self) -> np.ndarray:
        y = np.linspace(0, 1, _IMG_H)[:, None, None]
        top, bottom = np.array([70, 62, 58]), np.array([40, 36, 34])
        return np.broadcast_to(top * (1 - y) + bottom * y, (_IMG_H, _IMG_W, 3)).astype(np.uint8).copy()

    def _render(self, px: np.ndarray) -> np.ndarray:
        img = self._bg.copy()
        if cv2 is None:
            return img
        skin = (150, 180, 225)
        pts = px[:, :2].astype(int)
        palm = pts[[0, 1, 5, 9, 13, 17]]
        cv2.fillConvexPoly(img, cv2.convexHull(palm), skin, lineType=cv2.LINE_AA)
        for chain in MEDIAPIPE_CHAIN.values():
            for a, b in zip(chain[1:], chain[2:]):
                cv2.line(img, tuple(pts[a]), tuple(pts[b]), skin, 22, cv2.LINE_AA)
        return img

    def _measure(self, elapsed_s):
        pose = hand_pose21(self._flex(elapsed_s))
        if self._mode == "reference":
            pose[:, 0] *= -1  # the unaffected hand is the opposite hand; mirroring maps it back
        px = self._to_pixels(pose)
        px[1:] += self._rng.normal(0, 0.5, (20, 3))
        img = self._render(px)
        if self._rng.random() < 0.01:   # occasional lost detection
            return Measurement(image=img, detected=False, overlay_px=None, confidence=0.0)
        overlay = np.stack([px[:, 0] / _IMG_W, px[:, 1] / _IMG_H], axis=1)
        conf = float(np.clip(self._rng.normal(0.95, 0.02), 0, 1))
        return Measurement(image=img, detected=True, overlay_px=overlay, confidence=conf,
                           new_pts21=px, new_t=elapsed_s)
