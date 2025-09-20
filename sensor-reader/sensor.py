# sensor.py
import logging
import random
import time
from typing import Tuple, Optional, List

import serial  # from pyserial
from config import settings

log = logging.getLogger(__name__)

"""
SDS011 frames (data):
  AA C0 pm25_lo pm25_hi pm10_lo pm10_hi id_lo id_hi chk AB
  Values are reported in 0.1 μg/m³.
"""

def _read_frame(ser: serial.Serial, timeout_s: float = 2.0) -> Optional[bytes]:
    """Scan for a valid AA C0 ... AB frame; return None on timeout."""
    ser.timeout = timeout_s
    START, DATA, END = 0xAA, 0xC0, 0xAB
    while True:
        b = ser.read(1)
        if not b:
            return None  # timeout
        if b[0] != START:
            continue
        b2 = ser.read(1)
        if not b2 or b2[0] != DATA:
            continue
        rest = ser.read(8)
        if len(rest) != 8 or rest[-1] != END:
            continue
        frame = bytes([START, DATA]) + rest
        # verify checksum (bytes 2..7)
        if (sum(frame[2:8]) % 256) != frame[8]:
            continue
        return frame

def _parse_frame(frame: bytes) -> Optional[Tuple[float, float]]:
    if len(frame) != 10 or frame[0] != 0xAA or frame[1] != 0xC0 or frame[9] != 0xAB:
        return None
    if (sum(frame[2:8]) % 256) != frame[8]:
        return None
    pm25_raw = int.from_bytes(frame[2:4], byteorder="little")
    pm10_raw = int.from_bytes(frame[4:6], byteorder="little")
    return (pm25_raw / 10.0, pm10_raw / 10.0)

class SensorReader:
    """
    Each call to read():
      1) Wake sensor (WORK=1)
      2) Ensure ACTIVE reporting & period=0 (continuous)
      3) Warm up for N seconds
      4) Read one valid frame (with retries), discarding all-zero warm-up frames
      5) Put sensor back to SLEEP
    """

    def __init__(
        self,
        warmup_seconds: float = 3.0,
        read_timeout_s: float = 2.0,
        retries: int = 8,
        persist_cfg: bool = False,   # session-only changes (no EEPROM wear)
        ignore_zero_frames: bool = True,
        extra_settle_s: float = 2.0, # wait a bit before retry when 0,0 is seen
        number_of_readings_per_session: int = 10,
        interval_between_readings: int = 2
    ) -> None:
        self._mock = settings.dry_run or (settings.sds011_port is None)
        self._ser: Optional[serial.Serial] = None
        self.warmup_seconds = warmup_seconds
        self.read_timeout_s = read_timeout_s
        self.retries = max(1, retries)
        self.persist_cfg = persist_cfg
        self.ignore_zero_frames = ignore_zero_frames
        self.extra_settle_s = max(0.0, extra_settle_s)
        self.number_of_readings_per_session = number_of_readings_per_session
        self.interval_between_readings = interval_between_readings

        if self._mock:
            log.info("SensorReader: DRY_RUN or no port configured; generating mock values.")
            return

        self._ser = serial.Serial(
            port=settings.sds011_port,
            baudrate=settings.sds011_baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self.read_timeout_s,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        try:
            self._ser.dtr = False
            self._ser.rts = False
        except Exception:
            pass

        log.info(
            "SensorReader: opened port=%s baud=%s warmup=%.1fs timeout=%.1fs retries=%d persist_cfg=%s",
            settings.sds011_port, settings.sds011_baud, self.warmup_seconds,
            self.read_timeout_s, self.retries, self.persist_cfg
        )

    # ---------- command helpers (19-byte AA B4 ... frames) ----------

    @staticmethod
    def _build_cmd(payload: List[int], dev_id_lo: int = 0xFF, dev_id_hi: int = 0xFF) -> bytes:
        data = (list(payload) + [0x00] * 13)[:13]
        frame = [0xAA, 0xB4] + data + [dev_id_lo, dev_id_hi]
        chk = (sum(frame[2:15]) + frame[15] + frame[16]) % 256
        return bytes(frame + [chk, 0xAB])

    def _send_cmd(self, payload: List[int], label: str) -> None:
        if self._ser is None:
            return
        frame = self._build_cmd(payload)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("%s → %s", label, frame.hex(" "))
        else:
            log.info("%s", label)
        self._ser.reset_input_buffer()
        self._ser.write(frame)
        self._ser.flush()

    def _wake(self) -> None:
        # Always SET (0x01), work=1
        self._send_cmd([0x06, 0x01, 0x01], "SDS011 WAKE (work=1)")
        time.sleep(0.3)

    def _sleep(self) -> None:
        # Always SET (0x01), work=0
        self._send_cmd([0x06, 0x01, 0x00], "SDS011 SLEEP (work=0)")
        time.sleep(0.3)

    def _set_active(self) -> None:
        # Reporting mode ACTIVE: [0x02, set]; (third byte implicit 0 → active)
        self._send_cmd([0x02, 0x01 if self.persist_cfg else 0x00], "SDS011 SET_ACTIVE (continuous)")

    def _set_period(self, minutes: int = 0) -> None:
        m = max(0, min(30, int(minutes))) & 0xFF
        self._send_cmd([0x08, 0x01 if self.persist_cfg else 0x00, m], f"SDS011 SET_PERIOD (min={m})")

    def _query_once(self) -> None:
        self._send_cmd([0x04], "SDS011 QUERY_PM (one-shot)")

    # ---------------- reading cycle ----------------

    def read(self) -> Tuple[float, float]:
        if self._mock or self._ser is None:
            pm25 = max(0.0, random.gauss(8, 3))
            pm10 = max(0.0, random.gauss(12, 4))
            log.debug("Mock reading: PM2.5=%.1f PM10=%.1f", pm25, pm10)
            return (round(pm25, 1), round(pm10, 1))

        try:
            self._wake()
            self._set_period(0)
            self._set_active()
            self._query_once()  # nudge first frame

            log.info("SDS011 warm-up for %.1fs…", self.warmup_seconds)
            time.sleep(self.warmup_seconds)

            for attempt in range(1, self.retries + 1):
                pm25_readings = []
                pm10_readings = []
                is_successfull_attempt = True
                for reading_no in range(1, self.number_of_readings_per_session + 1):
                    frame = _read_frame(self._ser, timeout_s=self.read_timeout_s)
                    if not frame:
                        log.debug("Start attempt %d/%d, reading # %d: timeout/no frame", attempt, self.retries, reading_no)
                        is_successfull_attempt = False
                        break

                    if log.isEnabledFor(logging.DEBUG):
                        log.debug("C0 frame: %s", frame.hex(" "))

                    parsed = _parse_frame(frame)
                    if not parsed:
                        log.debug("Read attempt %d/%d, reading # %d: bad frame: %s", attempt, self.retries, frame.hex(" "), reading_no)
                        is_successfull_attempt = False
                        break

                    pm25, pm10 = parsed

                    # discard initial 0/0 frames if requested
                    if self.ignore_zero_frames and pm25 == 0.0 and pm10 == 0.0:
                        log.info("Discarded zero frame (attempt %d/%d); settling for %.1fs…",
                                attempt, self.retries, self.extra_settle_s)
                        time.sleep(self.extra_settle_s)
                        is_successfull_attempt = False
                        break

                    log.info("Adding SDS011 data: PM2.5=%.1f µg/m³, PM10=%.1f µg/m³, reading # %d", pm25, pm10, reading_no)

                    pm25_readings.append(pm25)
                    pm10_readings.append(pm10)
                    
                    time.sleep(self.interval_between_readings)
                
                if not is_successfull_attempt:
                    continue

                pm25_mean = sum(pm25_readings) / self.number_of_readings_per_session
                pm10_mean = sum(pm10_readings) / self.number_of_readings_per_session

                log.info("SDS011 data: PM2.5=%.1f µg/m³, PM10=%.1f µg/m³, number of redings: %d", pm25_mean, pm10_mean, self.number_of_readings_per_session)
                
                return (round(pm25_mean, 1), round(pm10_mean, 1))

            raise RuntimeError("Failed to read/parse SDS011 frame")
        finally:
            try:
                self._sleep()
            except Exception as e:
                log.warning("SDS011: failed to send SLEEP in finally: %s", e)
