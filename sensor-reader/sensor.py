# sensor.py
import random
import time
from typing import Tuple, Optional

import serial  # from pyserial

from config import settings

"""
SDS011 frames (data):
  0: 0xAA  (start)
  1: 0xC0  (data frame)
  2: PM2.5 low byte
  3: PM2.5 high byte
  4: PM10  low byte
  5: PM10  high byte
  6: ID byte 1
  7: ID byte 2
  8: checksum = (bytes 2..7) % 256
  9: 0xAB  (end)

Values are reported in 0.1 μg/m³.
"""

def _read_frame(ser: serial.Serial, timeout_s: float = 2.0) -> Optional[bytes]:
    ser.timeout = timeout_s
    # Read until we find 0xAA 0xC0, then read the rest of the frame
    while True:
        b = ser.read(1)
        if not b:
            return None
        if b[0] != 0xAA:
            continue
        b2 = ser.read(1)
        if not b2:
            return None
        if b2[0] != 0xC0:
            continue
        # Now read remaining 8 bytes
        rest = ser.read(8)
        if len(rest) != 8:
            return None
        frame = bytes([0xAA, 0xC0]) + rest
        return frame

def _parse_frame(frame: bytes) -> Optional[Tuple[float, float]]:
    if len(frame) != 10:
        return None
    if frame[0] != 0xAA or frame[1] != 0xC0 or frame[9] != 0xAB:
        return None

    # PM2.5 = bytes 2-3 (little-endian), PM10 = bytes 4-5
    pm25_raw = int.from_bytes(frame[2:4], byteorder="little")
    pm10_raw = int.from_bytes(frame[4:6], byteorder="little")

    checksum = sum(frame[2:8]) % 256
    if checksum != frame[8]:
        return None

    pm25 = pm25_raw / 10.0
    pm10 = pm10_raw / 10.0
    return (pm25, pm10)

class SensorReader:
    """
    Works in two modes:
      - DRY_RUN=True => generate mock values.
      - Real: reads SDS011 frames from serial.
    """
    def __init__(self) -> None:
        self._mock = settings.dry_run or (settings.sds011_port is None)
        self._ser: Optional[serial.Serial] = None
        if not self._mock:
            # Typical SDS011: 9600-8N1
            self._ser = serial.Serial(
                port=settings.sds011_port,
                baudrate=settings.sds011_baud,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=2.0,
            )
            # Warm-up: SDS011 laser/fan stabilization
            time.sleep(1.0)

    def read(self) -> Tuple[float, float]:
        if self._mock or self._ser is None:
            pm25 = max(0.0, random.gauss(8, 3))
            pm10 = max(0.0, random.gauss(12, 4))
            return (round(pm25, 1), round(pm10, 1))

        # Try a few frames in case of junk bytes
        for _ in range(5):
            frame = _read_frame(self._ser, timeout_s=2.0)
            if frame is None:
                continue
            parsed = _parse_frame(frame)
            if parsed is None:
                continue
            pm25, pm10 = parsed
            return (round(pm25, 1), round(pm10, 1))

        # If we failed to parse after retries, raise to let caller log it
        raise RuntimeError("Failed to read/parse SDS011 frame")
