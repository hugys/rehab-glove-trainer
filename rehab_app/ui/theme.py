"""Colours and the application stylesheet."""
from PySide6.QtGui import QColor

BG = "#f4f6f9"
SURFACE = "#ffffff"
BORDER = "#d9dee7"
TEXT = "#1d2433"
MUTED = "#5f6b7d"
ACCENT = "#1f6feb"
ACCENT_DARK = "#1858bd"
OK = "#1f9d55"
WARN = "#e0a100"
BAD = "#d64545"
ASSIST = "#8a3ffc"     # glove motor assisting

FINGER_COLORS = {
    "thumb": "#e8743b",
    "index": "#1f6feb",
    "middle": "#19a979",
    "ring": "#945ecf",
    "pinky": "#d6456d",
}


def qcolor(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def error_color(err: float | None, threshold: float) -> str:
    if err is None:
        return MUTED
    if err <= threshold * 0.75:
        return OK
    if err <= threshold:
        return WARN
    return BAD


STYLESHEET = f"""
QWidget {{ font-size: 14px; color: {TEXT}; }}
QMainWindow, QDialog, QStackedWidget > QWidget#page {{ background: {BG}; }}
QLabel#h1 {{ font-size: 24px; font-weight: 600; }}
QLabel#h2 {{ font-size: 18px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#topbar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #a0a8b5; background: #eef0f4; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#primary:disabled {{ background: #9dbcf0; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton#nav {{ border: none; background: transparent; padding: 6px 10px; color: {MUTED}; }}
QPushButton#nav:checked {{ color: {ACCENT}; font-weight: 600; border-bottom: 2px solid {ACCENT}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px;
}}
QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
               gridline-color: {BORDER}; }}
QHeaderView::section {{ background: #eef1f6; border: none; padding: 6px; font-weight: 600; }}
"""
