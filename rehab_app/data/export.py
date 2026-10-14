"""CSV export for sessions and reference trajectories (opens in Excel / MATLAB)."""
from __future__ import annotations

import csv
from pathlib import Path

from ..config import FINGERS, MOVEMENTS_BY_ID, LANDMARK_PRESETS
from .database import ERR_COLS, FRAME_INSERT_COLS, Database

FRAME_COLS = FRAME_INSERT_COLS


def _name(movement_id: str) -> str:
    m = MOVEMENTS_BY_ID.get(movement_id)
    return m.name if m else movement_id


def export_session(db: Database, session_id: int, path: Path | str) -> Path:
    """err_* = diagnostic distance to the reference (mm); state_*/cmd_* = stall assist
    (cmd: +1 flex, -1 extend, 0 hold) - what the glove motors were told."""
    path = Path(path)
    s = db.get_session(session_id)
    p = db.get_patient(s["patient_id"])
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["# patient", p["name"], p["patient_code"] or "", p["affected_hand"],
                    "hand_size_mm", s["hand_size_mm"]])
        w.writerow(["# session_id", session_id, "movement", _name(s["movement_id"]),
                    "speed_pct", s["speed_pct"], "threshold_mm", s["threshold_mm"],
                    "stall_time_s", s["stall_window_s"], "start_grace_s", s["start_grace_s"],
                    "stall_move_eps_pct", s["stall_move_eps_pct"],
                    "complete_tol_pct", s["complete_tol_pct"],
                    "landmarks", s["landmark_preset"], "started", s["started_at"]])
        w.writerow(FRAME_COLS)
        for row in db.session_frames(session_id):
            w.writerow([row[c] for c in FRAME_COLS])
    return path


def export_reference(db: Database, reference_id: int, path: Path | str) -> Path:
    """Long format: progress, landmark, x, y, z (in the reference's units)."""
    path = Path(path)
    ref = db.get_reference(reference_id)
    ids = ref.landmark_ids or [i for i, _ in LANDMARK_PRESETS[ref.landmark_preset]]
    unit = "hand_units" if ref.units == "hand" else ref.units
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["# movement", _name(ref.movement_id), "takes", ref.n_takes, "units", unit])
        w.writerow(["progress_pct", "landmark_id", "x", "y", "z"]
                   + ([f"tol_{f}" for f in FINGERS] if ref.tolerance is not None else []))
        for t, prog in enumerate(ref.progress):
            for r, lm_id in enumerate(ids):
                x, y, z = ref.data[t, r]
                row = [f"{prog:.1f}", lm_id, f"{x:.5f}", f"{y:.5f}", f"{z:.5f}"]
                if ref.tolerance is not None:
                    row += [f"{v:.5f}" for v in ref.tolerance[t]]
                w.writerow(row)
    return path


def export_patient(db: Database, patient_id: int, folder: Path | str) -> list[Path]:
    """Every session for a patient, one CSV each, plus a summary CSV."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    summary_path = folder / f"patient_{patient_id}_summary.csv"
    with summary_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_id", "started_at", "movement", "speed_pct", "threshold_mm",
                    "stall_time_s", "landmarks", *[f"mean_{c}" for c in ERR_COLS],
                    "mean_err_overall", *[f"pct_assist_{f}" for f in FINGERS]])
        for s in db.list_sessions(patient_id):
            summ = db.session_summary(s["id"])
            w.writerow([s["id"], s["started_at"], s["movement_id"], s["speed_pct"],
                        s["threshold_mm"], s["stall_window_s"], s["landmark_preset"],
                        *[summ["mean"][f] for f in FINGERS], summ["overall"],
                        *[round(summ["pct_assist"][f], 1) for f in FINGERS]])
            out.append(export_session(db, s["id"], folder / f"session_{s['id']}.csv"))
    out.insert(0, summary_path)
    return out
