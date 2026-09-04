# main.py
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from common.air_quality import Level, Thresholds
from common.alerts import Alert
from common.db import Database, ReadingRepository

from alerting import AlertGate
from config import settings
from publisher import Publisher
from sampler import Sample, Sampler, build_sampler

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


def sample_once(
    sampler: Sampler,
    db: Database,
    thresholds: Thresholds,
    gate: AlertGate,
    *,
    now: datetime | None = None,
) -> tuple[Sample, Level]:
    """One full cycle: read, store, and offer an alert to the gate."""
    sample = sampler.read()
    level = thresholds.level(sample.pm25, sample.pm10)
    observed_at = now or datetime.now(timezone.utc)

    log.info(
        "Reading pm2.5=%.1f μg/m³, pm10=%.1f μg/m³, status=%s",
        sample.pm25, sample.pm10, level.value,
    )

    with db.session() as s:
        ReadingRepository(s).add(
            pm25=sample.pm25,
            pm10=sample.pm10,
            status=level.value,
            timestamp=observed_at,
        )

    gate.submit(
        Alert(
            level=level,
            pm25=sample.pm25,
            pm10=sample.pm10,
            ts=observed_at.timestamp(),
        )
    )
    return sample, level


def main():
    log.info(
        "Starting sensor-reader (dry_run=%s, port=%s)", settings.dry_run, settings.sds011_port
    )
    # No create_all: the schema belongs to Alembic, and the Dockerfile runs
    # `alembic upgrade head` before this. create_all only ever created a
    # missing table - it could not apply a change to an existing one, which
    # made every column added after the first deploy a silent no-op.
    db = Database(url=settings.database_url, echo=False)

    sampler = build_sampler(settings)
    publisher = Publisher.from_settings(settings)
    gate = AlertGate(publisher.publish, cooldown_seconds=settings.alert_cooldown_seconds)

    interval = settings.interval_seconds

    try:
        while not _stop:
            loop_start = time.monotonic()
            try:
                sample_once(sampler, db, settings.thresholds, gate)
            except Exception as e:
                log.exception("Failed to read/store sensor data: %s", e)

            # Periodic sleep: aim for 'interval' seconds between cycle starts
            elapsed = time.monotonic() - loop_start
            end_time = time.monotonic() + max(0.0, interval - elapsed)
            while not _stop and time.monotonic() < end_time:
                time.sleep(0.2)
    finally:
        sampler.close()
        publisher.close()
        log.info("Sensor-reader stopped gracefully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
