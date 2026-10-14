"""Real-time session: live camera, per-finger error, stall-triggered glove assist, video per rep."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QVBoxLayout)

from ... import config
from ...config import FINGERS, MOVEMENTS_BY_ID
from ...data.database import corr_mask_from
from ...data.video import video_path
from ...hardware.servo_link import format_line
from .. import theme
from ..video_player import play_video
from ..widgets import CameraView, FingerErrorPanel, HandComparisonView, MovementProgressBar
from .base import Page, button, card, heading, muted


def _stat(label: str) -> tuple[QLabel, QVBoxLayout]:
    val = QLabel("--")
    val.setObjectName("h2")
    lay = QVBoxLayout()
    lay.setSpacing(0)
    lay.addWidget(muted(label))
    lay.addWidget(val)
    return val, lay


class LiveSessionPage(Page):
    title = "Live session"

    def __init__(self, ctx, navigate):
        super().__init__(ctx, navigate)
        self.session_id: int | None = None
        self.reference = None
        self.params = None
        self.reps = 0
        self.notes = ""
        self.rep = 0
        self._running = False
        self._buffer: list[tuple] = []
        self._rep_err_sum = 0.0
        self._rep_err_n = 0
        self._rep_assisted: set[str] = set()
        self._prev_states: dict[str, str] = {}

        self.title_lbl = heading("")
        self.subtitle = muted("")
        self.camera = CameraView(ctx.landmark_set)
        self.hand = HandComparisonView(ctx.landmark_set)
        self.fingers = FingerErrorPanel()
        self.progress = MovementProgressBar()

        self.v_rep, l_rep = _stat("Repetition")
        self.v_err, l_err = _stat("Mean error")
        self.v_conf, l_conf = _stat("Tracking confidence")
        self.v_servo, l_servo = _stat("Glove command")
        self.servo_status = muted("")
        stats = QGridLayout()
        for i, l in enumerate((l_rep, l_err, l_conf, l_servo)):
            stats.addLayout(l, 0, i)

        self.events = QListWidget()
        self.events.setMaximumHeight(140)
        self.auto_next = QCheckBox("Automatically start next repetition")
        self.auto_next.setChecked(True)

        self.btn_rep = button("▶  Start repetition", "primary", self._start_rep)
        self.btn_abort = button("■  Abort repetition", slot=self._abort_rep)
        self.btn_finish = button("Finish session", slot=self._finish)

        left = QVBoxLayout()
        left.addWidget(self.camera, 3)
        left.addWidget(self.progress)
        ctrl = QHBoxLayout()
        ctrl.addWidget(self.btn_rep)
        ctrl.addWidget(self.btn_abort)
        ctrl.addWidget(self.auto_next)
        ctrl.addStretch()
        ctrl.addWidget(self.btn_finish)
        left.addLayout(ctrl)

        right = QVBoxLayout()
        stats_card = card(title="Live")
        stats_card.layout().addLayout(stats)
        stats_card.layout().addWidget(self.servo_status)
        right.addWidget(stats_card)
        legend = muted(f"Bars = distance from reference (diagnostic) · "
                       f"<span style='color:{theme.ASSIST}'><b>ASSIST</b></span> = finger stalled, "
                       f"glove driving it")
        legend.setWordWrap(False)
        right.addWidget(card(self.fingers, legend, title="Per finger"), 3)
        right.addWidget(card(self.hand), 2)
        right.addWidget(card(self.events, title="Repetitions & events (double-click ▶ to play video)"))
        self.events.itemDoubleClicked.connect(self._play_item)

        top = QVBoxLayout()
        top.addWidget(self.title_lbl)
        top.addWidget(self.subtitle)
        body = QHBoxLayout()
        body.setSpacing(18)
        body.addLayout(left, 5)
        body.addLayout(right, 3)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addLayout(top)
        lay.addLayout(body, 1)

        self._next_timer = QTimer(self)
        self._next_timer.setSingleShot(True)
        self._next_timer.timeout.connect(self._start_rep)

    # ---- lifecycle -----------------------------------------------------------
    def on_enter(self, reference=None, params=None, reps=5, notes="", **kwargs):
        if reference is None or params is None:
            self.navigate("home")
            return
        self.reference, self.params, self.reps, self.notes = reference, params, reps, notes
        self.session_id = None
        self.rep = 0
        self._running = False
        self.events.clear()
        m = MOVEMENTS_BY_ID[reference.movement_id]
        dur = config.session_duration_s(params.speed_pct)
        self.title_lbl.setText(f"Session · {m.name}")
        p = self.ctx.patient()
        self.subtitle.setText(
            f"{p['name']} · affected {p['affected_hand']} hand · speed {params.speed_pct:.0f}% "
            f"({dur:.1f} s) · {reps} reps · threshold {params.threshold_mm:.1f} mm · "
            f"assist after {params.stall_window_s:.1f} s stall (grace {params.start_grace_s:.1f} s)")
        self.fingers.set_threshold(params.threshold_mm)
        self.fingers.clear()
        self.hand.set_poses(None, None)
        self.hand.reset_scale()
        self.progress.reset()
        self.camera.mirror = self.ctx.mirror_preview()
        self.v_rep.setText(f"0 / {reps}")
        self.v_err.setText("--")
        self.v_conf.setText("--")
        self.v_servo.setText(format_line([0] * 5).strip())
        self.servo_status.setText(f"Glove link: {self.ctx.servo.status()}")
        self._update_buttons()
        if self.ctx.patient_display:
            self.ctx.patient_display.show_movement(m.id, "session", params.threshold_mm)
        self.ctx.worker.open_camera()

    def on_leave(self):
        self._next_timer.stop()
        self.ctx.worker.abort()
        self.ctx.servo.release_all()
        self.ctx.worker.close_camera()
        self.camera.clear()
        if self.session_id is not None:
            self.ctx.db.end_session(self.session_id)
            self.session_id = None

    def can_leave(self) -> bool:
        if self._running or self._next_timer.isActive():
            QMessageBox.information(self, "Session running",
                                    "Abort the current repetition before leaving this screen.")
            return False
        return True

    def _update_buttons(self):
        done = self.rep >= self.reps
        waiting = self._next_timer.isActive()
        self.btn_rep.setEnabled(not self._running and not done and not waiting)
        self.btn_rep.setText("▶  Start repetition" if self.rep == 0 else "▶  Next repetition")
        self.btn_abort.setEnabled(self._running or waiting)
        self.btn_finish.setEnabled(not self._running)

    def _event(self, text: str, color: str | None = None, video: str | None = None):
        item = QListWidgetItem(text)
        if color:
            item.setForeground(theme.qcolor(color))
        if video:
            item.setData(256, video)
        self.events.insertItem(0, item)

    def _play_item(self, item: QListWidgetItem):
        path = item.data(256)
        if path:
            play_video(path, item.text(), self)

    # ---- actions -------------------------------------------------------------
    def _start_rep(self):
        if self._running or self.rep >= self.reps:
            return
        prm = self.params
        if self.session_id is None:
            self.session_id = self.ctx.db.create_session(
                self.ctx.patient_id, self.reference.movement_id,
                self.reference.meta.get("reference_id"), self.ctx.landmark_set.preset,
                prm.speed_pct, prm.threshold_mm, self.reps, self.notes,
                hand_size_mm=prm.hand_size_mm, stall_window_s=prm.stall_window_s,
                stall_move_eps_pct=prm.stall_move_eps_pct, complete_tol_pct=prm.complete_tol_pct,
                start_grace_s=prm.start_grace_s, servo=self.ctx.servo.status())
        self._running = True
        self._buffer = []
        self._rep_err_sum, self._rep_err_n = 0.0, 0
        self._rep_assisted = set()
        self._prev_states = {}
        self.progress.reset()
        self.progress.set_color(theme.ACCENT)
        self._update_buttons()
        if self.ctx.patient_display:
            self.ctx.patient_display.show_movement(self.reference.movement_id, "session",
                                                   prm.threshold_mm)
        path = (video_path(self.ctx.patient_id, f"session{self.session_id}_rep{self.rep + 1}")
                if self.ctx.record_videos() else None)
        self.ctx.worker.run_repetition(self.reference, prm, path)

    def _abort_rep(self):
        self._next_timer.stop()
        if self._running:
            self.ctx.worker.abort()
            self._event(f"Rep {self.rep + 1} aborted", theme.WARN)
        self._running = False
        self._buffer = []
        self.ctx.servo.release_all()
        self.v_servo.setText(format_line(self.ctx.servo.last_codes).strip())
        self.camera.set_countdown(None)
        self.progress.reset()
        self._update_buttons()
        if self.ctx.patient_display:
            self.ctx.patient_display.set_status("Paused")

    def _finish(self):
        sid = self.session_id
        if sid is not None:
            self.ctx.db.end_session(sid)
            self.session_id = None
        if sid is None or self.rep == 0:
            self.navigate("home")
        else:
            self.navigate("results", session_id=sid)

    # ---- worker hooks --------------------------------------------------------
    def on_phase(self, phase):
        self.camera.set_banner("Opening camera…" if phase == "opening" else None)

    def on_frame(self, frame, phase, elapsed, duration):
        in_session = phase == "session"
        self.camera.set_frame(frame, recording=in_session)
        if frame.confidence:
            self.v_conf.setText(f"{frame.confidence * 100:.0f}%")
        if not in_session or not self._running:
            return
        self.progress.set_progress(frame.progress, elapsed, duration)
        self.hand.set_poses(frame.landmarks, frame.reference_landmarks)
        self.fingers.set_values(frame.finger_error_mm, frame.assist_state)

        # Glove: send the stall-assist command (rate-limited inside the link)
        if self.ctx.servo.send(frame.assist_cmd):
            self.v_servo.setText(format_line(self.ctx.servo.last_codes).strip())

        if not frame.new_measurement:
            return
        if frame.overall_error_mm is not None:
            self.v_err.setText(f"{frame.overall_error_mm:.1f} mm")
            self._rep_err_sum += frame.overall_error_mm
            self._rep_err_n += 1
        states = frame.assist_state or {}
        for f, st in states.items():
            if st != self._prev_states.get(f):
                if st == "assisting":
                    self._rep_assisted.add(f)
                    self._event(f"  Rep {self.rep + 1}: {f} stopped at {frame.progress:.0f}% "
                                f"→ glove assisting", theme.ASSIST)
                elif st == "done" and f in self._rep_assisted:
                    self._event(f"  Rep {self.rep + 1}: {f} reached end position → assist stopped",
                                theme.ACCENT)
        self._prev_states = dict(states)
        errs = frame.finger_error_mm or {}
        cmds = frame.assist_cmd or {}
        self._buffer.append((
            self.rep + 1, elapsed, frame.progress, frame.confidence, int(frame.hand_detected),
            *[errs.get(f) for f in FINGERS], frame.overall_error_mm,
            corr_mask_from(frame.correction),
            *[states.get(f) for f in FINGERS], *[cmds.get(f) for f in FINGERS]))

    def on_countdown(self, n):
        self.camera.set_countdown(n if n > 0 else None)

    def on_rep_done(self, video, duration):
        if not self._running:
            return
        self._running = False
        self.ctx.servo.release_all()
        self.v_servo.setText(format_line(self.ctx.servo.last_codes).strip())
        self.rep += 1
        self.ctx.db.add_frames(self.session_id, self._buffer)
        if video:
            self.ctx.db.add_video(self.ctx.patient_id, "session", self.reference.movement_id,
                                  video, self.rep, duration, session_id=self.session_id)
        self._buffer = []
        mean = self._rep_err_sum / self._rep_err_n if self._rep_err_n else float("nan")
        assisted = ", ".join(sorted(self._rep_assisted, key=FINGERS.index)) or "none"
        self._event(f"{'▶ ' if video else ''}Rep {self.rep}: mean error {mean:.1f} mm · "
                    f"assisted: {assisted}", video=video or None)
        self.v_rep.setText(f"{self.rep} / {self.reps}")
        pd = self.ctx.patient_display
        if self.rep >= self.reps:
            if pd:
                pd.set_status("Session complete - great work!", theme.OK)
            self.ctx.db.end_session(self.session_id)
        else:
            if pd:
                pd.set_status(f"Rep {self.rep} done - relax", theme.OK)
            if self.auto_next.isChecked():
                self._next_timer.start(2000)
        self._update_buttons()

    def on_error(self, msg):
        was_running = self._running
        self._running = False
        self._buffer = []
        self._next_timer.stop()
        self.ctx.servo.release_all()
        self.camera.set_banner(None)
        self.camera.set_countdown(None)
        self._update_buttons()
        if was_running:
            self._event(f"Rep {self.rep + 1} failed: {msg}", theme.BAD)
        QMessageBox.critical(self, "Problem", msg)
