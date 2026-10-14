"""USB-serial link to the glove's ESP32 (protocol from the team's script).

Protocol: one ASCII line per update, five comma-separated ints for
thumb,index,middle,ring,pinky:   +1 = CW (flex), -1 = CCW (extend), 0 = hold
    e.g. "-1,0,1,0,0\\n"
Commands are rate-limited to SEND_HZ (heartbeat even if unchanged). All motors are
set to hold whenever a repetition ends or is aborted.

The glove does not exist yet: by default the app uses NullServoLink, which
sends nothing but remembers the last command so the UI can show what WOULD be sent.
"""
from __future__ import annotations

import time

from ..config import FINGERS

SERIAL_BAUD = 115200
SEND_HZ = 20
HOLD_ALL = [0] * len(FINGERS)


def format_line(codes: list[int]) -> str:
    return ",".join(str(int(c)) for c in codes) + "\n"


def codes_from(cmd: dict[str, int] | None) -> list[int]:
    return [int((cmd or {}).get(f, 0)) for f in FINGERS]


class ServoLink:
    name = "base"
    connected = False

    def __init__(self, hz: float = SEND_HZ):
        self._min_interval = 1.0 / hz
        self._last_sent_t = 0.0
        self.last_codes: list[int] = list(HOLD_ALL)
        self.sent = 0

    def send(self, cmd: dict[str, int] | None, force: bool = False) -> bool:
        """cmd: finger -> +1/-1/0. Returns True if a line was written this call."""
        now = time.monotonic()
        if not force and now - self._last_sent_t < self._min_interval:
            return False
        codes = codes_from(cmd)
        self._last_sent_t = now
        self.last_codes = codes
        self._write(format_line(codes))
        self.sent += 1
        return True

    def release_all(self) -> None:
        self.send(None, force=True)

    def status(self) -> str:
        return self.name

    def _write(self, line: str) -> None:
        pass

    def close(self) -> None:
        pass


class NullServoLink(ServoLink):
    """No hardware: nothing is sent; last command kept for display."""
    name = "not connected"


class SerialServoLink(ServoLink):
    name = "serial"

    def __init__(self, port: str, baud: int = SERIAL_BAUD, hz: float = SEND_HZ):
        super().__init__(hz)
        try:
            import serial  # pyserial
        except ImportError as e:
            raise RuntimeError("pyserial is not installed (pip install pyserial)") from e
        self.port = port
        self._conn = serial.Serial(port, baud, timeout=0, write_timeout=0)
        time.sleep(2.0)          # ESP32 resets when the port opens; let it boot
        self.connected = True
        self.name = f"ESP32 @ {port}"

    def _write(self, line: str) -> None:
        if not self.connected:
            return
        try:
            self._conn.write(line.encode("ascii"))
        except Exception:
            self.connected = False   # never let a dropped link crash a session
            self.name = f"ESP32 @ {self.port} (write failed)"

    def close(self) -> None:
        try:
            if self.connected:
                self._conn.write(format_line(HOLD_ALL).encode("ascii"))
            self._conn.close()
        except Exception:
            pass
        self.connected = False


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]
