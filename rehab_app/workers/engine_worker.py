"""Background thread that drives the engine at camera rate and emits Qt signals.

All engine calls happen on this thread. The worker owns the clock, so the 4 s
reference takes and the speed-adjusted repetitions are timed with
`time.perf_counter()` independently of UI load. It also writes the MP4 video of
every take / repetition while it is being recorded.
"""
from __future__ import annotations

import queue
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .. import config
from ..data.video import VideoRecorder
from ..engine.interface import EngineError, ReferenceTrajectory, RehabEngine, SessionParams

PHASE_IDLE = "idle"          # camera closed
PHASE_OPENING = "opening"
PHASE_PREVIEW = "preview"
PHASE_COUNTDOWN = "countdown"
PHASE_REFERENCE = "reference"
PHASE_SESSION = "session"


class EngineWorker(QThread):
    frame_ready = Signal(object, str, float, float)   # frame, phase, elapsed_s, duration_s
    countdown = Signal(int)                            # seconds remaining (0 = go)
    phase_changed = Signal(str)
    take_started = Signal(int, int)                    # take index (1-based), total
    take_done = Signal(int, int, object, str)          # take, total, info dict, video path
    reference_done = Signal(object, object)            # ReferenceTrajectory, [video paths]
    rep_done = Signal(str, float)                      # video path ("" if none), duration
    error = Signal(str)

    def __init__(self, engine: RehabEngine, camera_source=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.camera_source = camera_source             # None = auto, int index or video path
        self._cmds: queue.Queue = queue.Queue()
        self._running = True
        self._phase = PHASE_IDLE
        self._recorder: VideoRecorder | None = None

    # ---- public API (UI thread) ----------------------------------------------
    def open_camera(self):
        self._cmds.put(("open",))

    def close_camera(self):
        self._cmds.put(("close",))

    def reopen_camera(self):
        self._cmds.put(("close",))
        self._cmds.put(("open",))

    def record_reference(self, movement_id: str, source_hand: str, hand_size_mm: float,
                         n_takes: int = 1, video_paths: list[Path] | None = None,
                         countdown_s: int = config.COUNTDOWN_S):
        self._cmds.put(("reference", movement_id, source_hand, hand_size_mm, n_takes,
                        video_paths or [], countdown_s))

    def run_repetition(self, reference: ReferenceTrajectory, params: SessionParams,
                       video_path: Path | None = None, countdown_s: int = config.COUNTDOWN_S):
        self._cmds.put(("session", reference, params, video_path, countdown_s))

    def abort(self):
        self._cmds.put(("abort",))

    def stop(self):
        self._running = False
        self._cmds.put(("quit",))
        self.wait(5000)

    # ---- thread body -----------------------------------------------------------
    def _set_phase(self, phase: str):
        if phase != self._phase:
            self._phase = phase
            self.phase_changed.emit(phase)

    def run(self):
        period = 1.0 / config.CAMERA_FPS
        job = None
        while self._running:
            tick = time.perf_counter()
            try:
                while True:
                    try:
                        busy = self._phase != PHASE_IDLE or job
                        cmd = self._cmds.get_nowait() if busy else self._cmds.get(timeout=0.2)
                    except queue.Empty:
                        break
                    job = self._handle(cmd, job)
                    if not self._running:
                        return

                if self._phase == PHASE_PREVIEW:
                    self.frame_ready.emit(self.engine.preview_frame(), PHASE_PREVIEW, 0.0, 0.0)
                elif self._phase == PHASE_COUNTDOWN:
                    remaining = job["countdown_end"] - tick
                    secs = int(remaining) + 1 if remaining > 0 else 0
                    if secs != job["last_count"]:
                        job["last_count"] = secs
                        self.countdown.emit(secs)
                    if remaining <= 0:
                        self._begin_recording(job)
                    else:
                        self.frame_ready.emit(self.engine.preview_frame(), PHASE_COUNTDOWN,
                                              0.0, job["duration"])
                elif self._phase in (PHASE_REFERENCE, PHASE_SESSION):
                    job = self._record_step(job)
            except Exception as e:  # engine failures must not kill the thread
                if not isinstance(e, EngineError):
                    traceback.print_exc()
                self._discard_recorder()
                self.error.emit(str(e) or type(e).__name__)
                job = None
                self._set_phase(PHASE_PREVIEW if self._phase not in (PHASE_IDLE, PHASE_OPENING)
                                else PHASE_IDLE)

            if self._phase not in (PHASE_IDLE, PHASE_OPENING):
                dt = time.perf_counter() - tick
                if dt < period:
                    time.sleep(period - dt)

    def _ensure_open(self):
        if self._phase == PHASE_IDLE:
            self._set_phase(PHASE_OPENING)
            try:
                self.engine.open_camera(self.camera_source)
            except Exception:
                self._set_phase(PHASE_IDLE)
                raise
            self._set_phase(PHASE_PREVIEW)

    def _handle(self, cmd, job):
        kind = cmd[0]
        if kind == "quit":
            self._running = False
            self._discard_recorder()
            for fn in (self.engine.close_camera, self.engine.close):
                try:
                    fn()
                except Exception:
                    pass
            return None
        if kind == "open":
            self._ensure_open()
            return job
        if kind == "close":
            self._discard_recorder()
            if self._phase != PHASE_IDLE:
                self.engine.close_camera()
                self._set_phase(PHASE_IDLE)
            return None
        if kind == "abort":
            self._discard_recorder()
            if job and job["kind"] == "session" and self._phase == PHASE_SESSION:
                self.engine.finish_session()
            if job and job["kind"] == "reference":
                for v in job["videos"]:           # takes already finished in this recording
                    if v:
                        Path(v).unlink(missing_ok=True)
            self._set_phase(PHASE_PREVIEW if self._phase != PHASE_IDLE else PHASE_IDLE)
            return None
        if kind in ("reference", "session"):
            self._ensure_open()
            if kind == "reference":
                _, movement_id, hand, hand_mm, n_takes, paths, cd = cmd
                job = {"kind": "reference", "movement_id": movement_id, "hand": hand,
                       "hand_mm": hand_mm, "takes": max(1, n_takes), "take": 0,
                       "paths": list(paths), "videos": [], "cd": cd,
                       "duration": config.REF_DURATION_S}
            else:
                _, ref, params, path, cd = cmd
                job = {"kind": "session", "ref": ref, "params": params, "path": path, "cd": cd,
                       "duration": config.session_duration_s(params.speed_pct)}
            self._start_countdown(job)
            return job
        return job

    def _start_countdown(self, job):
        job["countdown_end"] = time.perf_counter() + job["cd"]
        job["last_count"] = None
        self._set_phase(PHASE_COUNTDOWN)

    def _begin_recording(self, job):
        if job["kind"] == "reference":
            if job["take"] == 0:
                self.engine.start_reference(job["movement_id"], job["duration"], job["hand"],
                                            job["hand_mm"])
            self.engine.start_take(job["take"])
            path = job["paths"][job["take"]] if job["take"] < len(job["paths"]) else None
            self.take_started.emit(job["take"] + 1, job["takes"])
            self._set_phase(PHASE_REFERENCE)
        else:
            self.engine.start_session(job["ref"], job["params"])
            path = job["path"]
            self._set_phase(PHASE_SESSION)
        self._recorder = VideoRecorder(path) if path else None
        job["t0"] = time.perf_counter()

    def _close_recorder(self, end_elapsed: float) -> str:
        if self._recorder is None:
            return ""
        out = self._recorder.close(end_elapsed)
        self._recorder = None
        return str(out) if out else ""

    def _discard_recorder(self):
        if self._recorder is not None:
            path = self._recorder.close()
            self._recorder = None
            if path:
                Path(path).unlink(missing_ok=True)

    def _record_step(self, job):
        elapsed = time.perf_counter() - job["t0"]
        done = elapsed >= job["duration"]
        elapsed = min(elapsed, job["duration"])
        if job["kind"] == "reference":
            frame = self.engine.reference_frame(elapsed)
        else:
            frame = self.engine.session_frame(elapsed)
        if self._recorder is not None:
            self._recorder.write(frame.image, elapsed)
        self.frame_ready.emit(frame, self._phase, elapsed, job["duration"])
        if not done:
            return job

        video = self._close_recorder(job["duration"])
        if job["kind"] == "session":
            self.engine.finish_session()
            self._set_phase(PHASE_PREVIEW)
            self.rep_done.emit(video, job["duration"])
            return None

        try:
            info = self.engine.end_take()
        except Exception:
            if video:
                Path(video).unlink(missing_ok=True)
            for v in job["videos"]:
                Path(v).unlink(missing_ok=True)
            raise
        job["videos"].append(video)
        job["take"] += 1
        self.take_done.emit(job["take"], job["takes"], info, video)
        if job["take"] < job["takes"]:
            self._start_countdown(job)
            return job
        ref = self.engine.finish_reference()
        self._set_phase(PHASE_PREVIEW)
        self.reference_done.emit(ref, job["videos"])
        return None
