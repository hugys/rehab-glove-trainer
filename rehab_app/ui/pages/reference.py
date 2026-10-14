"""Guided reference recording of the unaffected hand: 1-5 takes of exactly 4 s each.

Each take is filmed (MP4). Takes are averaged into the reference trajectory; with
several takes the spread between them defines the tolerance band.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QMessageBox, QSpinBox,
                               QVBoxLayout, QWidget)

from ... import config
from ...config import MOVEMENTS, MOVEMENTS_BY_ID, REF_DURATION_S
from ...data.video import video_path
from .. import theme
from ..video_player import play_video
from ..widgets import CameraView, MovementDemo, MovementProgressBar
from .base import Page, button, card, heading, muted


def fingertip_reach_mm(ref, landmark_set, hand_size_mm: float) -> dict[str, np.ndarray]:
    """Distance (mm) from wrist to each finger's most distal tracked point, per progress step."""
    scale = hand_size_mm if ref.units == "hand" else 1.0
    out = {}
    for f, rows in landmark_set.finger_groups().items():
        if rows:
            out[f] = np.linalg.norm(ref.data[:, rows[-1], :], axis=1) * scale
    return out


class ReferencePage(Page):
    title = "Record reference"

    def __init__(self, ctx, navigate):
        super().__init__(ctx, navigate)
        self._pending_ref = None
        self._pending_videos: list[str] = []
        self._warnings: list[str] = []
        self._state = "ready"

        self.movement = QComboBox()
        for m in MOVEMENTS:
            self.movement.addItem(m.name, m.id)
        self.movement.currentIndexChanged.connect(self._movement_changed)
        self.takes = QSpinBox()
        self.takes.setRange(1, 5)
        self.takes.setValue(int(ctx.db.get_setting("ref_takes", str(config.TAKES_DEFAULT))))
        self.takes.setToolTip("Several takes are averaged; their spread sets the tolerance band. "
                              "3 takes recommended.")
        self.takes.valueChanged.connect(lambda _: self._set_state(self._state))
        self.instruction = muted("")
        self.hand_label = QLabel()
        self.demo = MovementDemo()
        self.camera = CameraView(ctx.landmark_set)
        self.take_label = QLabel("")
        self.take_label.setObjectName("h2")
        self.progress = MovementProgressBar()

        self.btn_start = button("▶  Start recording", "primary", self._start)
        self.btn_cancel = button("Cancel", slot=self._cancel)
        self.btn_accept = button("✓ Accept && save", "primary", self._accept)
        self.btn_redo = button("↺ Redo", slot=self._redo)

        pg.setConfigOptions(antialias=True, background="w", foreground=theme.TEXT)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Progress", units="%")
        self.plot.setLabel("left", "Wrist → fingertip (mm)")
        self.plot.addLegend(offset=(-10, 10))
        self.plot.setMinimumHeight(190)
        self.review_info = muted("")
        self.video_row = QHBoxLayout()
        vids = QWidget()
        vids.setLayout(self.video_row)
        self.review = card(self.review_info, self.plot, vids, title="Captured reference")
        self.review.hide()

        takes_row = QHBoxLayout()
        takes_row.addWidget(QLabel("Takes"))
        takes_row.addWidget(self.takes)
        takes_row.addStretch()

        left = QVBoxLayout()
        left.addWidget(heading("Record reference"))
        left.addWidget(QLabel("Movement"))
        left.addWidget(self.movement)
        left.addLayout(takes_row)
        left.addWidget(self.hand_label)
        left.addWidget(self.instruction)
        left.addWidget(card(self.demo, title="Demonstration"), 1)
        left.addWidget(button("← Back to patient", slot=lambda: self.navigate("home")))

        right = QVBoxLayout()
        right.addWidget(self.camera, 3)
        right.addWidget(self.take_label)
        right.addWidget(self.progress)
        btns = QHBoxLayout()
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_cancel)
        btns.addStretch()
        btns.addWidget(self.btn_redo)
        btns.addWidget(self.btn_accept)
        right.addLayout(btns)
        right.addWidget(self.review, 2)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(20)
        lay.addLayout(left, 2)
        lay.addLayout(right, 5)
        self._set_state("ready")

    # ---- state ---------------------------------------------------------------
    def _set_state(self, state: str):
        self._state = state
        self.btn_start.setVisible(state == "ready")
        self.btn_cancel.setVisible(state == "recording")
        self.btn_accept.setVisible(state == "review")
        self.btn_redo.setVisible(state == "review")
        self.movement.setEnabled(state != "recording")
        self.takes.setEnabled(state == "ready")
        self.review.setVisible(state == "review")
        self.btn_start.setText(f"▶  Start recording ({self.takes.value()} × {REF_DURATION_S:.0f} s)")
        if state == "ready":
            self.take_label.setText("")

    def _movement_id(self) -> str:
        return self.movement.currentData()

    def _movement_changed(self):
        if self._state == "review":
            self._discard_pending()
        m = MOVEMENTS_BY_ID[self._movement_id()]
        self.instruction.setText(m.instruction)
        self.demo.set_movement(m.id)
        if self.ctx.patient_display and self.isVisible():
            self.ctx.patient_display.show_movement(m.id, "reference")
        self._set_state("ready")

    def _discard_pending(self):
        for v in self._pending_videos:
            if v:
                Path(v).unlink(missing_ok=True)
        self._pending_ref, self._pending_videos, self._warnings = None, [], []

    # ---- lifecycle -----------------------------------------------------------
    def on_enter(self, movement_id: str | None = None, **kwargs):
        if movement_id:
            self.movement.setCurrentIndex(self.movement.findData(movement_id))
        self.camera.mirror = self.ctx.mirror_preview()
        self.hand_label.setText(f"<b>Use the UNAFFECTED ({self.ctx.healthy_hand()}) hand</b>")
        self._movement_changed()
        if self.ctx.patient_display:
            self.ctx.patient_display.show_movement(self._movement_id(), "reference")
        self.progress.reset()
        self.ctx.worker.open_camera()

    def on_leave(self):
        self.ctx.worker.abort()
        self.ctx.worker.close_camera()
        self.camera.clear()
        self._discard_pending()
        self._set_state("ready")

    def can_leave(self) -> bool:
        if self._state == "recording":
            QMessageBox.information(self, "Recording", "Cancel the recording before leaving.")
            return False
        if self._state == "review":
            return QMessageBox.question(self, "Discard recording?",
                                        "The captured reference has not been saved. Discard it?"
                                        ) == QMessageBox.Yes
        return True

    # ---- actions -------------------------------------------------------------
    def _start(self):
        self._discard_pending()
        self.progress.reset()
        n = self.takes.value()
        self.ctx.db.set_setting("ref_takes", str(n))
        self._set_state("recording")
        self.take_label.setText(f"Get ready - take 1 of {n}")
        if self.ctx.patient_display:
            self.ctx.patient_display.show_movement(self._movement_id(), "reference")
        paths = ([video_path(self.ctx.patient_id, f"ref_{self._movement_id()}_take{i + 1}")
                  for i in range(n)] if self.ctx.record_videos() else [])
        self.ctx.worker.record_reference(self._movement_id(), self.ctx.healthy_hand(),
                                         self.ctx.hand_size_mm(), n, paths)

    def _cancel(self):
        self.ctx.worker.abort()
        self.camera.set_countdown(None)
        self.progress.reset()
        self._pending_videos = []        # the worker deletes unfinished takes' files
        self._set_state("ready")
        if self.ctx.patient_display:
            self.ctx.patient_display.set_status("Recording cancelled")

    def _redo(self):
        self._discard_pending()
        self._start()

    def _accept(self):
        if self._pending_ref is None:
            return
        pid = self.ctx.patient_id
        ref = self._pending_ref
        rid = self.ctx.db.save_reference(pid, ref)
        for i, v in enumerate(self._pending_videos):
            if v:
                self.ctx.db.add_video(pid, "reference", ref.movement_id, v, i + 1,
                                      REF_DURATION_S, reference_id=rid)
        self._pending_ref, self._pending_videos, self._warnings = None, [], []
        self._set_state("ready")
        if self.ctx.patient_display:
            self.ctx.patient_display.set_status("Saved - well done!", theme.OK)
        status = self.ctx.db.reference_status(pid, self.ctx.landmark_set)
        nxt = next((m for m in MOVEMENTS if m.id not in status), None)
        msg = f"Reference for “{MOVEMENTS_BY_ID[ref.movement_id].name}” saved."
        if nxt:
            QMessageBox.information(self, "Reference saved", f"{msg}\nNext: {nxt.name}.")
            self.movement.setCurrentIndex(self.movement.findData(nxt.id))
        else:
            QMessageBox.information(self, "Reference saved", f"{msg}\nAll movements have a reference.")
            self.navigate("home")

    # ---- worker hooks --------------------------------------------------------
    def on_phase(self, phase):
        self.camera.set_banner("Opening camera…" if phase == "opening" else None)

    def on_frame(self, frame, phase, elapsed, duration):
        self.camera.set_frame(frame, recording=phase == "reference")
        if phase == "reference":
            self.progress.set_progress(frame.progress, elapsed, duration)

    def on_countdown(self, n):
        self.camera.set_countdown(n if n > 0 else None)

    def on_take_started(self, take, total):
        self.take_label.setText(f"Recording take {take} of {total}")
        self.progress.reset()

    def on_take_done(self, take, total, info, video):
        self._pending_videos.append(video)
        if info.get("warning"):
            self._warnings.append(f"Take {take}: {info['warning']}")
        if take < total:
            self.take_label.setText(f"Take {take} done ({info['frames']} frames) - get ready for "
                                    f"take {take + 1} of {total}")

    def on_reference_done(self, ref, videos):
        self._pending_ref = ref
        self._pending_videos = list(videos)
        self.progress.set_progress(100, REF_DURATION_S, REF_DURATION_S)
        hand = self.ctx.hand_size_mm()
        self.plot.clear()
        for f, reach in fingertip_reach_mm(ref, self.ctx.landmark_set, hand).items():
            self.plot.plot(ref.progress, reach, pen=pg.mkPen(theme.FINGER_COLORS[f], width=2),
                           name=f.capitalize())
        info = [f"{ref.n_takes} take(s) averaged."]
        if ref.rom is not None:
            info.append("Fingertip range of motion: " + ", ".join(
                f"{f} {r * hand:.0f} mm" for f, r in zip(config.FINGERS, ref.rom)))
        if ref.n_takes == 1:
            info.append("Single take: the tolerance band uses its floor value - record 3 takes "
                        "to capture natural variability.")
        info += [f"⚠ {w}" for w in self._warnings]
        self.review_info.setText("\n".join(info))
        while self.video_row.count():
            w = self.video_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, v in enumerate(self._pending_videos):
            if v:
                self.video_row.addWidget(button(f"▶ Video take {i + 1}",
                                                slot=lambda _=False, p=v, n=i + 1:
                                                play_video(p, f"Reference take {n}", self)))
        self.video_row.addStretch()
        self._set_state("review")
        self.take_label.setText(f"All {ref.n_takes} take(s) recorded - review and save")
        if self.ctx.patient_display:
            self.ctx.patient_display.set_status("Recording complete", theme.OK)

    def on_error(self, msg):
        if self._state == "recording":
            self._pending_videos = []     # worker already removed this recording's files
            self._pending_ref, self._warnings = None, []
            self._set_state("ready")
            self.progress.reset()
            self.camera.set_countdown(None)
        self.camera.set_banner(None)
        QMessageBox.critical(self, "Problem", msg)
