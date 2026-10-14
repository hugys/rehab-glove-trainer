"""SQLite persistence: patients, references, sessions, per-frame logs, videos, settings."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import config
from ..config import FINGERS, LandmarkSet
from ..engine.interface import ReferenceTrajectory

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    patient_code  TEXT,
    affected_hand TEXT NOT NULL CHECK (affected_hand IN ('left', 'right')),
    dob           TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_trajectories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    movement_id     TEXT NOT NULL,
    landmark_preset TEXT NOT NULL,
    source_hand     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    n_steps         INTEGER NOT NULL,
    n_landmarks     INTEGER NOT NULL,
    progress        BLOB NOT NULL,
    data            BLOB NOT NULL,
    meta            TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    movement_id     TEXT NOT NULL,
    reference_id    INTEGER REFERENCES reference_trajectories(id) ON DELETE SET NULL,
    landmark_preset TEXT NOT NULL,
    speed_pct       REAL NOT NULL,
    threshold_mm    REAL NOT NULL,
    reps            INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    notes           TEXT
);
CREATE TABLE IF NOT EXISTS session_frames (
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    rep          INTEGER NOT NULL,
    t_s          REAL NOT NULL,
    progress     REAL NOT NULL,
    confidence   REAL,
    hand_detected INTEGER NOT NULL,
    err_thumb    REAL, err_index REAL, err_middle REAL, err_ring REAL, err_pinky REAL,
    err_overall  REAL,
    corr_mask    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_frames_session ON session_frames(session_id, rep);
CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id   INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('reference', 'session')),
    reference_id INTEGER REFERENCES reference_trajectories(id) ON DELETE CASCADE,
    session_id   INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    take_or_rep  INTEGER NOT NULL,
    movement_id  TEXT NOT NULL,
    path         TEXT NOT NULL,
    duration_s   REAL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first release - applied to existing databases on open.
MIGRATIONS = {
    "patients": [("hand_size_mm", f"REAL NOT NULL DEFAULT {config.HAND_SIZE_DEFAULT_MM}")],
    "reference_trajectories": [
        ("landmark_ids", "TEXT"), ("units", "TEXT NOT NULL DEFAULT 'mm'"),
        ("n_takes", "INTEGER NOT NULL DEFAULT 1"), ("tolerance", "BLOB"), ("rom", "BLOB"),
    ],
    "sessions": [
        ("hand_size_mm", "REAL"), ("stall_window_s", "REAL"), ("stall_move_eps_pct", "REAL"),
        ("complete_tol_pct", "REAL"), ("start_grace_s", "REAL"), ("servo", "TEXT"),
    ],
    "session_frames": [(f"state_{f}", "TEXT") for f in FINGERS]
                      + [(f"cmd_{f}", "INTEGER") for f in FINGERS],
}

ERR_COLS = [f"err_{f}" for f in FINGERS]
STATE_COLS = [f"state_{f}" for f in FINGERS]
CMD_COLS = [f"cmd_{f}" for f in FINGERS]
FRAME_INSERT_COLS = ["rep", "t_s", "progress", "confidence", "hand_detected", *ERR_COLS,
                     "err_overall", "corr_mask", *STATE_COLS, *CMD_COLS]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def corr_mask_from(correction: dict[str, bool] | None) -> int:
    if not correction:
        return 0
    return sum(1 << i for i, f in enumerate(FINGERS) if correction.get(f))


def _blob(a) -> bytes | None:
    return None if a is None else np.ascontiguousarray(a, dtype=np.float64).tobytes()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    # ---- settings ----------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_float(self, key: str, default: float) -> float:
        try:
            return float(self.get_setting(key, str(default)))
        except (TypeError, ValueError):
            return default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
        self.conn.commit()

    # ---- patients ----------------------------------------------------------
    def add_patient(self, name: str, affected_hand: str, patient_code: str = "",
                    dob: str = "", notes: str = "",
                    hand_size_mm: float = config.HAND_SIZE_DEFAULT_MM) -> int:
        cur = self.conn.execute(
            "INSERT INTO patients(name, patient_code, affected_hand, dob, notes, hand_size_mm,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, patient_code, affected_hand, dob, notes, hand_size_mm, _now()))
        self.conn.commit()
        return cur.lastrowid

    def update_patient(self, patient_id: int, **fields) -> None:
        allowed = {"name", "patient_code", "affected_hand", "dob", "notes", "hand_size_mm"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE patients SET {sets} WHERE id = ?", (*fields.values(), patient_id))
        self.conn.commit()

    def delete_patient(self, patient_id: int) -> list[str]:
        """Deletes the patient and all their rows. Returns the video file paths that
        belonged to them (the caller decides whether to delete the files)."""
        paths = [r["path"] for r in self.conn.execute(
            "SELECT path FROM videos WHERE patient_id = ?", (patient_id,))]
        self.conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        self.conn.commit()
        return paths

    def get_patient(self, patient_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()

    def list_patients(self, search: str = "") -> list[sqlite3.Row]:
        like = f"%{search}%"
        return self.conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM sessions s WHERE s.patient_id = p.id) AS n_sessions"
            " FROM patients p WHERE name LIKE ? OR patient_code LIKE ? ORDER BY name",
            (like, like)).fetchall()

    # ---- references --------------------------------------------------------
    def save_reference(self, patient_id: int, ref: ReferenceTrajectory) -> int:
        data = np.ascontiguousarray(ref.data, dtype=np.float64)
        cur = self.conn.execute(
            "INSERT INTO reference_trajectories(patient_id, movement_id, landmark_preset, source_hand,"
            " created_at, n_steps, n_landmarks, progress, data, meta, landmark_ids, units, n_takes,"
            " tolerance, rom) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (patient_id, ref.movement_id, ref.landmark_preset, ref.source_hand, _now(),
             data.shape[0], data.shape[1], _blob(ref.progress), data.tobytes(),
             json.dumps(ref.meta), json.dumps(list(map(int, ref.landmark_ids))), ref.units,
             ref.n_takes, _blob(ref.tolerance), _blob(ref.rom)))
        self.conn.commit()
        ref.meta["reference_id"] = cur.lastrowid
        return cur.lastrowid

    @staticmethod
    def _row_to_reference(row: sqlite3.Row) -> ReferenceTrajectory:
        n, k = row["n_steps"], row["n_landmarks"]
        prog = np.frombuffer(row["progress"], dtype=np.float64).copy()
        data = np.frombuffer(row["data"], dtype=np.float64).reshape(n, k, 3).copy()
        tol = (np.frombuffer(row["tolerance"], dtype=np.float64).reshape(n, len(FINGERS)).copy()
               if row["tolerance"] else None)
        rom = np.frombuffer(row["rom"], dtype=np.float64).copy() if row["rom"] else None
        return ReferenceTrajectory(
            movement_id=row["movement_id"], landmark_preset=row["landmark_preset"],
            landmark_ids=json.loads(row["landmark_ids"] or "[]"), progress=prog, data=data,
            tolerance=tol, rom=rom, n_takes=row["n_takes"], units=row["units"],
            source_hand=row["source_hand"],
            meta=json.loads(row["meta"] or "{}") | {"reference_id": row["id"],
                                                     "created_at": row["created_at"]})

    @staticmethod
    def _ids(ls: LandmarkSet) -> str:
        return json.dumps(list(ls.mediapipe_indices))

    # References recorded with the simulated hand must never become a real patient's
    # baseline: when running the real engine, they are hidden.
    real_engine_only = False

    def _engine_filter(self, alias: str = "") -> str:
        if not self.real_engine_only:
            return ""
        col = f"{alias}.meta" if alias else "meta"
        return f" AND COALESCE(json_extract({col}, '$.engine'), '') != 'MockEngine'"

    def latest_reference(self, patient_id: int, movement_id: str,
                         ls: LandmarkSet) -> ReferenceTrajectory | None:
        """Newest usable reference: same landmark set (exact indices) and hand-size units."""
        row = self.conn.execute(
            "SELECT * FROM reference_trajectories WHERE patient_id = ? AND movement_id = ? "
            "AND landmark_ids = ? AND units = 'hand'" + self._engine_filter() + " ORDER BY id DESC LIMIT 1",
            (patient_id, movement_id, self._ids(ls))).fetchone()
        return self._row_to_reference(row) if row else None

    def get_reference(self, reference_id: int) -> ReferenceTrajectory | None:
        row = self.conn.execute("SELECT * FROM reference_trajectories WHERE id = ?",
                                (reference_id,)).fetchone()
        return self._row_to_reference(row) if row else None

    def reference_status(self, patient_id: int, ls: LandmarkSet) -> dict[str, sqlite3.Row]:
        """movement_id -> row (id, created_at, n_takes) of the latest usable reference."""
        rows = self.conn.execute(
            "SELECT movement_id, id, created_at, n_takes FROM reference_trajectories r "
            "WHERE patient_id = ? AND landmark_ids = ? AND units = 'hand' AND id = ("
            "  SELECT MAX(id) FROM reference_trajectories r2 WHERE r2.patient_id = r.patient_id"
            "  AND r2.movement_id = r.movement_id AND r2.landmark_ids = r.landmark_ids"
            "  AND r2.units = 'hand'" + self._engine_filter("r2") + ")",
            (patient_id, self._ids(ls))).fetchall()
        return {r["movement_id"]: r for r in rows}

    # ---- sessions ----------------------------------------------------------
    def create_session(self, patient_id: int, movement_id: str, reference_id: int | None,
                       landmark_preset: str, speed_pct: float, threshold_mm: float,
                       reps: int, notes: str = "", **assist) -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions(patient_id, movement_id, reference_id, landmark_preset, speed_pct,"
            " threshold_mm, reps, started_at, notes, hand_size_mm, stall_window_s,"
            " stall_move_eps_pct, complete_tol_pct, start_grace_s, servo)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (patient_id, movement_id, reference_id, landmark_preset, speed_pct, threshold_mm,
             reps, _now(), notes, assist.get("hand_size_mm"), assist.get("stall_window_s"),
             assist.get("stall_move_eps_pct"), assist.get("complete_tol_pct"),
             assist.get("start_grace_s"), assist.get("servo")))
        self.conn.commit()
        return cur.lastrowid

    def end_session(self, session_id: int) -> None:
        self.conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (_now(), session_id))
        self.conn.commit()

    def add_frames(self, session_id: int, frames: list[tuple]) -> None:
        """frames: tuples in FRAME_INSERT_COLS order."""
        cols = ", ".join(FRAME_INSERT_COLS)
        marks = ",".join("?" * (len(FRAME_INSERT_COLS) + 1))
        self.conn.executemany(f"INSERT INTO session_frames(session_id, {cols}) VALUES ({marks})",
                              [(session_id, *f) for f in frames])
        self.conn.commit()

    def list_sessions(self, patient_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.*, "
            " (SELECT AVG(err_overall) FROM session_frames f WHERE f.session_id = s.id) AS mean_err,"
            " (SELECT COUNT(DISTINCT rep) FROM session_frames f WHERE f.session_id = s.id) AS reps_done"
            " FROM sessions s WHERE patient_id = ? ORDER BY s.id DESC", (patient_id,)).fetchall()

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def session_frames(self, session_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM session_frames WHERE session_id = ? ORDER BY rep, t_s",
            (session_id,)).fetchall()

    def session_summary(self, session_id: int) -> dict:
        """Mean error per finger, % of tracked frames above threshold, % of frames assisted."""
        s = self.get_session(session_id)
        cols = ", ".join(f"AVG({c}) AS {c}" for c in ERR_COLS)
        over = ", ".join(f"AVG(CASE WHEN {c} > :thr THEN 1.0 ELSE 0.0 END) AS over_{c}"
                         for c in ERR_COLS)
        assist = ", ".join(f"AVG(CASE WHEN state_{f} = 'assisting' THEN 1.0 ELSE 0.0 END) AS as_{f}"
                           for f in FINGERS)
        row = self.conn.execute(
            f"SELECT {cols}, AVG(err_overall) AS err_overall, {over}, {assist}, COUNT(*) AS n "
            "FROM session_frames WHERE session_id = :sid AND hand_detected = 1",
            {"sid": session_id, "thr": s["threshold_mm"]}).fetchone()
        return {
            "mean": {f: row[f"err_{f}"] for f in FINGERS},
            "overall": row["err_overall"],
            "pct_over": {f: (row[f"over_err_{f}"] or 0) * 100 for f in FINGERS},
            "pct_assist": {f: (row[f"as_{f}"] or 0) * 100 for f in FINGERS},
            "n_frames": row["n"],
        }

    def delete_session(self, session_id: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()

    # ---- videos ------------------------------------------------------------
    def add_video(self, patient_id: int, kind: str, movement_id: str, path: str,
                  take_or_rep: int, duration_s: float | None = None,
                  reference_id: int | None = None, session_id: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO videos(patient_id, kind, reference_id, session_id, take_or_rep,"
            " movement_id, path, duration_s, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (patient_id, kind, reference_id, session_id, take_or_rep, movement_id, str(path),
             duration_s, _now()))
        self.conn.commit()
        return cur.lastrowid

    def videos_for_session(self, session_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM videos WHERE session_id = ? ORDER BY take_or_rep",
                                 (session_id,)).fetchall()

    def videos_for_reference(self, reference_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM videos WHERE reference_id = ? ORDER BY take_or_rep",
                                 (reference_id,)).fetchall()

    def videos_for_patient(self, patient_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM videos WHERE patient_id = ? ORDER BY id DESC",
                                 (patient_id,)).fetchall()
