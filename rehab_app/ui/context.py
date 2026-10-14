"""Shared application state passed to every page."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config
from ..config import LandmarkSet
from ..data.database import Database
from ..engine.interface import RehabEngine
from ..hardware.servo_link import NullServoLink, ServoLink
from ..workers.engine_worker import EngineWorker


@dataclass
class AppContext:
    db: Database
    engine: RehabEngine
    engine_kind: str
    worker: EngineWorker
    landmark_set: LandmarkSet
    servo: ServoLink = field(default_factory=NullServoLink)
    patient_id: int | None = None
    patient_display: object | None = None  # PatientDisplay window

    def patient(self):
        return self.db.get_patient(self.patient_id) if self.patient_id else None

    def hand_size_mm(self) -> float:
        p = self.patient()
        try:
            return float(p["hand_size_mm"]) if p and p["hand_size_mm"] else config.HAND_SIZE_DEFAULT_MM
        except (KeyError, IndexError):
            return config.HAND_SIZE_DEFAULT_MM

    def record_videos(self) -> bool:
        return self.db.get_setting("record_videos", "1") == "1"

    def mirror_preview(self) -> bool:
        return self.db.get_setting("mirror_preview", "1") == "1"

    def healthy_hand(self) -> str:
        p = self.patient()
        if not p:
            return "right"
        return "left" if p["affected_hand"] == "right" else "right"
