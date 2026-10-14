"""Rehab Glove Trainer - clinician + patient front end around the MediaPipe hand-tracking engine.

    python main.py                         # MediaPipe engine, auto-detected camera
    python main.py --camera 1              # a specific camera index
    python main.py --video clip.mp4        # run on a pre-recorded video instead of a camera
    python main.py --engine mock           # simulated hand, no camera needed
    python main.py --landmarks 15          # 15-point landmark set instead of 12
"""
from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from rehab_app import config
from rehab_app.config import LandmarkSet
from rehab_app.data.database import Database
from rehab_app.engine import EngineError, create_engine
from rehab_app.hardware.servo_link import NullServoLink, SerialServoLink
from rehab_app.ui.context import AppContext
from rehab_app.ui.main_window import MainWindow
from rehab_app.ui.patient_display import PatientDisplay
from rehab_app.ui.settings_dialog import camera_source_from_settings
from rehab_app.ui.theme import STYLESHEET
from rehab_app.workers.engine_worker import EngineWorker


def parse_args(argv):
    ap = argparse.ArgumentParser(description=config.APP_NAME)
    ap.add_argument("--engine", choices=["mediapipe", "mock"], default=None,
                    help="tracking engine (default: saved setting, else mediapipe)")
    ap.add_argument("--landmarks", choices=list(config.LANDMARK_PRESETS), default=None,
                    help="landmark preset (default: saved setting, else 12)")
    ap.add_argument("--camera", type=int, default=None, help="camera index (default: saved / auto)")
    ap.add_argument("--video", default=None, help="use a video file instead of a camera")
    ap.add_argument("--model", default=None, help="path to hand_landmarker .task file")
    ap.add_argument("--db", default=str(config.DEFAULT_DB_PATH), help="SQLite database path")
    ap.add_argument("--no-patient-view", action="store_true", help="don't open the patient window")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    app = QApplication(sys.argv[:1])
    app.setApplicationName(config.APP_NAME)
    app.setStyleSheet(STYLESHEET)

    db = Database(args.db)
    preset = args.landmarks or db.get_setting("landmark_preset", config.DEFAULT_LANDMARK_PRESET)
    # "engine_choice" is only written by the Settings dialog. (Older versions auto-saved
    # "engine" on every launch, which left some databases stuck on the mock engine.)
    engine_kind = args.engine or db.get_setting("engine_choice", "mediapipe")
    db.set_setting("landmark_preset", preset)
    landmark_set = LandmarkSet.from_preset(preset)

    try:
        engine = create_engine(engine_kind, landmark_set, args.model)
    except EngineError as e:
        QMessageBox.warning(None, "MediaPipe unavailable",
                            f"{e}\n\nStarting with the simulated (mock) engine instead.")
        engine_kind = "mock"
        engine = create_engine("mock", landmark_set)

    db.real_engine_only = engine_kind == "mediapipe"

    if args.video:
        source = args.video
    elif args.camera is not None:
        source = args.camera
    else:
        source = camera_source_from_settings(db)
    worker = EngineWorker(engine, camera_source=source)

    servo = NullServoLink()
    if db.get_setting("servo_enabled", "0") == "1":
        try:
            servo = SerialServoLink(db.get_setting("servo_port", ""),
                                    int(db.get_setting("servo_baud", "115200")))
        except Exception as e:  # keep running without hardware
            print(f"[glove] link disabled: {e}", file=sys.stderr)

    ctx = AppContext(db=db, engine=engine, engine_kind=engine_kind, worker=worker,
                     landmark_set=landmark_set, servo=servo)
    ctx.patient_display = PatientDisplay(landmark_set)
    ctx.patient_display.camera.mirror = ctx.mirror_preview()
    worker.start()

    win = MainWindow(ctx)
    win.show()
    if not args.no_patient_view and db.get_setting("patient_display", "1") == "1":
        ctx.patient_display.show_on_best_screen()
        win.raise_()
        win.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
