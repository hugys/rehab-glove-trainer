"""Plain-language explanations behind the small "i" icons, one place for all of them.

Written for the physician, not the developer. Keep the numbers in sync with config.py.
"""
from __future__ import annotations

INFO: dict[str, str] = {
    "pct_hand": (
        "<b>% of hand size</b><br>Distances are given as a share of the patient's own hand "
        "length (wrist crease to middle-finger knuckle), which the camera measures in every "
        "frame. 10 % ≈ 9 mm on an average adult hand (90 mm). No ruler is needed, and small "
        "and large hands are directly comparable."),
    "hand_size": (
        "<b>Hand size (optional)</b><br>Wrist crease to the knuckle of the middle finger, in "
        "mm, measured with a ruler.<br><br>The app does not need it: every result is a % of "
        "hand size, measured by the camera. If you enter it, results also show the equivalent "
        "in millimetres (e.g. 10 % on a 90 mm hand ≈ 9 mm)."),
    "takes": (
        "<b>Takes</b><br>How many times the healthy hand performs the movement for the "
        "reference. The takes are averaged into one smooth reference, which evens out natural "
        "wobble in a single recording. 3 is recommended."),
    "speed": (
        "<b>Speed</b><br>How fast the patient should move compared with the healthy-hand "
        "recording (4 s at 100 %). 50 % = 8 s, 200 % = 2 s.<br><br>The reference shown to the "
        "patient, the error and the stall check all follow this speed. Lower it when the "
        "affected hand cannot keep up; raise it over the weeks as a measure of progress."),
    "reps": "<b>Repetitions</b><br>How many times the movement is performed in this session.",
    "threshold": (
        "<b>Error threshold</b><br>A finger whose error is above this is shown red and counted "
        "in “time above threshold” in the results.<br><br>For assessment only: it never moves "
        "the glove. In % of hand size (10 % ≈ 9 mm on a 90 mm hand)."),
    "stall_time": (
        "<b>Stall time t</b><br>How long a finger may stop before the glove motor for that "
        "finger takes over and moves it to the end position.<br><br>A finger only counts as "
        "stopped if it is also behind where the reference is at that moment, so a patient "
        "keeping pace at a slow speed is never interrupted."),
    "grace": (
        "<b>Start-up grace</b><br>No stops are judged during the first seconds of each "
        "repetition, so a slow start is not mistaken for a stall. 0 = off."),
    "not_moving": (
        "<b>“Not moving” below</b><br>Fingertip movement within the stall time t below this "
        "counts as stopped, in % of hand size.<br><br>This is an upper limit: at slow speeds "
        "the app lowers it automatically to half of what the reference itself moves in t "
        "(never below 1 %, the camera's own jitter). Too low: stops are missed. Too high: slow "
        "movement is taken for a stop."),
    "complete": (
        "<b>“Complete” within</b><br>A finger has reached the end position when it is within "
        "this share of <i>its own</i> movement in the reference, or past it in the right "
        "direction (a fist closed tighter than the reference counts as done and is never "
        "pushed back).<br><br>So 10 % means about 12 % of hand size for a full fist, but under "
        "2 % for the pinky in a precision grip. Fingers that should stay still during a "
        "movement must stay within 8 % of hand size."),
    "error": (
        "<b>Error</b><br>Distance between the patient's finger and the healthy-hand reference "
        "at the same point of the movement, averaged over the tracked points of that finger. "
        "In % of hand size (mm in brackets if the hand size was entered). 0 = identical to "
        "the reference.<br><br>For assessment only: the glove is driven by stalls, not by "
        "error."),
    "mean_error": (
        "<b>Mean error</b><br>Average error of the five fingers, in % of hand size (mm in "
        "brackets if the hand size was entered)."),
    "confidence": (
        "<b>Tracking confidence</b><br>How sure the camera's hand tracker is that it sees a "
        "hand, 0-100 %. Below about 80 % the measurements become unreliable: improve the "
        "lighting or keep the whole hand in view."),
    "glove_cmd": (
        "<b>Glove command</b><br>What is sent to the glove, one number per finger in the order "
        "thumb, index, middle, ring, pinky:<br>+1 = motor flexes (closes) the finger<br>"
        "−1 = motor extends (opens) it<br>0 = holds"),
    "finger_panel": (
        "<b>Per finger</b><br>Bar = error (dashed line = threshold; green / amber / red).<br>"
        "<b>moving</b> = the patient is moving the finger.<br><b>ASSIST</b> = it stalled and "
        "the glove is moving it.<br><b>✓ done</b> = it reached the end position."),
    "rom": (
        "<b>Range of motion</b><br>How far each fingertip travelled towards or away from the "
        "wrist during the reference."),
    "time_above": (
        "<b>Time above threshold</b><br>Share of the tracked time in which the finger's error "
        "was above the session's threshold."),
    "time_assist": (
        "<b>Time glove assisting</b><br>Share of the tracked time in which the glove was moving "
        "the finger because it had stalled."),
    "landmarks": (
        "<b>Landmark set</b><br>How many points on the hand are tracked. 12 = the team's "
        "original set; 15 adds the knuckles. References must be recorded with the same set "
        "they are used with."),
}
