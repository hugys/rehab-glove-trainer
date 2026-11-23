"""Patient-facing window: big, simple, readable from a distance. Meant for a second monitor."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import config
from ..config import APP_NAME, MOVEMENTS_BY_ID, session_duration_s
from . import theme
from .hands import hand_phrase, wrong_hand_text
from .widgets import (CameraView, FingerErrorPanel, MovementDemo, MovementProgressBar,
                      ReferenceHandView)


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
        # session: the patient's own recorded healthy-hand reference replaces the demo
        self.ref_hand = ReferenceHandView(landmark_set)
        self.ref_hand.hide()
        self.camera = CameraView(landmark_set)
        self.camera.placeholder = ""
        mid = QHBoxLayout()
        mid.addWidget(self.demo, 1)
        mid.addWidget(self.ref_hand, 1)
        mid.addWidget(self.camera, 1)

        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self._status_style()
        self.progress = MovementProgressBar(height=64)
        self._before_wrong: tuple | None = None   # status to restore after "Wrong hand"
        self.fingers = FingerErrorPanel(compact=True)
        self.fingers.setMinimumHeight(140)
        self.fingers.hide()
        self.legend = QLabel(
            f"<span style='color:{theme.BAD}'>●</span> not there yet &nbsp;&nbsp; "
            f"<span style='color:{theme.WARN}'>●</span> almost &nbsp;&nbsp; "
            f"<span style='color:{theme.OK}'>●</span> movement done &nbsp;&nbsp; "
            f"<span style='color:{theme.ASSIST}'>◯</span> glove helping")
        self.legend.setAlignment(Qt.AlignCenter)
        self.legend.setStyleSheet(f"font-size: 20px; color: {theme.MUTED};")
        self.legend.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 30)
        lay.setSpacing(18)
        lay.addWidget(self.title)
        lay.addWidget(self.instruction)
        lay.addLayout(mid, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.progress)
        lay.addWidget(self.fingers)
        lay.addWidget(self.legend)

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
        self._status_color = color
        self.status.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {color or theme.TEXT};")

    # ---- states --------------------------------------------------------------
    def show_idle(self, patient_name: str | None = None):
        self.title.setText(f"Hello, {patient_name.split()[0]}" if patient_name else "Welcome")
        self.instruction.setText("Your therapist will start the next exercise shortly.")
        self.demo.set_movement(None)
        self.demo.show()
        self.ref_hand.set_reference(None, 4.0)
        self.ref_hand.hide()
        self.camera.clear()
        self.camera.expected_hand = None
        self._before_wrong = None
        self.status.setText("")
        self._status_style()
        self.progress.reset()
        self.fingers.hide()
        self.legend.hide()

    def show_movement(self, movement_id: str, mode: str, threshold: float | None = None,
                      reference=None, speed_pct: float = 100.0, hand: str | None = None):
        """mode: 'reference' (healthy hand) or 'session' (affected hand). `hand` is that
        hand's side ("left" / "right"). In a session, pass the recorded reference so the
        patient follows their own healthy hand."""
        m = MOVEMENTS_BY_ID[movement_id]
        role = "unaffected" if mode == "reference" else "affected"
        self.title.setText(f"{m.name} · {hand} hand" if hand else m.name)
        which = hand_phrase(role, hand) if hand else f"{role} hand"
        self.instruction.setText(f"Use your <b>{which.upper()}</b>. {m.instruction}")
        self.camera.expected_hand = hand
        use_ref = mode == "session" and reference is not None
        self.demo.set_movement(None if use_ref else movement_id)
        self.demo.setVisible(not use_ref)
        self.ref_hand.setVisible(use_ref)
        if use_ref:
            self.ref_hand.mirror = self.camera.mirror
            self.ref_hand.set_reference(reference, session_duration_s(speed_pct))
        self._before_wrong = None
        self.status.setText("Get ready…")
        self._status_style()
        self.progress.reset()
        self.progress.set_color(theme.ACCENT)
        if mode == "session":
            self.fingers.set_threshold(threshold or config.THRESHOLD_DEFAULT_PCT)
            self.fingers.clear()
            self.fingers.show()
        else:
            self.fingers.hide()
        self.legend.setVisible(mode == "session")

    def set_countdown(self, n: int):
        self.camera.set_countdown(n)
        self.status.setText(f"Starting in {n}…" if n > 0 else "Go!")
        if n == 0:
            self.camera.set_countdown(None)

    def update_frame(self, frame, phase: str, elapsed: float, duration: float):
        recording = phase in ("reference", "session")
        self.camera.set_frame(frame, recording=recording)
        wrong = self.camera.wrong_hand()
        if wrong and self._before_wrong is None:
            self._before_wrong = (self.status.text(), self._status_color)
            self.status.setText(wrong_hand_text(self.camera.expected_hand))
            self._status_style(theme.BAD)
        elif not wrong and self._before_wrong is not None:
            text, color = self._before_wrong            # put the previous message back
            self._before_wrong = None
            self.status.setText(text)
            self._status_style(color)
        if recording:
            self.progress.set_progress(frame.progress, elapsed, duration)
            states = frame.assist_state or {}
            if phase == "session":
                self.ref_hand.set_progress(frame.progress,
                                           {f for f, s in states.items() if s == "assisting"})
            if wrong:
                pass                                    # the warning stays up
            elif not frame.hand_detected:
                self.status.setText("Keep your hand in view")
            elif phase == "session" and "assisting" in states.values():
                self.status.setText("Time is up - the glove is finishing the movement"
                                    if frame.finishing else
                                    "The glove is helping - relax and let it guide you")
            else:
                self.status.setText("Keep going…")
            if phase == "session" and frame.finger_error_pct:
                self.fingers.set_values(frame.finger_error_pct, states)

    def set_status(self, text: str, color: str | None = None):
        self.ref_hand.set_progress(None)          # rep over -> back to looping preview
        self._before_wrong = None
        self.status.setText(text)
        self._status_style(color)
        if color:
            self.progress.set_color(color)
