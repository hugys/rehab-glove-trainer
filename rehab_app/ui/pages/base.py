"""Common page base class and small layout helpers."""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ..context import AppContext


class Page(QWidget):
    """A screen in the clinician window. Main window forwards worker signals to the
    currently visible page via the on_* hooks."""

    title = ""

    def __init__(self, ctx: AppContext, navigate: Callable[..., None]):
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self.navigate = navigate

    # lifecycle
    def on_enter(self, **kwargs): ...
    def on_leave(self): ...
    def can_leave(self) -> bool:
        return True

    # worker hooks
    def on_frame(self, frame, phase: str, elapsed: float, duration: float): ...
    def on_countdown(self, n: int): ...
    def on_phase(self, phase: str): ...
    def on_take_started(self, take: int, total: int): ...
    def on_take_done(self, take: int, total: int, info: dict, video: str): ...
    def on_reference_done(self, ref, videos: list[str]): ...
    def on_rep_done(self, video: str, duration: float): ...
    def on_error(self, msg: str): ...


def card(*widgets, title: str | None = None, spacing: int = 10) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(spacing)
    if title:
        lay.addWidget(heading(title, 2))
    for w in widgets:
        lay.addWidget(w)
    return f


def heading(text: str, level: int = 1) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(f"h{level}")
    return lbl


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


def button(text: str, kind: str | None = None, slot=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if slot:
        b.clicked.connect(slot)
    return b
