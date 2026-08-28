import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from common.db import Database, ReadingRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("housekeeper")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    prune_enabled: bool
    prune_max_age_days: int
    prune_interval_hours: int

    @classmethod
    def load(cls) -> "Settings":
        url = os.getenv("DATABASE_URL")
        if not url:
            raise ConfigError("Missing required env var: DATABASE_URL")
        enabled = os.getenv("PRUNE_ENABLED", "true").lower() in ("1", "true", "yes", "y", "on")
        return cls(
            database_url=url,
            prune_enabled=enabled,
            prune_max_age_days=int(os.getenv("PRUNE_MAX_AGE_DAYS", "90")),
            prune_interval_hours=int(os.getenv("PRUNE_INTERVAL_HOURS", "24")),
        )


def prune_once(db: Database, max_age_days: int, *, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
    with db.session() as s:
        deleted = ReadingRepository(s).prune_older_than(cutoff)
    log.info("Deleted %s rows older than %s", deleted, cutoff.isoformat())
    return deleted


def main():
    settings = Settings.load()
    if not settings.prune_enabled:
        log.info("PRUNE_ENABLED is off; housekeeper idle.")
        while True:
            time.sleep(3600)

    # One engine for the process, not one per pass.
    db = Database(url=settings.database_url, echo=False)

    while True:
        try:
            prune_once(db, settings.prune_max_age_days)
        except Exception as e:
            log.exception("Retention failed: %s", e)
        time.sleep(settings.prune_interval_hours * 3600)


if __name__ == "__main__":
    main()
