"""Formatting of error values: % of hand size, plus mm when the hand size was measured.

Sessions recorded before errors moved to % of hand size stored mm (err_units 'mm').
"""
from __future__ import annotations


def fmt_err(value: float | None, hand_mm: float | None = None, units: str = "pct",
            decimals: int = 1) -> str:
    if value is None or value != value:          # None or NaN
        return "--"
    if units == "mm":
        return f"{value:.{decimals}f} mm"
    text = f"{value:.{decimals}f} %"
    if hand_mm:
        text += f" ({value * hand_mm / 100:.{decimals}f} mm)"
    return text


def unit_label(units: str = "pct") -> str:
    return "mm" if units == "mm" else "% of hand size"
