"""Per-patient dashboard: reference status per movement and session history."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout)

from pathlib import Path

from ...config import MOVEMENTS, MOVEMENTS_BY_ID
from ...data.database import session_threshold
from ...data.overlay_video import OverlayBuilder, overlay_path
from .. import theme
from ..units import fmt_err
from ..widgets import VideoClip, header_info
from .base import Page, button, heading, muted


class MovementCard(QFrame):
    def __init__(self, movement, on_record, on_session):
        super().__init__()
        self.setObjectName("card")
        self.movement = movement
        self.name = QLabel(movement.name)
        self.name.setObjectName("h2")
        self.status = QLabel()
        # the patient's own recorded reference, with the tracked skeleton drawn on it
        self.clip = VideoClip()
        self.clip.setFixedHeight(170)
        self.clip.placeholder = "No reference video"
        self.btn_ref = button("Record reference", slot=lambda: on_record(movement.id))
        self.btn_sess = button("Start session", "primary", slot=lambda: on_session(movement.id))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(self.name)
        lay.addWidget(self.status)
        lay.addWidget(self.clip)
        lay.addStretch()
        row = QHBoxLayout()
        row.addWidget(self.btn_ref)
        row.addWidget(self.btn_sess)
        lay.addLayout(row)

    def set_reference(self, row):
        if row:
            takes = f" · {row['n_takes']} take{'s' if row['n_takes'] != 1 else ''}"
            self.status.setText(f"✓ Reference {row['created_at'][:16].replace('T', ' ')}{takes}")
            self.status.setStyleSheet(f"color: {theme.OK};")
            self.btn_ref.setText("Re-record reference")
            self.btn_sess.setEnabled(True)
            self.btn_sess.setToolTip("")
        else:
            self.status.setText("✗ No reference yet")
            self.status.setStyleSheet(f"color: {theme.BAD};")
            self.btn_ref.setText("Record reference")
            self.btn_sess.setEnabled(False)
            self.btn_sess.setToolTip("Record a reference with the unaffected hand first")


class PatientHomePage(Page):
    title = "Patient"

    def __init__(self, ctx, navigate):
        super().__init__(ctx, navigate)
        self.header = heading("")
        self.sub = muted("")
        self.sub.setWordWrap(False)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards: dict[str, MovementCard] = {}
        self._clip_source: dict[str, str] = {}       # recorded video -> movement id
        model = getattr(ctx.engine, "_model_path", None)       # None with the simulated hand
        self._overlays = OverlayBuilder(ctx.landmark_set, model, self)
        self._overlays.ready.connect(self._overlay_ready)
        for i, m in enumerate(MOVEMENTS):
            c = MovementCard(m, self._record, self._session)
            self.cards[m.id] = c
            grid.addWidget(c, i // 3, i % 3)

        self.sessions = QTableWidget(0, 6)
        self.sessions.setHorizontalHeaderLabels(
            ["Date", "Movement", "Speed", "Threshold", "Reps", "Mean error"])
        header_info(self.sessions, 2, "Speed", "speed")
        header_info(self.sessions, 3, "Threshold", "threshold")
        header_info(self.sessions, 5, "Mean error", "mean_error")
        self.sessions.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.sessions.verticalHeader().hide()
        self.sessions.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sessions.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sessions.doubleClicked.connect(self._open_results)

        top = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(self.header)
        col.addWidget(self.sub)
        top.addLayout(col)
        top.addStretch()
        top.addWidget(button("View results", slot=self._open_results))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(heading("Movements", 2))
        lay.addWidget(muted("Step 1: record a 4-second reference of each movement with the "
                            "UNAFFECTED hand.  Step 2: run sessions with the affected hand."))
        lay.addLayout(grid)
        lay.addWidget(heading("Session history", 2))
        lay.addWidget(self.sessions, 1)

    def on_enter(self, patient_id: int | None = None, **kwargs):
        if patient_id is not None:
            self.ctx.patient_id = patient_id
        p = self.ctx.patient()
        if not p:
            self.navigate("patients")
            return
        self.header.setText(p["name"])
        code = f"ID {p['patient_code']} · " if p["patient_code"] else ""
        hand = self.ctx.hand_size_mm()
        size = f"{hand:.0f} mm" if hand else "not measured (results in % of hand size)"
        self.sub.setText(f"{code}Affected hand: {p['affected_hand']} · "
                         f"Reference hand: {self.ctx.healthy_hand()} · "
                         f"Hand size: {size} · "
                         f"Landmark set: {self.ctx.landmark_set.count} points")
        if self.ctx.patient_display:
            self.ctx.patient_display.show_idle(p["name"])
        status = self.ctx.db.reference_status(p["id"], self.ctx.landmark_set)
        self._clip_source = {}
        for mid, c in self.cards.items():
            c.set_reference(status.get(mid))
            self._show_clip(mid, status.get(mid))
        rows = self.ctx.db.list_sessions(p["id"])
        self.sessions.setRowCount(len(rows))
        for i, s in enumerate(rows):
            vals = [s["started_at"][:16].replace("T", " "), MOVEMENTS_BY_ID[s["movement_id"]].name,
                    f"{s['speed_pct']:.0f}%",
                    fmt_err(session_threshold(s), s["hand_size_mm"], s["err_units"]),
                    f"{s['reps_done']}/{s['reps']}",
                    fmt_err(s["mean_err"], s["hand_size_mm"], s["err_units"])]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, s["id"])
                self.sessions.setItem(i, j, item)

    def _show_clip(self, movement_id: str, row):
        """Loop take 1 of the movement's reference on its card. The copy with the skeleton
        is made in the background the first time; the plain video plays until it is ready."""
        clip = self.cards[movement_id].clip
        videos = self.ctx.db.videos_for_reference(row["id"]) if row else []
        video = next((v for v in videos if Path(v["path"]).exists()), None)
        if video is None:
            clip.set_video(None)
            return
        hand = f" · {video['hand']} hand" if video["hand"] else ""
        caption = f"Reference take {video['take_or_rep']}{hand}"
        overlay = overlay_path(video["path"])
        if overlay.exists():
            clip.set_video(overlay, caption)
        else:
            clip.set_video(video["path"], caption)
            self._clip_source[video["path"]] = movement_id
            self._overlays.request(video["path"])

    def _overlay_ready(self, video: str, overlay: str):
        movement_id = self._clip_source.pop(video, None)
        if movement_id:
            clip = self.cards[movement_id].clip
            clip.set_video(overlay, clip.caption)

    def _record(self, movement_id):
        self.navigate("reference", movement_id=movement_id)

    def _session(self, movement_id):
        self.navigate("setup", movement_id=movement_id)

    def _open_results(self, *_):
        items = self.sessions.selectedItems()
        sid = items[0].data(Qt.UserRole) if items else None
        self.navigate("results", session_id=sid)
