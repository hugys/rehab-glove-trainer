"""How far and which way each finger's servo moves once a stall triggers assist.

PLACEHOLDER - the glove does not exist yet. The structure is final; the numbers in
SERVO_TRAVEL_DEG / MOVEMENT_PROFILES / SERVO_SPEED_DEG_S must be calibrated on the
real glove (tendon routing, spool radius, servo horn) before driving motors.

Model
-----
Finger position is expressed as progress along the *patient's own reference*:
    position = 0.0 -> finger is where the reference movement starts
    position = 1.0 -> finger is where the reference movement ends (target)
measured from fingertip reach (fingertip-to-wrist distance, hand-size units), the
same quantity StallAssist uses. The servo has to cover the remaining part:
    degrees = (1 - position) * travel of that finger for that movement
Direction comes from whether the target is closer to the wrist (flex, CW +1) or
further away (extend, CCW -1), matching servo_link's serial protocol.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import FINGERS

CW, CCW, HOLD = +1, -1, 0

# Full servo travel (degrees) that moves each finger through its whole range
# (straight <-> full fist). TODO: calibrate per glove.
SERVO_TRAVEL_DEG: dict[str, float] = {
    "thumb": 120.0, "index": 160.0, "middle": 170.0, "ring": 160.0, "pinky": 140.0,
}

# Per movement, the share of each finger's full travel that movement uses
# (e.g. a precision grip only half-flexes the index). 1.0 = full range.
# TODO: confirm with the physician / calibrate.
MOVEMENT_PROFILES: dict[str, dict[str, float]] = {
    "open_fist": {f: 1.0 for f in FINGERS},
    "close_fist": {f: 1.0 for f in FINGERS},
    "point_index": {"thumb": 0.0, "index": 1.0, "middle": 0.0, "ring": 0.0, "pinky": 0.0},
    "lateral_pinch": {"thumb": 0.7, "index": 0.55, "middle": 0.6, "ring": 0.65, "pinky": 0.7},
    "precision_grip": {"thumb": 0.65, "index": 0.55, "middle": 0.3, "ring": 0.3, "pinky": 0.3},
}

SERVO_SPEED_DEG_S = 60.0      # slow, comfortable assist speed. TODO: calibrate
MIN_REACH_SPAN = 0.02         # hand units; below this the reference barely moves the finger


@dataclass(frozen=True)
class ServoMove:
    finger: str
    direction: int            # +1 CW (flex) / -1 CCW (extend) / 0 hold
    degrees: float            # how far the servo should turn from where it is now
    speed_deg_s: float
    position: float           # finger position 0-1 along the reference when planned

    @property
    def label(self) -> str:
        if self.direction == HOLD or self.degrees <= 0:
            return "hold"
        return f"{'flex (CW)' if self.direction == CW else 'extend (CCW)'} {self.degrees:.0f}°"


def finger_position(reach_cur: float, reach_start: float, reach_end: float) -> float:
    """Fingertip reach -> 0..1 progress along the reference movement."""
    span = reach_end - reach_start
    if abs(span) < MIN_REACH_SPAN:
        return 1.0
    return min(1.0, max(0.0, (reach_cur - reach_start) / span))


def plan_move(movement_id: str, finger: str, reach_cur: float, reach_start: float,
              reach_end: float) -> ServoMove:
    """Servo command for one finger: direction + degrees left to reach the end pose."""
    pos = finger_position(reach_cur, reach_start, reach_end)
    remaining = reach_end - reach_cur
    if pos >= 1.0 or remaining == 0:
        return ServoMove(finger, HOLD, 0.0, 0.0, pos)
    # target further from the wrist -> extend (CCW); closer -> flex (CW)
    direction = CCW if remaining > 0 else CW
    share = MOVEMENT_PROFILES.get(movement_id, {}).get(finger, 1.0)
    degrees = (1.0 - pos) * share * SERVO_TRAVEL_DEG[finger]
    return ServoMove(finger, direction, degrees, SERVO_SPEED_DEG_S, pos)


def trigger(moves: list[ServoMove]) -> str:
    """Hand the planned moves to the glove.

    For now this only reports "Servo motor triggered". When the firmware accepts
    angle/speed commands, send them from here (servo_link carries direction only).
    """
    detail = ", ".join(f"{m.finger} {m.label}" for m in moves)
    msg = f"Servo motor triggered: {detail}"
    print(msg, flush=True)
    return msg
