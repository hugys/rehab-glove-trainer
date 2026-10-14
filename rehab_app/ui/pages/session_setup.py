"""Clinician chooses movement, speed, error threshold, reps and the glove-assist timing."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QSlider, QSpinBox, QVBoxLayout)

from ... import config
from ...config import MOVEMENTS
from ...engine.interface import SessionParams
from ..widgets import MovementDemo
from .base import Page, button, card, heading, muted


def _spin(lo, hi, step, value, suffix, decimals=1) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setValue(value)
    return s


class SessionSetupPage(Page):
    title = "Session setup"

    def __init__(self, ctx, navigate):
        super().__init__(ctx, navigate)
        db = ctx.db
        self._status = {}
        self.movement = QComboBox()
        self.movement.currentIndexChanged.connect(self._movement_changed)
        self.ref_info = muted("")

        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(config.SPEED_MIN_PCT, config.SPEED_MAX_PCT)
        self.speed.setSingleStep(5)
        self.speed.setPageStep(25)
        self.speed.setTickInterval(25)
        self.speed.setTickPosition(QSlider.TicksBelow)
        self.speed.valueChanged.connect(self._speed_changed)
        self.speed_label = QLabel()
        self.speed_label.setObjectName("h2")
        self.duration_label = muted("")

        self.threshold = _spin(1, 50, 0.5, db.get_float("last_threshold_mm", config.THRESHOLD_DEFAULT_MM), " mm")
        self.reps = QSpinBox()
        self.reps.setRange(1, 50)
        self.reps.setValue(int(db.get_float("last_reps", config.REPS_DEFAULT)))

        # --- glove assist
        self.stall_time = _spin(config.STALL_TIME_MIN_S, config.STALL_TIME_MAX_S, 0.1,
                                db.get_float("last_stall_s", config.STALL_TIME_DEFAULT_S), " s")
        self.stall_time.setToolTip("If a finger stops moving for this long before reaching the end "
                                   "position, the glove motor for that finger takes over.")
        self.grace = _spin(0, 10, 0.5, db.get_float("last_grace_s", config.START_GRACE_DEFAULT_S), " s")
        self.grace.setToolTip("No stall is judged during the first seconds of each repetition, so "
                              "a slow start is not mistaken for a stop. 0 = off.")
        self.assist_info = muted("")

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Session notes (optional)")
        self.notes.setFixedHeight(70)

        self.demo = MovementDemo()
        self.btn_start = button("▶  Start session", "primary", self._start)

        speed_row = QHBoxLayout()
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(self.speed_label)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.addRow("Movement", self.movement)
        form.addRow("", self.ref_info)
        form.addRow("Speed", speed_row)
        form.addRow("", self.duration_label)
        form.addRow("Repetitions", self.reps)
        form.addRow("Error threshold", self.threshold)
        form.addRow("", muted("Diagnostic: per-finger distance from the reference that is "
                              "flagged red and counted in the results."))

        assist_form = QFormLayout()
        assist_form.setVerticalSpacing(12)
        assist_form.addRow("Stall time (t)", self.stall_time)
        assist_form.addRow("Start-up grace", self.grace)
        assist_form.addRow("", self.assist_info)

        settings = card(title="Session parameters")
        settings.layout().addLayout(form)
        assist = card(title="Glove assist")
        assist.layout().addWidget(muted(
            "When a finger stops moving for the stall time before reaching the end position, "
            "the glove's motor for that finger drives it the rest of the way."))
        assist.layout().addLayout(assist_form)

        foot = QHBoxLayout()
        foot.addWidget(button("← Back to patient", slot=lambda: self.navigate("home")))
        foot.addStretch()
        foot.addWidget(self.btn_start)

        left = QVBoxLayout()
        left.addWidget(settings)
        left.addWidget(assist)
        left.addWidget(card(self.notes, title="Notes"))
        left.addStretch()
        left.addLayout(foot)

        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(card(self.demo, title="Movement"), 2)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Session setup"))
        lay.addLayout(body, 1)
        self.speed.setValue(int(db.get_float("last_speed_pct", config.SPEED_DEFAULT_PCT)))
        self._speed_changed()

    def on_enter(self, movement_id: str | None = None, **kwargs):
        self._status = self.ctx.db.reference_status(self.ctx.patient_id, self.ctx.landmark_set)
        self.movement.blockSignals(True)
        self.movement.clear()
        for m in MOVEMENTS:
            label = m.name if m.id in self._status else f"{m.name}  (no reference)"
            self.movement.addItem(label, m.id)
            self.movement.model().item(self.movement.count() - 1).setEnabled(m.id in self._status)
        self.movement.blockSignals(False)
        target = movement_id if movement_id in self._status else next(
            (m.id for m in MOVEMENTS if m.id in self._status), None)
        if target:
            self.movement.setCurrentIndex(self.movement.findData(target))
        self._movement_changed()
        eps = self.ctx.db.get_float("stall_move_eps_pct", config.STALL_MOVE_EPS_PCT)
        tol = self.ctx.db.get_float("complete_tol_pct", config.COMPLETE_TOL_PCT)
        hand = self.ctx.hand_size_mm()
        self.assist_info.setText(
            f"“Stopped” = fingertip moves less than {eps:g}% of hand size (≈{eps * hand / 100:.1f} mm) "
            f"within t. “Complete” = within {tol:g}% (≈{tol * hand / 100:.1f} mm) of the end "
            f"position. Glove output: {self.ctx.servo.status()}. (Adjust in Settings.)")

    def _movement_changed(self):
        mid = self.movement.currentData()
        row = self._status.get(mid)
        self.btn_start.setEnabled(row is not None)
        self.demo.set_movement(mid)
        if row is not None:
            self.ref_info.setText(f"Using reference from {row['created_at'][:16].replace('T', ' ')}"
                                  f" ({row['n_takes']} take{'s' if row['n_takes'] != 1 else ''})")
        else:
            self.ref_info.setText("Record a reference for this movement first.")

    def _speed_changed(self):
        v = self.speed.value()
        self.speed_label.setText(f"{v}%")
        d = config.session_duration_s(v)
        self.duration_label.setText(
            f"Movement duration: {d:.1f} s  (reference {config.REF_DURATION_S:.0f} s at 100%)")

    def _start(self):
        mid = self.movement.currentData()
        ref = self.ctx.db.latest_reference(self.ctx.patient_id, mid, self.ctx.landmark_set)
        if ref is None:
            return
        db = self.ctx.db
        params = SessionParams(
            speed_pct=float(self.speed.value()), threshold_mm=self.threshold.value(),
            hand_size_mm=self.ctx.hand_size_mm(), stall_window_s=self.stall_time.value(),
            stall_move_eps_pct=db.get_float("stall_move_eps_pct", config.STALL_MOVE_EPS_PCT),
            complete_tol_pct=db.get_float("complete_tol_pct", config.COMPLETE_TOL_PCT),
            start_grace_s=self.grace.value())
        for k, v in (("last_speed_pct", params.speed_pct), ("last_threshold_mm", params.threshold_mm),
                     ("last_reps", self.reps.value()), ("last_stall_s", params.stall_window_s),
                     ("last_grace_s", params.start_grace_s)):
            db.set_setting(k, str(v))
        self.navigate("live", reference=ref, params=params, reps=self.reps.value(),
                      notes=self.notes.toPlainText().strip())
        self.notes.clear()
