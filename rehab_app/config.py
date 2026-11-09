"""Central configuration: landmark presets, finger groups, movements, timing defaults."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Rehab Glove Trainer"

# --- Paths -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "rehab.db"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
VIDEO_DIR = DATA_DIR / "videos"
MODEL_PATH = ASSETS_DIR / "models" / "hand_landmarker.task"

# --- Fingers -----------------------------------------------------------------
FINGERS: list[str] = ["thumb", "index", "middle", "ring", "pinky"]

# --- Landmark presets --------------------------------------------------------
# Each entry is (MediaPipe index, name). Names must start with a finger name
# (or be "wrist") so finger groups can be derived automatically.
# Row order of every (N, 3) landmark array follows the list order.
LANDMARK_PRESETS: dict[str, list[tuple[int, str]]] = {
    # 12-point set used by the team's MediaPipe script (KEY_LANDMARKS):
    # wrist, thumb MCP/IP/TIP, and PIP + TIP of each other finger.
    "12": [
        (0, "wrist"),
        (2, "thumb_mcp"), (3, "thumb_ip"), (4, "thumb_tip"),
        (6, "index_pip"), (8, "index_tip"),
        (10, "middle_pip"), (12, "middle_tip"),
        (14, "ring_pip"), (16, "ring_tip"),
        (18, "pinky_pip"), (20, "pinky_tip"),
    ],
    "15": [
        (0, "wrist"),
        (2, "thumb_mcp"), (4, "thumb_tip"),
        (5, "index_mcp"), (6, "index_pip"), (8, "index_tip"),
        (9, "middle_mcp"), (10, "middle_pip"), (12, "middle_tip"),
        (13, "ring_mcp"), (14, "ring_pip"), (16, "ring_tip"),
        (17, "pinky_mcp"), (18, "pinky_pip"), (20, "pinky_tip"),
    ],
}
DEFAULT_LANDMARK_PRESET = "12"

# MediaPipe 21-point skeleton connections (used to draw whatever subset is active).
MEDIAPIPE_CHAIN = {
    "thumb": [0, 1, 2, 3, 4],
    "index": [0, 5, 6, 7, 8],
    "middle": [0, 9, 10, 11, 12],
    "ring": [0, 13, 14, 15, 16],
    "pinky": [0, 17, 18, 19, 20],
}


@dataclass(frozen=True)
class LandmarkSet:
    """A named selection of MediaPipe landmarks used for analysis."""

    preset: str
    points: tuple[tuple[int, str], ...]

    @classmethod
    def from_preset(cls, preset: str) -> "LandmarkSet":
        if preset not in LANDMARK_PRESETS:
            raise ValueError(f"Unknown landmark preset {preset!r}; options: {list(LANDMARK_PRESETS)}")
        return cls(preset, tuple(LANDMARK_PRESETS[preset]))

    @property
    def count(self) -> int:
        return len(self.points)

    @property
    def mediapipe_indices(self) -> list[int]:
        return [i for i, _ in self.points]

    @property
    def names(self) -> list[str]:
        return [n for _, n in self.points]

    def finger_rows(self, finger: str) -> list[int]:
        """Row indices (into an (N, 3) array) belonging to a finger."""
        return [r for r, (_, n) in enumerate(self.points) if n.startswith(finger + "_")]

    def finger_groups(self) -> dict[str, list[int]]:
        return {f: self.finger_rows(f) for f in FINGERS}

    def skeleton_edges(self) -> list[tuple[int, int]]:
        """Row-index pairs to draw, connecting consecutive active points along each finger."""
        row_of = {mp: r for r, (mp, _) in enumerate(self.points)}
        edges = []
        for chain in MEDIAPIPE_CHAIN.values():
            active = [row_of[i] for i in chain if i in row_of]
            edges += list(zip(active, active[1:]))
        # Palm outline across MCPs
        palm = [row_of[i] for i in (2, 5, 9, 13, 17) if i in row_of]
        edges += list(zip(palm, palm[1:]))
        return edges


# --- Movements ---------------------------------------------------------------
@dataclass(frozen=True)
class Movement:
    id: str
    name: str
    instruction: str
    image: str  # filename under assets/movements (optional; placeholder drawn if missing)


MOVEMENTS: list[Movement] = [
    Movement("open_fist", "Opening a Fist",
             "Start with a closed fist. Slowly open the hand until all fingers are fully extended.",
             "open_fist.png"),
    Movement("close_fist", "Closing a Fist",
             "Start with the hand open and fingers extended. Slowly curl all fingers into a fist.",
             "close_fist.png"),
    Movement("point_index", "Pointing Index Finger",
             "From a relaxed fist, extend only the index finger to point forward. Keep the others curled.",
             "point_index.png"),
    Movement("lateral_pinch", "Lateral Pinch",
             "Press the pad of the thumb against the side of the index finger, like holding a key.",
             "lateral_pinch.png"),
    Movement("precision_grip", "Precision Grip",
             "Bring the tips of the thumb and index finger together, like picking up a small bead.",
             "precision_grip.png"),
]
MOVEMENTS_BY_ID = {m.id: m for m in MOVEMENTS}

# --- Timing & thresholds -----------------------------------------------------
REF_DURATION_S = 4.0          # reference recordings are hard-coded to 4 s
CAMERA_FPS = 30
COUNTDOWN_S = 3
SPEED_MIN_PCT = 25
SPEED_MAX_PCT = 200
SPEED_DEFAULT_PCT = 100
THRESHOLD_DEFAULT_PCT = 11.0  # error threshold, % of hand size (~10 mm on a 90 mm hand)
REPS_DEFAULT = 5
TAKES_DEFAULT = 3             # reference takes averaged into one reference
VIDEO_FPS = 30

# Patient hand size (wrist -> middle-finger MCP, mm). Optional: the engine works in
# hand-size units measured from the camera (as in the team's script), so every result is
# a % of hand size. A measured hand size only adds mm next to those numbers. The default
# is used for drawing hands to scale when no size was entered.
HAND_SIZE_DEFAULT_MM = 90.0

# Stall-triggered assist (drives the glove motors). The clinician sets the stall
# time t per session; the other two are set in Settings > Glove assist tuning.
STALL_TIME_DEFAULT_S = 1.0
STALL_TIME_MIN_S = 0.2
STALL_TIME_MAX_S = 10.0
START_GRACE_DEFAULT_S = 2.0   # no stall is judged in the first seconds of a rep
STALL_MOVE_EPS_PCT = 4.0      # % of hand size: less fingertip movement within t = "stopped"
                              # (upper limit - lowered automatically at slow speeds)
COMPLETE_TOL_MOVE_PCT = 10.0  # % of EACH finger's own movement in the reference: this close
                              # to the end position (or past it) = "movement complete"


def session_duration_s(speed_pct: float) -> float:
    """Intended movement duration at a given speed (% of the 4 s reference).

    50 % speed -> 8 s, 200 % speed -> 2 s.
    """
    if speed_pct <= 0:
        raise ValueError("speed_pct must be > 0")
    return REF_DURATION_S * 100.0 / speed_pct
