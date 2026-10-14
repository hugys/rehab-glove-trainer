"""Patient-facing window: big, simple, readable from a distance. Meant for a second monitor."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..config import APP_NAME, MOVEMENTS_BY_ID
from . import theme
from .widgets import CameraView, FingerErrorPanel, MovementDemo, MovementProgressBar


class PatientDisplay(QWidget):
    def __init__(self, landmark_set):
        super().__init__(None, Qt.Window)
        self.setWindowTitle(f"{APP_NAME} - Patient View")
        self.setStyleSheet(f"background: {theme.BG};")
        self.resize(1100, 750)

        self.title = QLabel("Welcome")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 40px; font-weight: 700;")
        self.instruction = QLabel("")
        self.instruction.setAlignment(Qt.AlignCenter)
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet(f"font-size: 22px; color: {theme.MUTED};")

        self.demo = MovementDemo()
        self.camera = CameraView(landmark_set)
        self.camera.placeholder = ""
        mid = QHBoxLayout()
        mid.addWidget(self.demo, 1)
        mid.addWidget(self.camera, 1)

        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self._status_style()
        self.progress = MovementProgressBar(height=64)
        self.fingers = FingerErrorPanel(compact=True)
        self.fingers.setMinimumHeight(140)
        self.fingers.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 30)
        lay.setSpacing(18)
        lay.addWidget(self.title)
        lay.addWidget(self.instruction)
        lay.addLayout(mid, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.progress)
        lay.addWidget(self.fingers)

        self.show_idle()

    # ---- placement -----------------------------------------------------------
    def show_on_best_screen(self):
        screens = QGuiApplication.screens()
        if len(screens) > 1:
            geo = screens[1].availableGeometry()
            self.move(geo.topLeft())
            self.showFullScreen()
        else:
            self.show()
        self.raise_()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_F11):
            self.toggle_fullscreen()
        super().keyPressEvent(e)

    def _status_style(self, color: str | None = None):
        self.status.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {color or theme.TEXT};")

    # ---- states --------------------------------------------------------------
    def show_idle(self, patient_name: str | None = None):
        self.title.setText(f"Hello, {patient_name.split()[0]}" if patient_name else "Welcome")
        self.instruction.setText("Your therapist will start the next exercise shortly.")
        self.demo.set_movement(None)
        self.camera.clear()
        self.status.setText("")
        self._status_style()
        self.progress.reset()
        self.fingers.hide()

    def show_movement(self, movement_id: str, mode: str, threshold: float | None = None):
        """mode: 'reference' (healthy hand) or 'session' (affected hand)."""
        m = MOVEMENTS_BY_ID[movement_id]
        self.title.setText(m.name)
        hand = "unaffected" if mode == "reference" else "affected"
        self.instruction.setText(f"Use your {hand} hand. {m.instruction}")
        self.demo.set_movement(movement_id)
        self.status.setText("Get ready…")
        self._status_style()
        self.progress.reset()
        self.progress.set_color(theme.ACCENT)
        if mode == "session":
            self.fingers.set_threshold(threshold or 10)
            self.fingers.clear()
            self.fingers.show()
        else:
            self.fingers.hide()

    def set_countdown(self, n: int):
        self.camera.set_countdown(n)
        self.status.setText(f"Starting in {n}…" if n > 0 else "Go!")
        if n == 0:
            self.camera.set_countdown(None)

    def update_frame(self, frame, phase: str, elapsed: float, duration: float):
        recording = phase in ("reference", "session")
        self.camera.set_frame(frame, recording=recording)
        if recording:
            self.progress.set_progress(frame.progress, elapsed, duration)
            states = frame.assist_state or {}
            if not frame.hand_detected:
                self.status.setText("Keep your hand in view")
            elif phase == "session" and "assisting" in states.values():
                self.status.setText("The glove is helping - relax and let it guide you")
            else:
                self.status.setText("Keep going…")
            if phase == "session" and frame.finger_error_mm:
                self.fingers.set_values(frame.finger_error_mm, states)

    def set_status(self, text: str, color: str | None = None):
        self.status.setText(text)
        self._status_style(color)
        if color:
            self.progress.set_color(color)
