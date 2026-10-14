"""Clinician main window: top bar navigation + stacked pages."""
from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..config import APP_NAME
from ..data import export
from . import theme
from .context import AppContext
from .pages.base import button
from .pages.live_session import LiveSessionPage
from .pages.patient_home import PatientHomePage
from .pages.patients import PatientsPage
from .pages.reference import ReferencePage
from .pages.results import ResultsPage
from .pages.session_setup import SessionSetupPage
from .settings_dialog import SettingsDialog

NEEDS_PATIENT = {"home", "reference", "setup", "live", "results"}


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} - Clinician")
        self.resize(1400, 900)

        self.pages = {
            "patients": PatientsPage(ctx, self.navigate),
            "home": PatientHomePage(ctx, self.navigate),
            "reference": ReferencePage(ctx, self.navigate),
            "setup": SessionSetupPage(ctx, self.navigate),
            "live": LiveSessionPage(ctx, self.navigate),
            "results": ResultsPage(ctx, self.navigate),
        }
        self.stack = QStackedWidget()
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.current = None

        # ---- top bar
        bar = QFrame()
        bar.setObjectName("topbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 10, 20, 10)
        title = QLabel(APP_NAME)
        title.setObjectName("h2")
        bl.addWidget(title)
        bl.addSpacing(24)
        self.nav = {}
        for key, label in (("patients", "Patients"), ("home", "Patient"), ("results", "Results")):
            b = button(label, "nav", slot=lambda _=False, k=key: self.navigate(k))
            b.setCheckable(True)
            self.nav[key] = b
            bl.addWidget(b)
        bl.addStretch()
        self.patient_label = QLabel("")
        self.patient_label.setObjectName("muted")
        bl.addWidget(self.patient_label)
        bl.addSpacing(16)
        label = "MEDIAPIPE" if ctx.engine_kind == "mediapipe" else "MOCK (SIMULATED)"
        badge = QLabel(f" {label} · {ctx.landmark_set.count} pts ")
        color = theme.WARN if ctx.engine_kind == "mock" else theme.OK
        badge.setStyleSheet(f"background: {color}; color: white; border-radius: 4px; "
                            "padding: 3px 6px; font-weight: 600; font-size: 12px;")
        bl.addWidget(badge)
        bl.addWidget(button("Patient view", slot=self.show_patient_display))
        bl.addWidget(button("Settings", slot=self.open_settings))

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(bar)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._build_menu()

        # ---- worker -> page dispatch
        w = ctx.worker
        w.frame_ready.connect(self._on_frame)
        w.countdown.connect(self._on_countdown)
        w.phase_changed.connect(lambda ph: self._page_call("on_phase", ph))
        w.take_started.connect(lambda t, n: self._page_call("on_take_started", t, n))
        w.take_done.connect(lambda t, n, info, v: self._page_call("on_take_done", t, n, info, v))
        w.reference_done.connect(lambda r, v: self._page_call("on_reference_done", r, v))
        w.rep_done.connect(lambda v, d: self._page_call("on_rep_done", v, d))
        w.error.connect(lambda m: self._page_call("on_error", m))

        self.navigate("patients")

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("Export current patient data…", self)
        a.triggered.connect(self._export_patient)
        m.addAction(a)
        a = QAction("Settings…", self)
        a.setShortcut(QKeySequence.Preferences)
        a.triggered.connect(self.open_settings)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        v = self.menuBar().addMenu("&View")
        a = QAction("Show patient view", self)
        a.triggered.connect(self.show_patient_display)
        v.addAction(a)
        a = QAction("Toggle patient view full screen", self)
        a.setShortcut("F11")
        a.triggered.connect(lambda: self.ctx.patient_display and
                            self.ctx.patient_display.toggle_fullscreen())
        v.addAction(a)

    # ---- navigation ------------------------------------------------------------
    def navigate(self, name: str, **kwargs):
        if name in NEEDS_PATIENT and not self.ctx.patient_id and "patient_id" not in kwargs:
            QMessageBox.information(self, "Select a patient", "Please select a patient first.")
            name, kwargs = "patients", {}
        if self.current is not None and self.current is not self.pages[name]:
            if not self.current.can_leave():
                self._sync_nav()
                return
            self.current.on_leave()
        self.current = self.pages[name]
        self.stack.setCurrentWidget(self.current)
        self.current.on_enter(**kwargs)
        self._sync_nav()

    def _sync_nav(self):
        key = next(k for k, p in self.pages.items() if p is self.current)
        for k, b in self.nav.items():
            b.setChecked(k == key or (k == "home" and key in ("reference", "setup", "live")))
        p = self.ctx.patient()
        self.patient_label.setText(f"Patient: {p['name']}" if p else "No patient selected")

    def _page_call(self, method: str, *args):
        if self.current is not None:
            getattr(self.current, method)(*args)

    def _on_frame(self, frame, phase, elapsed, duration):
        self._page_call("on_frame", frame, phase, elapsed, duration)
        if self.ctx.patient_display and self.ctx.patient_display.isVisible():
            self.ctx.patient_display.update_frame(frame, phase, elapsed, duration)

    def _on_countdown(self, n):
        self._page_call("on_countdown", n)
        if self.ctx.patient_display:
            self.ctx.patient_display.set_countdown(n)

    # ---- actions ---------------------------------------------------------------
    def show_patient_display(self):
        if self.ctx.patient_display:
            self.ctx.patient_display.show_on_best_screen()

    def open_settings(self):
        if self.current is not None and not self.current.can_leave():
            return
        if SettingsDialog(self.ctx, self).exec():
            self.pages["live"].servo_status.setText(f"Glove link: {self.ctx.servo.status()}")
            if self.ctx.patient_display:
                self.ctx.patient_display.camera.mirror = self.ctx.mirror_preview()
            # re-enter the page so camera changes take effect
            if self.current in (self.pages["reference"], self.pages["live"]):
                self.ctx.worker.open_camera()

    def _export_patient(self):
        if not self.ctx.patient_id:
            QMessageBox.information(self, "Select a patient", "Please select a patient first.")
            return
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Export all data to folder")
        if folder:
            files = export.export_patient(self.ctx.db, self.ctx.patient_id, folder)
            QMessageBox.information(self, "Exported", f"Wrote {len(files)} files to {folder}")

    def closeEvent(self, e):
        if self.current is not None and not self.current.can_leave():
            e.ignore()
            return
        if self.current is not None:
            self.current.on_leave()
        self.ctx.worker.stop()
        self.ctx.servo.close()
        if self.ctx.patient_display:
            self.ctx.patient_display.close()
        self.ctx.db.close()
        e.accept()
