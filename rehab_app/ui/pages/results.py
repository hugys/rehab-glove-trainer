"""Session results: per-finger error vs progress, trend across sessions, CSV export."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QMessageBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...config import FINGERS, MOVEMENTS_BY_ID
from ...data import export
from .. import theme
from ..video_player import play_video
from .base import Page, button, card, heading, muted


class ResultsPage(Page):
    title = "Results"

    def __init__(self, ctx, navigate):
        super().__init__(ctx, navigate)
        self.session_box = QComboBox()
        self.session_box.currentIndexChanged.connect(self._show_session)
        self.summary = muted("")

        pg.setConfigOptions(antialias=True, background="w", foreground=theme.TEXT)
        self.curve_plot = pg.PlotWidget()
        self.curve_plot.setLabel("bottom", "Movement progress", units="%")
        self.curve_plot.setLabel("left", "Error", units="mm")
        self.curve_plot.addLegend(offset=(-10, 10))
        self.curve_plot.showGrid(y=True, alpha=0.2)

        self.trend_plot = pg.PlotWidget()
        self.trend_plot.setLabel("bottom", "Session # (this movement)")
        self.trend_plot.setLabel("left", "Mean error", units="mm")
        self.trend_plot.addLegend(offset=(-10, 10))
        self.trend_plot.showGrid(y=True, alpha=0.2)

        self.table = QTableWidget(len(FINGERS), 4)
        self.table.setHorizontalHeaderLabels(["Finger", "Mean error", "Time above threshold",
                                              "Time glove assisting"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMaximumHeight(200)
        self.table.verticalHeader().setDefaultSectionSize(28)

        top = QHBoxLayout()
        top.addWidget(heading("Results"))
        top.addStretch()
        top.addWidget(button("Export session CSV", slot=self._export_session))
        top.addWidget(button("Export reference CSV", slot=self._export_reference))
        top.addWidget(button("Export all patient data", slot=self._export_patient))

        sel = QHBoxLayout()
        sel.addWidget(self.session_box, 1)
        sel.addWidget(button("← Back to patient", slot=lambda: self.navigate("home")))

        plots = QHBoxLayout()
        plots.addWidget(card(self.curve_plot, muted("Mean across repetitions. Dashed line = threshold."),
                             title="Error through the movement"), 1)
        plots.addWidget(card(self.trend_plot, title="Progress across sessions"), 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addLayout(top)
        lay.addLayout(sel)
        lay.addWidget(self.summary)
        lay.addLayout(plots, 1)
        self.video_row = QHBoxLayout()
        vids = QWidget()
        vids.setLayout(self.video_row)
        bottom = QHBoxLayout()
        bottom.addWidget(card(self.table, title="Per-finger summary"), 3)
        bottom.addWidget(card(vids, title="Videos"), 2)
        lay.addLayout(bottom)

    def on_enter(self, session_id: int | None = None, **kwargs):
        sessions = self.ctx.db.list_sessions(self.ctx.patient_id)
        self.session_box.blockSignals(True)
        self.session_box.clear()
        for s in sessions:
            self.session_box.addItem(
                f"{s['started_at'][:16].replace('T', ' ')} · {MOVEMENTS_BY_ID[s['movement_id']].name}"
                f" · {s['speed_pct']:.0f}% · {s['reps_done']}/{s['reps']} reps", s["id"])
        self.session_box.blockSignals(False)
        if session_id is not None:
            idx = self.session_box.findData(session_id)
            self.session_box.setCurrentIndex(max(idx, 0))
        self._show_session()

    def _sid(self):
        return self.session_box.currentData()

    def _show_session(self):
        self.curve_plot.clear()
        self.trend_plot.clear()
        sid = self._sid()
        self._fill_videos(sid)
        if sid is None:
            self.summary.setText("No sessions recorded yet.")
            self.table.clearContents()
            return
        s = self.ctx.db.get_session(sid)
        frames = self.ctx.db.session_frames(sid)
        thr = s["threshold_mm"]

        # --- error vs progress, averaged across reps on a 0-100 grid
        grid = np.linspace(0, 100, 101)
        for f in FINGERS:
            curves = []
            for rep in sorted({r["rep"] for r in frames}):
                pts = [(r["progress"], r[f"err_{f}"]) for r in frames
                       if r["rep"] == rep and r[f"err_{f}"] is not None]
                if len(pts) > 1:
                    x, y = np.array(pts).T
                    curves.append(np.interp(grid, x, y))
            if curves:
                self.curve_plot.plot(grid, np.mean(curves, axis=0),
                                     pen=pg.mkPen(theme.FINGER_COLORS[f], width=2),
                                     name=f.capitalize())
        self.curve_plot.addLine(y=thr, pen=pg.mkPen(theme.TEXT, width=1, style=Qt.DashLine))

        # --- trend across sessions of the same movement
        same = [x for x in reversed(self.ctx.db.list_sessions(self.ctx.patient_id))
                if x["movement_id"] == s["movement_id"] and x["reps_done"]]
        summaries = [self.ctx.db.session_summary(x["id"]) for x in same]
        xs = np.arange(1, len(same) + 1)
        for f in FINGERS:
            ys = [sm["mean"][f] if sm["mean"][f] is not None else np.nan for sm in summaries]
            if ys:
                self.trend_plot.plot(xs, ys, pen=pg.mkPen(theme.FINGER_COLORS[f], width=2),
                                     symbol="o", symbolSize=7,
                                     symbolBrush=theme.FINGER_COLORS[f], name=f.capitalize())
        self.trend_plot.getAxis("bottom").setTickSpacing(major=max(1, len(same) // 10 or 1), minor=1)
        self.trend_plot.setXRange(0.5, max(len(same), 1) + 0.5, padding=0)
        if sid in [x["id"] for x in same]:
            i = [x["id"] for x in same].index(sid) + 1
            self.trend_plot.addLine(x=i, pen=pg.mkPen(theme.MUTED, width=1, style=Qt.DotLine))

        # --- table
        summ = self.ctx.db.session_summary(sid)
        for i, f in enumerate(FINGERS):
            m = summ["mean"][f]
            vals = [f.capitalize(), f"{m:.1f} mm" if m is not None else "--",
                    f"{summ['pct_over'][f]:.0f}%", f"{summ['pct_assist'][f]:.0f}%"]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        overall = f"{summ['overall']:.1f} mm" if summ["overall"] is not None else "--"
        self.summary.setText(
            f"Speed {s['speed_pct']:.0f}% · threshold {thr:.1f} mm · landmarks {s['landmark_preset']}"
            + (f" · stall time {s['stall_window_s']:.1f} s" if s["stall_window_s"] else "")
            + f" · {summ['n_frames']} tracked frames · overall mean error {overall}"
            + (f" · notes: {s['notes']}" if s["notes"] else ""))

    def _fill_videos(self, sid):
        while self.video_row.count():
            w = self.video_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        rows = self.ctx.db.videos_for_session(sid) if sid is not None else []
        s = self.ctx.db.get_session(sid) if sid is not None else None
        if s is not None and s["reference_id"]:
            rows = list(self.ctx.db.videos_for_reference(s["reference_id"])) + list(rows)
        if not rows:
            self.video_row.addWidget(muted("No videos for this session."))
        for v in rows:
            label = (f"▶ Ref take {v['take_or_rep']}" if v["kind"] == "reference"
                     else f"▶ Rep {v['take_or_rep']}")
            self.video_row.addWidget(button(label, slot=lambda _=False, p=v["path"], t=label:
                                            play_video(p, t[2:], self)))
        self.video_row.addStretch()

    # ---- export --------------------------------------------------------------
    def _export_session(self):
        sid = self._sid()
        if sid is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export session", f"session_{sid}.csv", "CSV (*.csv)")
        if path:
            export.export_session(self.ctx.db, sid, path)
            QMessageBox.information(self, "Exported", f"Saved {path}")

    def _export_reference(self):
        sid = self._sid()
        s = self.ctx.db.get_session(sid) if sid else None
        if not s or not s["reference_id"]:
            QMessageBox.information(self, "No reference", "This session has no stored reference.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export reference",
                                              f"reference_{s['reference_id']}.csv", "CSV (*.csv)")
        if path:
            export.export_reference(self.ctx.db, s["reference_id"], path)
            QMessageBox.information(self, "Exported", f"Saved {path}")

    def _export_patient(self):
        folder = QFileDialog.getExistingDirectory(self, "Export all data to folder")
        if folder:
            files = export.export_patient(self.ctx.db, self.ctx.patient_id, folder)
            QMessageBox.information(self, "Exported", f"Wrote {len(files)} files to {folder}")
