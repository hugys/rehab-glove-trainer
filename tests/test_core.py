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
           "done", "moving", "moving", "assisting", "idle", 0, 0, 0, 1, 0)
    assert len(row) == len(FRAME_INSERT_COLS)
    db.add_frames(sid, [row] * 4)
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
    assert s["err_units"] == "pct" and session_threshold(s) == 12.0 and s["threshold_mm"] == 0
    # a session from before the change: mm units, threshold in threshold_mm
    db.conn.execute("UPDATE sessions SET err_units = 'mm', threshold_mm = 10 WHERE id = ?", (sid,))
    assert session_threshold(db.get_session(sid)) == 10


def test_fmt_err():
    from rehab_app.ui.units import fmt_err
    assert fmt_err(10.0) == "10.0 %"
    assert fmt_err(10.0, 90) == "10.0 % (9.0 mm)"
    assert fmt_err(7.5, 90, "mm") == "7.5 mm"
    assert fmt_err(None) == fmt_err(float("nan")) == "--"
