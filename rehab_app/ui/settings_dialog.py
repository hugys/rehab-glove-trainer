"""Settings: engine & landmark set, camera source, video recording, assist tuning, glove link."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout)

from .. import config
from ..config import LANDMARK_PRESETS
from ..hardware.servo_link import NullServoLink, SerialServoLink, list_serial_ports
from .context import AppContext


def _group(title: str, form: QFormLayout) -> QGroupBox:
    g = QGroupBox(title)
    g.setLayout(form)
    return g


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        db = ctx.db

        # --- engine
        self.engine = QComboBox()
        self.engine.addItem("MediaPipe (real camera)", "mediapipe")
        self.engine.addItem("Mock (simulated hand, no camera)", "mock")
        self.engine.setCurrentIndex(self.engine.findData(db.get_setting("engine_choice", ctx.engine_kind)))
        self.preset = QComboBox()
        for key, pts in LANDMARK_PRESETS.items():
            self.preset.addItem(f"{key} landmarks  ({', '.join(str(i) for i, _ in pts)})", key)
        self.preset.setCurrentIndex(self.preset.findData(
            db.get_setting("landmark_preset", ctx.landmark_set.preset)))
        f1 = QFormLayout()
        f1.addRow("Engine", self.engine)
        f1.addRow("Landmark set", self.preset)
        f1.addRow("", QLabel(f"Running now: {ctx.engine_kind}, {ctx.landmark_set.count} points. "
                             "These two apply after restarting the app."))

        # --- camera
        self.camera = QComboBox()
        self.camera.addItem("Auto-detect", "auto")
        for i in range(6):
            self.camera.addItem(f"Camera {i}", str(i))
        self.camera.addItem("Video file…", "file")
        self.video_file = QLineEdit(db.get_setting("camera_file", ""))
        self.video_file.setPlaceholderText("Pre-recorded video used instead of a camera")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        src = db.get_setting("camera_source", "auto")
        self.camera.setCurrentIndex(max(0, self.camera.findData(src)))
        file_row = QHBoxLayout()
        file_row.addWidget(self.video_file, 1)
        file_row.addWidget(browse)
        self.mirror = QCheckBox("Mirror the preview (selfie view)")
        self.mirror.setChecked(ctx.mirror_preview())
        self.record = QCheckBox("Save a video of every reference take and session repetition")
        self.record.setChecked(ctx.record_videos())
        f2 = QFormLayout()
        f2.addRow("Source", self.camera)
        f2.addRow("Video file", file_row)
        f2.addRow("", self.mirror)
        f2.addRow("", self.record)
        f2.addRow("Video folder", QLabel(str(config.VIDEO_DIR)))

        # --- assist tuning
        self.eps = QDoubleSpinBox()
        self.eps.setRange(0.5, 30)
        self.eps.setSingleStep(0.5)
        self.eps.setSuffix(" % of hand size")
        self.eps.setValue(db.get_float("stall_move_eps_pct", config.STALL_MOVE_EPS_PCT))
        self.tol = QDoubleSpinBox()
        self.tol.setRange(1, 40)
        self.tol.setSingleStep(0.5)
        self.tol.setSuffix(" % of hand size")
        self.tol.setValue(db.get_float("complete_tol_pct", config.COMPLETE_TOL_PCT))
        f3 = QFormLayout()
        f3.addRow("“Not moving” below", self.eps)
        f3.addRow("", QLabel("Fingertip movement within the stall time below this counts as "
                             "stopped. Too low and camera jitter prevents stalls registering."))
        f3.addRow("“Complete” within", self.tol)
        f3.addRow("", QLabel("The stall time t itself is set per session on the setup screen."))

        # --- glove
        self.servo_on = QCheckBox("Send motor commands to the glove (ESP32 over USB serial)")
        self.servo_on.setChecked(db.get_setting("servo_enabled", "0") == "1")
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.addItems(list_serial_ports())
        if db.get_setting("servo_port", ""):
            self.port.setCurrentText(db.get_setting("servo_port", ""))
        self.baud = QComboBox()
        self.baud.addItems(["115200", "57600", "9600"])
        self.baud.setCurrentText(db.get_setting("servo_baud", "115200"))
        f4 = QFormLayout()
        f4.addRow("", self.servo_on)
        f4.addRow("Serial port", self.port)
        f4.addRow("Baud rate", self.baud)
        f4.addRow("", QLabel("Protocol: “t,i,m,r,p\\n”, each +1 flex / -1 extend / 0 hold, 20 Hz.\n"
                             f"Current link: {ctx.servo.status()}"))

        # --- display
        self.patient_display = QCheckBox("Open the patient view on start")
        self.patient_display.setChecked(db.get_setting("patient_display", "1") == "1")
        f5 = QFormLayout()
        f5.addRow("", self.patient_display)
        f5.addRow("Database", QLabel(str(db.path)))

        for f in (f1, f2, f3, f4, f5):
            f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            for i in range(f.rowCount()):
                item = f.itemAt(i, QFormLayout.FieldRole)
                if item and isinstance(item.widget(), QLabel):
                    item.widget().setWordWrap(True)
                    item.widget().setObjectName("muted")

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for title, f in (("Tracking engine", f1), ("Camera & video", f2),
                         ("Glove assist tuning", f3), ("Glove connection", f4), ("Other", f5)):
            lay.addWidget(_group(title, f))
        lay.addWidget(bb)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose video", "",
                                              "Video (*.mp4 *.mov *.avi *.m4v)")
        if path:
            self.video_file.setText(path)
            self.camera.setCurrentIndex(self.camera.findData("file"))

    def _save(self):
        db, ctx = self.ctx.db, self.ctx
        src = self.camera.currentData()
        if src == "file" and not self.video_file.text().strip():
            QMessageBox.warning(self, "Video file", "Choose a video file or another source.")
            return
        values = {
            "engine_choice": self.engine.currentData(), "landmark_preset": self.preset.currentData(),
            "camera_source": src, "camera_file": self.video_file.text().strip(),
            "mirror_preview": "1" if self.mirror.isChecked() else "0",
            "record_videos": "1" if self.record.isChecked() else "0",
            "stall_move_eps_pct": f"{self.eps.value():g}", "complete_tol_pct": f"{self.tol.value():g}",
            "servo_enabled": "1" if self.servo_on.isChecked() else "0",
            "servo_port": self.port.currentText(), "servo_baud": self.baud.currentText(),
            "patient_display": "1" if self.patient_display.isChecked() else "0",
        }
        old_source = ctx.worker.camera_source
        for k, v in values.items():
            db.set_setting(k, v)
        ctx.worker.camera_source = camera_source_from_settings(db)
        if ctx.worker.camera_source != old_source:
            ctx.worker.close_camera()

        # glove link: re-open only if something changed
        want = self.servo_on.isChecked()
        port, baud = self.port.currentText(), int(self.baud.currentText())
        same = (want and isinstance(ctx.servo, SerialServoLink) and ctx.servo.connected
                and ctx.servo.port == port) or (not want and isinstance(ctx.servo, NullServoLink))
        if not same:
            ctx.servo.close()
            ctx.servo = NullServoLink()
            if want:
                QGuiApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    ctx.servo = SerialServoLink(port, baud)
                except Exception as e:
                    QMessageBox.warning(self, "Glove link", f"Could not open {port}:\n{e}")
                finally:
                    QGuiApplication.restoreOverrideCursor()
        self.accept()


def camera_source_from_settings(db):
    """None = auto, int index, or video file path."""
    src = db.get_setting("camera_source", "auto")
    if src == "file":
        return db.get_setting("camera_file", "") or None
    if src and src.isdigit():
        return int(src)
    return None
