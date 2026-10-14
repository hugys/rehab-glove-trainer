"""Per-patient dashboard: reference status per movement and session history."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ...config import MOVEMENTS, MOVEMENTS_BY_ID
from .. import theme
from .base import Page, button, heading, muted


class MovementCard(QFrame):
    def __init__(self, movement, on_record, on_session):
        super().__init__()
        self.setObjectName("card")
        self.movement = movement
        self.name = QLabel(movement.name)
        self.name.setObjectName("h2")
        self.status = QLabel()
        self.btn_ref = button("Record reference", slot=lambda: on_record(movement.id))
        self.btn_sess = button("Start session", "primary", slot=lambda: on_session(movement.id))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(self.name)
        lay.addWidget(self.status)
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
        for i, m in enumerate(MOVEMENTS):
            c = MovementCard(m, self._record, self._session)
            self.cards[m.id] = c
            grid.addWidget(c, i // 3, i % 3)

        self.sessions = QTableWidget(0, 6)
        self.sessions.setHorizontalHeaderLabels(
            ["Date", "Movement", "Speed", "Threshold", "Reps", "Mean error"])
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
        self.sub.setText(f"{code}Affected hand: {p['affected_hand']} · "
                         f"Reference hand: {self.ctx.healthy_hand()} · "
                         f"Hand size: {self.ctx.hand_size_mm():.0f} mm · "
                         f"Landmark set: {self.ctx.landmark_set.count} points")
        if self.ctx.patient_display:
            self.ctx.patient_display.show_idle(p["name"])
        status = self.ctx.db.reference_status(p["id"], self.ctx.landmark_set)
        for mid, c in self.cards.items():
            c.set_reference(status.get(mid))
        rows = self.ctx.db.list_sessions(p["id"])
        self.sessions.setRowCount(len(rows))
        for i, s in enumerate(rows):
            vals = [s["started_at"][:16].replace("T", " "), MOVEMENTS_BY_ID[s["movement_id"]].name,
                    f"{s['speed_pct']:.0f}%", f"{s['threshold_mm']:.1f} mm",
                    f"{s['reps_done']}/{s['reps']}",
                    f"{s['mean_err']:.1f} mm" if s["mean_err"] is not None else "--"]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, s["id"])
                self.sessions.setItem(i, j, item)

    def _record(self, movement_id):
        self.navigate("reference", movement_id=movement_id)

    def _session(self, movement_id):
        self.navigate("setup", movement_id=movement_id)

    def _open_results(self, *_):
        items = self.sessions.selectedItems()
        sid = items[0].data(Qt.UserRole) if items else None
        self.navigate("results", session_id=sid)
