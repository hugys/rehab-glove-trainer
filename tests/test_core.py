import sqlite3

import numpy as np
import pytest

from rehab_app import config
from rehab_app.config import FINGERS, LandmarkSet
from rehab_app.data import export
from rehab_app.data.database import FRAME_INSERT_COLS, Database
from rehab_app.data.video import VideoRecorder
from rehab_app.engine import SessionParams
from rehab_app.engine import tracking_core as tc
from rehab_app.engine.mock_engine import MockEngine
from rehab_app.hardware.servo_link import NullServoLink, format_line


@pytest.mark.parametrize("speed,expected", [(100, 4.0), (50, 8.0), (200, 2.0), (25, 16.0)])
def test_session_duration(speed, expected):
    assert config.session_duration_s(speed) == pytest.approx(expected)


@pytest.mark.parametrize("preset,count", [("12", 12), ("15", 15)])
def test_landmark_presets(preset, count):
    ls = LandmarkSet.from_preset(preset)
    assert ls.count == count
    groups = ls.finger_groups()
    covered = sorted(r for rows in groups.values() for r in rows)
    assert covered == list(range(1, count))
    tc.Geometry.from_landmark_set(ls)  # asserts wrist first, every finger present


def test_12_matches_team_script():
    assert LandmarkSet.from_preset("12").mediapipe_indices == [0, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 20]


def test_normalize_pose_wrist_scale_mirror():
    geom = tc.Geometry.from_landmark_set(LandmarkSet.from_preset("12"))
    pts = np.random.default_rng(0).normal(size=(21, 3)) * 50 + 200
    out = tc.normalize_pose(pts, geom, mirror=True, scale=True)
    assert np.allclose(out[0], 0)
    rel = pts - pts[0]
    size = np.linalg.norm(rel[9])
    assert np.allclose(out[1], rel[2] / size * [-1, 1, 1])


def test_stall_assist_triggers_after_window_and_respects_grace():
    geom = tc.Geometry.from_landmark_set(LandmarkSet.from_preset("12"))
    pose = np.zeros((12, 3))
    pose[geom.tip_rows, 1] = -1.0             # fingertips 1 hand-unit from wrist
    target = pose.copy()
    target[geom.tip_rows, 1] = -0.5            # target closer to the wrist -> flex
    sa = tc.StallAssist(geom, window_s=1.0, move_eps=0.04, complete_tol=0.08, start_grace_s=0.0)
    states = None
    for i in range(40):                       # finger frozen for 1.3 s
        cmds, states = sa.update(pose, target, i / 30)
    assert states == ["assisting"] * 5
    assert cmds == [tc.CW] * 5                # flex toward the wrist = CW (+1)
    sa2 = tc.StallAssist(geom, window_s=1.0, start_grace_s=2.0)
    for i in range(40):
        _, states = sa2.update(pose, target, i / 30)
    assert "assisting" not in states          # still inside the grace period
    _, states = sa.update(target, target, 2.0)
    assert states == ["done"] * 5


def _record(engine, movement="close_fist", takes=1):
    engine.start_reference(movement, config.REF_DURATION_S, "right", 90.0)
    for t in range(takes):
        engine.start_take(t)
        for i in range(121):
            engine.reference_frame(i / 30)
        engine.end_take()
    return engine.finish_reference()


@pytest.mark.parametrize("preset", ["12", "15"])
def test_mock_pipeline_and_assist(preset):
    ls = LandmarkSet.from_preset(preset)
    eng = MockEngine(ls)
    ref = _record(eng, takes=3)
    assert ref.data.shape == (101, ls.count, 3) and ref.n_takes == 3
    assert ref.tolerance.shape == (101, 5) and ref.units == "hand"
    assert np.allclose(ref.data[:, 0], 0)
    eng.start_session(ref, SessionParams(100, 10, 90, 0.5, 4, 8, start_grace_s=1.0))
    seen = set()
    for i in range(121):
        fr = eng.session_frame(i / 30)
        seen |= set((fr.assist_state or {}).values())
    assert "assisting" in seen                # the simulated ring finger stalls
    assert set(fr.finger_error_pct) == set(FINGERS)


def test_session_rejects_other_landmark_set():
    ref = _record(MockEngine(LandmarkSet.from_preset("15")))
    eng = MockEngine(LandmarkSet.from_preset("12"))
    with pytest.raises(Exception):
        eng.start_session(ref, SessionParams(100, 10, 90, 1, 4, 8))


def test_servo_protocol():
    link = NullServoLink()
    assert link.send({"thumb": -1, "middle": 1}, force=True)
    assert format_line(link.last_codes) == "-1,0,1,0,0\n"
    link.release_all()
    assert link.last_codes == [0, 0, 0, 0, 0]


def test_database_roundtrip(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.add_patient("Jane Doe", "left", patient_code="P01", hand_size_mm=85)
    ls = LandmarkSet.from_preset("15")
    ref = _record(MockEngine(ls), "open_fist", takes=2)
    rid = db.save_reference(pid, ref)
    back = db.latest_reference(pid, "open_fist", ls)
    assert np.allclose(back.data, ref.data) and np.allclose(back.tolerance, ref.tolerance)
    assert back.n_takes == 2 and back.meta["reference_id"] == rid
    assert db.latest_reference(pid, "open_fist", LandmarkSet.from_preset("12")) is None
    assert set(db.reference_status(pid, ls)) == {"open_fist"}

    sid = db.create_session(pid, "open_fist", rid, "15", 50, 10, 2, stall_window_s=1.5)
    row = (1, 0.5, 10.0, 0.9, 1, 5, 6, 7, 15, 12, 9, 0b11000,
           "done", "moving", "moving", "assisting", "idle", 0, 0, 0, 1, 0, "left",
           95, 94, 93, 85, 88)
    assert len(row) == len(FRAME_INSERT_COLS)
    db.add_frames(sid, [row] * 3 + [row[:22] + ("right",) + row[23:]])
    assert db.rep_hands(sid) == {1: "left"}           # majority of the rep
    summ = db.session_summary(sid)
    assert summ["mean"]["ring"] == pytest.approx(15)
    assert summ["pct_over"]["ring"] == pytest.approx(100)
    assert summ["pct_assist"]["ring"] == pytest.approx(100)
    assert summ["pct_assist"]["thumb"] == pytest.approx(0)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")
    db.add_video(pid, "session", "open_fist", str(vid), 1, session_id=sid)
    files = export.export_patient(db, pid, tmp_path / "out")
    assert all(f.exists() for f in files)
    export.export_reference(db, rid, tmp_path / "ref.csv")
    assert len((tmp_path / "ref.csv").read_text().splitlines()) == 2 + 101 * 15
    assert db.delete_patient(pid) == [str(vid)]
    assert db.list_sessions(pid) == []


def test_migrates_old_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE patients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        patient_code TEXT, affected_hand TEXT NOT NULL, dob TEXT, notes TEXT, created_at TEXT NOT NULL);
      INSERT INTO patients(name, affected_hand, created_at) VALUES ('Old', 'left', '2026-01-01');
    """)
    con.commit()
    con.close()
    db = Database(path)
    assert db.get_patient(1)["hand_size_mm"] == config.HAND_SIZE_DEFAULT_MM


def test_video_recorder_matches_wall_clock(tmp_path):
    cv2 = pytest.importorskip("cv2")
    rec = VideoRecorder(tmp_path / "a.mp4", fps=30)
    frame = np.zeros((120, 160, 3), np.uint8)
    for i in range(50):                       # 50 frames delivered over 4 s (~12 fps camera)
        rec.write(frame, i * 4.0 / 49)
    path = rec.close(4.0)
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    assert abs(n - 121) <= 1                  # 4 s at 30 fps regardless of camera rate


def test_servo_motion_plan():
    from rehab_app.hardware import servo_motion as sm
    # close_fist: reach shrinks 1.0 -> 0.5; finger stuck halfway -> flex half the travel
    m = sm.plan_move("close_fist", "index", 0.75, 1.0, 0.5)
    assert m.direction == sm.CW and m.position == pytest.approx(0.5)
    assert m.degrees == pytest.approx(0.5 * sm.SERVO_TRAVEL_DEG["index"])
    # open_fist: reach grows -> extend; stuck at the start -> full travel
    m = sm.plan_move("open_fist", "ring", 0.5, 0.5, 1.0)
    assert m.direction == sm.CCW and m.degrees == pytest.approx(sm.SERVO_TRAVEL_DEG["ring"])
    # at target -> hold
    assert sm.plan_move("close_fist", "thumb", 0.5, 1.0, 0.5).direction == sm.HOLD
    assert "Servo motor triggered" in sm.trigger([m])


def test_session_frames_carry_servo_moves():
    eng = MockEngine(LandmarkSet.from_preset("12"))
    ref = _record(eng, takes=1)
    eng.start_session(ref, SessionParams(100, 10, 90, 0.5, 4, 8, start_grace_s=1.0))
    planned = set()
    for i in range(121):
        fr = eng.session_frame(i / 30)
        moves = fr.servo_moves or {}
        for f, st in (fr.assist_state or {}).items():
            assert (st == "assisting") == (f in moves)
        for f, m in moves.items():
            assert m.direction == fr.assist_cmd[f] and m.degrees > 0
            planned.add(f)
    assert planned                            # the simulated ring finger stalls


@pytest.mark.parametrize("speed", [25, 50, 100, 200])
def test_stall_is_speed_aware(speed):
    """Keeping pace with a slow reference is not a stall; freezing halfway still is."""
    ls = LandmarkSet.from_preset("12")
    geom = tc.Geometry.from_landmark_set(ls)
    ref = _record(MockEngine(ls), "precision_grip")
    tips = list(geom.tip_rows)
    reach = np.linalg.norm(ref.data[:, tips, :], axis=-1)
    dur = config.session_duration_s(speed)
    for freeze in (False, True):
        sa = tc.StallAssist(geom, 1.0, 0.04, 0.08, 2.0, ref_reach=reach, duration_s=dur)
        seen = set()
        for k in range(int(dur * 30) + 1):
            p = k / 30 / dur
            pose = tc.interp_rows(ref.data, min(p, 0.5) if freeze else p)
            _, states = sa.update(pose, ref.data[-1], k / 30, p)
            seen |= set(states)
        assert ("assisting" in seen) == freeze


@pytest.mark.parametrize("speed", [50, 100, 200])
def test_reference_follows_clinician_speed(speed):
    """The pose the patient is compared against (and that the patient view shows) is the
    reference stretched to the session duration set by the speed."""
    ls = LandmarkSet.from_preset("12")
    eng = MockEngine(ls)
    eng._rng = np.random.default_rng(0)       # the mock drops 1 % of detections at random
    ref = _record(eng)
    eng.start_session(ref, SessionParams(speed, 10, 90, 1, 4, 8))
    t = 2.0
    for i in range(int(t * 30) + 1):
        fr = eng.session_frame(i / 30)
    expected = min(1.0, t / config.session_duration_s(speed))
    assert fr.progress == pytest.approx(100 * expected)
    assert np.allclose(fr.reference_landmarks,
                       eng._display(tc.interp_rows(ref.data, expected)), atol=1e-6)


def _reach(ref, geom):
    return np.linalg.norm(ref.data[:, list(geom.tip_rows), :], axis=-1)


def test_completion_is_per_finger_and_direction_aware():
    ls = LandmarkSet.from_preset("12")
    geom = tc.Geometry.from_landmark_set(ls)
    ref = _record(MockEngine(ls), "precision_grip")
    reach = _reach(ref, geom)
    span = reach[-1] - reach[0]
    sa = tc.StallAssist(geom, ref_reach=reach, duration_s=4.0, complete_tol_move=0.10)
    pinky = FINGERS.index("pinky")
    # A flat 8 % of hand size was about half the pinky's movement in a precision grip
    assert 0.08 / abs(span[pinky]) > 0.4
    assert sa.tol[pinky] == pytest.approx(max(tc.COMPLETE_TOL_FLOOR, 0.10 * abs(span[pinky])))
    # Halfway is not done; past the end in the movement's direction is done (never pushed back)
    assert not sa._complete(pinky, 0.5 * span[pinky])
    assert sa._complete(pinky, -0.5 * span[pinky])
    # A finger the reference doesn't move keeps the absolute tolerance both ways
    still = tc.StallAssist(geom, ref_reach=np.zeros((101, 5)) + 1.0, duration_s=4.0,
                           complete_tol_move=0.10, complete_tol=0.08)
    assert still._complete(0, 0.05) and still._complete(0, -0.05) and not still._complete(0, 0.2)


def test_hand_size_optional_and_legacy_mm_sessions(tmp_path):
    from rehab_app.data.database import session_threshold
    db = Database(tmp_path / "t.db")
    pid = db.add_patient("No Ruler", "right")
    assert db.get_patient(pid)["hand_size_known"] == 0
    db.update_patient(pid, hand_size_mm=80)
    assert db.get_patient(pid)["hand_size_known"] == 1
    db.update_patient(pid, hand_size_mm=None)
    assert db.get_patient(pid)["hand_size_known"] == 0
    sid = db.create_session(pid, "open_fist", None, "12", 100, 12.0, 1)
    s = db.get_session(sid)
    assert s["err_units"] == "move" and session_threshold(s) == 12.0 and s["threshold_mm"] == 0
    # a session from before the change: mm units, threshold in threshold_mm
    db.conn.execute("UPDATE sessions SET err_units = 'mm', threshold_mm = 10 WHERE id = ?", (sid,))
    assert session_threshold(db.get_session(sid)) == 10


def test_fmt_err():
    from rehab_app.ui.units import fmt_err
    assert fmt_err(10.0) == "10.0 %"
    assert fmt_err(10.0, 90) == "10.0 % (9.0 mm)"
    assert fmt_err(7.5, 90, "mm") == "7.5 mm"
    assert fmt_err(None) == fmt_err(float("nan")) == "--"
    assert fmt_err(25.0, 90, "move") == "25.0 %"       # % of movement: no mm


def _axis_setup():
    """Reference that opens one finger along a straight line: start curled, end stretched."""
    geom = tc.Geometry.from_landmark_set(LandmarkSet.from_preset("12"))
    data = np.zeros((tc.N_PROGRESS, 12, 3))
    start, end = np.array([0.0, -0.5, 0.0]), np.array([0.0, -1.5, 0.0])
    rows = list(geom.finger_rows[1])                  # index finger
    for k, t in enumerate(np.linspace(0, 1, tc.N_PROGRESS)):
        data[k, rows] = start + (end - start) * t
    return geom, data, rows, start, end


def test_completion_is_along_the_movement_axis():
    geom, data, rows, start, end = _axis_setup()
    axis = tc.MovementAxis(data, geom)
    i = 1
    assert axis.moving[i] and not axis.moving[0]      # thumb doesn't move: no axis
    cur = data[-1].copy()
    assert axis.completion(cur)[i] == pytest.approx(1)          # fully stretched
    cur[rows] = start + (end - start) * 0.5           # halfway open
    assert axis.completion(cur)[i] == pytest.approx(0.5)
    cur[rows] += np.array([0.3, 0.0, 0.7])            # sideways / depth: not counted
    assert axis.completion(cur)[i] == pytest.approx(0.5)
    assert np.isnan(axis.completion(cur)[0])


def test_still_finger_error_is_scaled_drift():
    geom, data, *_ = _axis_setup()
    axis = tc.MovementAxis(data, geom)
    cur = data[-1].copy()
    cur[list(geom.finger_rows[0])] += np.array([tc.STILL_ERR_SCALE, 0, 0])   # thumb drifts
    drift = axis.drift(cur)
    assert drift[0] == pytest.approx(1.0) and np.isnan(drift[1])


def test_error_is_the_part_of_the_movement_not_reached(tmp_path):
    """Halfway open = 50 % error, fully open = 0 %, whatever the timing; a repetition's
    error is what is left at its end."""
    from rehab_app.engine.pipeline import Measurement, PipelineEngine
    ls = LandmarkSet.from_preset("12")

    class Scripted(PipelineEngine):
        flex = 1.0
        other_hand = False                              # the reference is the opposite hand
        def open_camera(self, source=None): pass
        def close_camera(self): pass
        def _measure(self, elapsed_s):
            from rehab_app.engine.mock_engine import hand_pose21
            px = hand_pose21({f: self.flex for f in FINGERS})
            px[:, 1] *= -1                              # image y-down
            if self.other_hand:
                px[:, 0] *= -1
            return Measurement(image=None, detected=True, overlay_px=px[:, :2], confidence=1.0,
                               hand="left", new_pts21=px, new_t=elapsed_s)

    eng = Scripted(ls)
    eng.other_hand = True
    eng.start_reference("open_fist", 4.0, "right", 90.0)
    eng.start_take(0)
    for i in range(121):
        eng.flex = 1.0 - i / 120                        # fist -> open
        eng.reference_frame(i / 30)
    eng.end_take()
    ref = eng.finish_reference()
    eng.other_hand = False

    def rep(flexes):
        eng.start_session(ref, SessionParams(100, 15, None, 1.0, 4.0, 10.0))
        for i, eng.flex in enumerate(flexes):
            fr = eng.session_frame(4.0 * i / len(flexes))
        return fr
    fr = rep([1.0] * 30)                                # never opens
    assert fr.finger_error_pct["index"] > 90
    fr = rep([1.0] * 20 + [0.0] * 10)                   # late, but fully open
    assert fr.finger_error_pct["index"] < 5 and fr.overall_error_pct < 5
    fr = rep([1.0] * 5 + [0.0] * 5 + [1.0] * 20)        # opened fully, then closed again
    assert fr.finger_error_pct["index"] < 5             # the furthest point counts
    half = rep([1.0] * 10 + [0.5] * 20)                 # opens only part of the way
    assert 20 < half.finger_error_pct["index"] < 80

    db = Database(tmp_path / "t.db")
    pid = db.add_patient("A", "left")
    sid = db.create_session(pid, "open_fist", None, "12", 100, 15, 2)
    def row(rep_no, t, e):
        return (rep_no, t, 25 * t, 1.0, 1, e, e, e, e, e, e, 0, *["moving"] * 5, *[0] * 5, "left",
                *[100 - e] * 5)
    db.add_frames(sid, [row(1, 0, 100), row(1, 2, 60), row(1, 4, 10),     # ends at 10 %
                        row(2, 0, 100), row(2, 4, 50)])                    # ends at 50 %
    summ = db.session_summary(sid)
    assert summ["mean"]["index"] == pytest.approx(30) and summ["overall"] == pytest.approx(30)
    assert summ["pct_over"]["index"] == pytest.approx(50)          # 1 of 2 reps above 15 %
    assert db.list_sessions(pid)[0]["mean_err"] == pytest.approx(30)


def test_align_rotates_hand_upright():
    geom = tc.Geometry.from_landmark_set(LandmarkSet.from_preset("12"))
    pts = np.random.default_rng(1).normal(size=(21, 3))
    pts[0] = 0
    pts[9] = [0, -2, 0]                               # knuckle straight up (image y-down)
    upright = tc.normalize_pose(pts, geom, False, True, align=True)
    a = np.radians(35)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    tilted = pts.copy()
    tilted[:, :2] = pts[:, :2] @ rot.T                # same hand, tilted 35 degrees
    assert np.allclose(tc.normalize_pose(tilted, geom, False, True, align=True), upright)
    assert np.allclose(upright, tc.normalize_pose(pts, geom, False, True))   # already upright


def test_real_hand_keeps_mediapipe_label():
    from rehab_app.engine.mediapipe_engine import real_hand
    assert real_hand("Left") == "left" and real_hand("Right") == "right"
    assert real_hand(None) is None


def test_mock_session_reports_hand_and_completion():
    ls = LandmarkSet.from_preset("12")
    eng = MockEngine(ls)
    ref = _record(eng, "open_fist")
    assert ref.meta["aligned"] and ref.source_hand == "right"
    eng.start_session(ref, SessionParams(100, 15, None, 1.0, 4.0, 10.0))
    frames = [eng.session_frame(t / 30) for t in range(1, 120)]
    hands = {f.hand for f in frames if f.hand}
    assert hands == {"left"}                          # affected = the other hand
    last = [f for f in frames if f.finger_completion_pct][-1]
    assert last.finger_completion_pct["index"] > 80   # the mock index finger opens fully


def test_hand_tally():
    from types import SimpleNamespace
    from rehab_app.ui.hands import HandTally
    t = HandTally()
    for h in ("left", "left", "right", None):
        t.add(SimpleNamespace(new_measurement=True, hand=h))
    assert t.recorded() == "left"
    assert t.label("left") == "left hand ✓"
    assert t.label("right").startswith("⚠ LEFT hand")


def test_time_up_assists_unfinished_fingers_and_freezes_their_error():
    """No stall within the movement time, but some fingers are not at the end when it is
    over: the glove finishes them, and what it does is not credited to the patient."""
    ls = LandmarkSet.from_preset("12")
    eng = MockEngine(ls)
    ref = _record(eng, "open_fist")
    dur = config.session_duration_s(100)
    eng.start_session(ref, SessionParams(100, 15, None, 30.0, 4.0, 10.0))   # stall time 30 s
    fr = None
    for i in range(int(dur * 30)):
        fr = eng.session_frame(i / 30)
        assert "assisting" not in fr.assist_state.values() and not fr.finishing
    before = dict(fr.finger_error_pct)
    assert before["ring"] > 25                        # the mock ring finger stops halfway
    t, started = dur, False
    while t < dur + config.ASSIST_FINISH_MAX_S:
        fr = eng.session_frame(t)
        t += 1 / 30
        started |= fr.assist_state["ring"] == "assisting" and fr.assist_cmd["ring"] == tc.CCW
        if all(s == "done" for s in fr.assist_state.values()):
            break
    assert fr.finishing and started and fr.progress == 100
    assert all(s == "done" for s in fr.assist_state.values())      # the glove finished it
    assert fr.finger_error_pct["ring"] == pytest.approx(before["ring"], abs=3)
    assert fr.finger_error_pct["index"] < 10          # done by the patient: no assist needed


def test_stall_assist_force():
    geom = tc.Geometry.from_landmark_set(LandmarkSet.from_preset("12"))
    pose = np.zeros((12, 3))
    pose[geom.tip_rows, 1] = -1.0
    target = pose.copy()
    target[geom.tip_rows[1], 1] = -0.5                # only the index is not at the target
    sa = tc.StallAssist(geom, window_s=5.0)
    cmds, states = sa.update(pose, target, 0.0)
    assert states[1] == "idle"
    cmds, states = sa.update(pose, target, 0.1, force=True)
    assert states[1] == "assisting" and cmds[1] == tc.CW
    assert states[0] == "done" and cmds[0] == tc.HOLD


def test_assist_spans_on_the_graph_axis():
    from rehab_app.ui.pages.results import assist_spans
    rows = [{"t_s": t, "state_ring": st} for t, st in
            [(0, "moving"), (1, "assisting"), (2, "assisting"), (3, "done"), (4, "done")]]
    assert assist_spans(rows, "ring", lambda r: 25.0 * r["t_s"]) == [(25.0, 75.0)]
    rows = [{"t_s": t, "state_ring": st} for t, st in [(4, "moving"), (5, "assisting"),
                                                       (6, "assisting")]]
    assert assist_spans(rows, "ring", lambda r: 25.0 * r["t_s"]) == [(125.0, 150.0)]


def test_position_keeps_the_gloves_part_but_error_does_not():
    ls = LandmarkSet.from_preset("12")
    eng = MockEngine(ls)
    ref = _record(eng, "open_fist")
    eng.start_session(ref, SessionParams(100, 15, None, 1.0, 4.0, 10.0))
    fr, t = None, 0.0
    while t < 4.0 + config.ASSIST_FINISH_MAX_S:
        fr = eng.session_frame(t)
        t += 1 / 30
        if t > 4.0 and all(s == "done" for s in fr.assist_state.values()):
            break
    assert fr.finger_completion_pct["ring"] > 85      # the glove got it there...
    assert fr.finger_error_pct["ring"] > 25           # ...which is not the patient's doing


class _Fed(MockEngine):
    """Mock hand with a zoom factor: zoom = the hand moving towards the camera."""
    zoom = 1.0

    def _to_pixels(self, pose_mm):
        px = super()._to_pixels(pose_mm)
        centre = np.array([320.0, 240.0, 0.0])
        return centre + (px - centre) * self.zoom


def _session_reach(mount, still_frames, zoom_during):
    ls = LandmarkSet.from_preset("12")
    eng = _Fed(ls)
    eng._rng = np.random.default_rng(0)
    eng.camera_mount = mount
    for _ in range(40):
        eng.preview_frame()
    ref = _record(eng, "open_fist")
    for _ in range(still_frames):
        eng.preview_frame()
    eng.start_session(ref, SessionParams(100, 15, None, 30.0, 4.0, 10.0))
    eng.zoom = zoom_during
    frames = [eng.session_frame(i / 30) for i in range(1, 60)]
    return ref, frames


def test_stand_fixes_the_hand_size_measured_in_the_countdown():
    ref, frames = _session_reach("stand", 40, 1.0)
    assert ref.meta["size_2d"] and ref.meta["size_fixed"] == [True]
    assert all(f.size_fixed for f in frames)
    _, frames = _session_reach("stand", 5, 1.0)        # hand hardly seen in the countdown
    assert not any(f.size_fixed for f in frames)       # -> measured continuously instead
    _, frames = _session_reach("handheld", 40, 1.0)
    assert not any(f.size_fixed for f in frames)


def test_handheld_size_follows_the_distance_but_stand_does_not():
    """Hand-held, a hand that comes 30 % closer still measures the same; on a stand the
    size is fixed, so the same change shows up (the hand must stay at its distance)."""
    def span(frames):
        f = [x for x in frames if x.landmarks is not None][-1]
        return float(np.linalg.norm(f.landmarks[1]))
    _, near = _session_reach("handheld", 40, 1.3)
    _, same = _session_reach("handheld", 40, 1.0)
    assert span(near) == pytest.approx(span(same), rel=0.03)
    _, near = _session_reach("stand", 40, 1.3)
    _, same = _session_reach("stand", 40, 1.0)
    assert span(near) == pytest.approx(1.3 * span(same), rel=0.03)


def test_closed_fist_with_noisy_depth_is_not_at_the_end_position():
    """A fist's depth estimate can be huge. It must not make the fingertips look further
    from the wrist than an open hand (which marked every finger done before it moved)."""
    geom = tc.Geometry.from_landmark_set(LandmarkSet.from_preset("12"))
    fist = np.zeros((12, 3))
    fist[geom.tip_rows, 1] = -0.5
    fist[geom.tip_rows, 2] = 2.0                       # depth noise
    open_hand = np.zeros((12, 3))
    open_hand[geom.tip_rows, 1] = -1.5
    assert np.all(tc.tip_reach(fist, geom) < tc.tip_reach(open_hand, geom))
    sa = tc.StallAssist(geom, window_s=1.0)
    _, states = sa.update(fist, open_hand, 0.0)
    assert "done" not in states
    cmds, states = sa.update(fist, open_hand, 0.1, force=True)
    assert states == ["assisting"] * 5 and cmds == [tc.CCW] * 5     # extend
