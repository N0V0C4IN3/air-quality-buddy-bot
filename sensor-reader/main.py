# main.py
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from config import settings
from sensor import SensorReader
from common.db import Database, ReadingRepository  # using your db.py

# ---- logging ----
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sensor-reader")

_stop = False
def _handle_stop(signum, frame):
    global _stop
    _stop = True
signal.signal(signal.SIGINT, _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)


def classify_status(pm25: float, pm10: float) -> str:
    """
    Trivial classifier; tune to your needs.
    """
    if pm25 >= settings.pm25_err or pm10 >= settings.pm10_err:
        return "err"
    if pm25 >= settings.pm25_warn or pm10 >= settings.pm10_warn:
        return "warn"
    return "ok"


def validate(pm25: float, pm10: float) -> tuple[float, float]:
    """
    Clamp negative/NaN/inf; SDS011 shouldn’t produce negatives but be defensive.
    """
    def _clean(x: float) -> float:
        if x != x:  # NaN
            return 0.0
        if x == float("inf") or x == float("-inf"):
            return 0.0
        if x < 0:
            return 0.0
        return round(float(x), 1)  # one decimal place is plenty for SDS011
    return _clean(pm25), _clean(pm10)


def main():
    log.info("Starting sensor-reader (dry_run=%s, port=%s)", settings.dry_run, settings.sds011_port)
    db = Database(url=settings.database_url, echo=False)
    db.create_all()

    sensor = SensorReader()

    interval = max(1, settings.interval_seconds)

    while not _stop:
        try:
            pm25, pm10 = sensor.read()
            pm25, pm10 = validate(pm25, pm10)
            status = classify_status(pm25, pm10)
            now = datetime.now(timezone.utc)

            log.info("Reading pm2.5=%.1f μg/m³, pm10=%.1f μg/m³, status=%s", pm25, pm10, status)

            with db.session() as s:
                repo = ReadingRepository(s)
                # You renamed ts -> timestamp in db.py
                repo.add(pm25=pm25, pm10=pm10, status=status, timestamp=now)

        except Exception as e:
            log.exception("Failed to read/store sensor data: %s", e)

        # sleep with fast shutdown
        for _ in range(interval):
            if _stop:
                break
            time.sleep(1)

    log.info("Sensor-reader stopped gracefully.")


if __name__ == "__main__":
    sys.exit(main())
