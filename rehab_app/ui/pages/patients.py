"""Patient list: search, add, edit, delete, open."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox,
                               QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ... import config
from .base import Page, button, heading, muted


class PatientDialog(QDialog):
    def __init__(self, parent=None, patient=None):
        super().__init__(parent)
        self.setWindowTitle("Edit patient" if patient else "New patient")
        self.setMinimumWidth(420)
        self.name = QLineEdit(patient["name"] if patient else "")
        self.code = QLineEdit(patient["patient_code"] or "" if patient else "")
        self.code.setPlaceholderText("Study / MRN identifier (optional)")
        self.dob = QLineEdit(patient["dob"] or "" if patient else "")
        self.dob.setPlaceholderText("YYYY-MM-DD (optional)")
        self.hand = QComboBox()
        self.hand.addItem("Right hand affected", "right")
        self.hand.addItem("Left hand affected", "left")
        if patient:
            self.hand.setCurrentIndex(0 if patient["affected_hand"] == "right" else 1)
        self.hand_size = QDoubleSpinBox()
        self.hand_size.setRange(50, 150)
        self.hand_size.setDecimals(0)
        self.hand_size.setSuffix(" mm")
        self.hand_size.setValue(float(patient["hand_size_mm"] or config.HAND_SIZE_DEFAULT_MM)
                                if patient else config.HAND_SIZE_DEFAULT_MM)
        self.hand_size.setToolTip("Distance from the wrist crease to the middle-finger knuckle "
                                  "(MCP). Tracking works in multiples of this length; it is used "
                                  "to report errors in mm.")
        self.notes = QPlainTextEdit(patient["notes"] or "" if patient else "")
        self.notes.setFixedHeight(90)

        form = QFormLayout()
        form.addRow("Name *", self.name)
        form.addRow("Patient ID", self.code)
        form.addRow("Date of birth", self.dob)
        form.addRow("Affected hand *", self.hand)
        form.addRow("Hand size", self.hand_size)
        hint = QLabel("Wrist crease → middle-finger knuckle. Measure with a ruler.")
        hint.setObjectName("muted")
        form.addRow("", hint)
        form.addRow("Notes", self.notes)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter the patient's name.")
            return
        self.accept()

    def values(self) -> dict:
        return dict(name=self.name.text().strip(), patient_code=self.code.text().strip(),
                    dob=self.dob.text().strip(), affected_hand=self.hand.currentData(),
                    notes=self.notes.toPlainText().strip(),
                    hand_size_mm=self.hand_size.value())


class PatientsPage(Page):
    title = "Patients"

    def __init__(self, ctx, navigate):
        super().__init__(ctx, navigate)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name or ID…")
        self.search.textChanged.connect(self.refresh)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Patient ID", "Affected hand", "Hand size",
                                              "Sessions", "Added"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._open)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.btn_open = button("Open patient", "primary", self._open)
        self.btn_edit = button("Edit", slot=self._edit)
        self.btn_delete = button("Delete", "danger", self._delete)

        top = QHBoxLayout()
        top.addWidget(heading("Patients"))
        top.addStretch()
        top.addWidget(button("+ New patient", "primary", self._add))
        actions = QHBoxLayout()
        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_edit)
        actions.addStretch()
        actions.addWidget(self.btn_delete)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addLayout(top)
        lay.addWidget(muted("Select a patient to record reference movements or run a session."))
        lay.addWidget(self.search)
        lay.addWidget(self.table, 1)
        lay.addLayout(actions)

    def on_enter(self, **kwargs):
        self.refresh()

    def refresh(self):
        rows = self.ctx.db.list_patients(self.search.text().strip())
        self.table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            vals = [p["name"], p["patient_code"] or "", p["affected_hand"].capitalize(),
                    f"{p['hand_size_mm']:.0f} mm", str(p["n_sessions"]), p["created_at"][:10]]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, p["id"])
                self.table.setItem(i, j, item)
        self._update_buttons()

    def _selected_id(self) -> int | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _update_buttons(self):
        has = self._selected_id() is not None
        for b in (self.btn_open, self.btn_edit, self.btn_delete):
            b.setEnabled(has)

    def _add(self):
        dlg = PatientDialog(self)
        if dlg.exec():
            pid = self.ctx.db.add_patient(**dlg.values())
            self.refresh()
            self.navigate("home", patient_id=pid)

    def _edit(self):
        pid = self._selected_id()
        if pid is None:
            return
        dlg = PatientDialog(self, self.ctx.db.get_patient(pid))
        if dlg.exec():
            self.ctx.db.update_patient(pid, **dlg.values())
            self.refresh()

    def _delete(self):
        pid = self._selected_id()
        if pid is None:
            return
        p = self.ctx.db.get_patient(pid)
        if QMessageBox.question(
                self, "Delete patient",
                f"Delete {p['name']} and ALL their references, sessions and videos?\nThis cannot be undone."
        ) == QMessageBox.Yes:
            for path in self.ctx.db.delete_patient(pid):
                Path(path).unlink(missing_ok=True)
            if self.ctx.patient_id == pid:
                self.ctx.patient_id = None
            self.refresh()

    def _open(self, *_):
        pid = self._selected_id()
        if pid is not None:
            self.navigate("home", patient_id=pid)
