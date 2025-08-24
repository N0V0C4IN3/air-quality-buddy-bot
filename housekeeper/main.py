import os, time, logging
from datetime import datetime, timedelta, timezone
from common.db import Database, ReadingRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("housekeeper")

DATABASE_URL = os.getenv("DATABASE_URL")
PRUNE_MAX_AGE_DAYS = int(os.getenv("PRUNE_MAX_AGE_DAYS", "90"))
PRUNE_INTERVAL_HOURS = int(os.getenv("PRUNE_INTERVAL_HOURS", "24"))

def run_once():
    db = Database(url=DATABASE_URL, echo=False)
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_MAX_AGE_DAYS)
    with db.session() as s:
        repo = ReadingRepository(s)
        deleted = repo.prune_older_than(cutoff)
    log.info("Deleted %s rows older than %s", deleted, cutoff.isoformat())

def main():
    while True:
        try:
            run_once()
        except Exception as e:
            log.exception("Retention failed: %s", e)
        for _ in range(PRUNE_INTERVAL_HOURS * 3600):
            time.sleep(1)

if __name__ == "__main__":
    main()
