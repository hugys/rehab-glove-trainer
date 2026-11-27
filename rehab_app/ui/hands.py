"""Which hand: wording for the screens, and the hand the camera actually recorded."""
from __future__ import annotations

from collections import Counter


def hand_phrase(role: str, side: str) -> str:
    """hand_phrase("unaffected", "left") -> "unaffected (left) hand"."""
    return f"{role} ({side}) hand"


def wrong_hand_text(expected: str) -> str:
    return f"Wrong hand - use your {expected.upper()} hand"


class HandTally:
    """Counts the hand the camera saw over one take / repetition (tracked results only)."""

    def __init__(self):
        self._counts: Counter = Counter()

    def reset(self):
        self._counts.clear()

    def add(self, frame):
        if frame.new_measurement and frame.hand:
            self._counts[frame.hand] += 1

    def recorded(self) -> str | None:
        """The hand seen in most of the recording, or None if it was never identified."""
        return self._counts.most_common(1)[0][0] if self._counts else None

    def label(self, expected: str) -> str:
        """e.g. "left hand ✓" or "⚠ RIGHT hand (expected left)" for event lists."""
        hand = self.recorded()
        if hand is None:
            return "hand not identified"
        if hand == expected:
            return f"{hand} hand ✓"
        return f"⚠ {hand.upper()} hand (expected {expected})"
