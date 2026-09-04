"""Entry point: wire config to the app and serve it.

This service only reads. It does not run `alembic upgrade` - sensor-reader and
reporter-bot own the schema, and a reader racing them to migrate buys nothing.
"""
from __future__ import annotations

import logging
import os

import uvicorn

from common.db import Database

from api import create_app
from auth import AccessGuard
from config import settings
from service import DashboardService

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("web_api")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# One engine for the process, as everywhere else in this repo.
db = Database(url=settings.database_url)

app = create_app(
    service=DashboardService(
        db,
        settings.tz,
        settings.thresholds,
        reading_interval_seconds=settings.reading_interval_seconds,
        retention_days=settings.retention_days,
        max_points=settings.max_points,
    ),
    guard=AccessGuard(
        settings.auth_mode,
        bot_token=settings.telegram_token,
        access_token=settings.access_token,
        max_age_seconds=settings.init_data_max_age,
    ),
    tz=settings.tz,
    static_dir=STATIC_DIR,
)


if __name__ == "__main__":
    log.info(
        "serving dashboard on %s:%s (auth=%s, tz=%s)",
        settings.host, settings.port, settings.auth_mode.value,
        getattr(settings.tz, "key", settings.tz),
    )
    uvicorn.run(app, host=settings.host, port=settings.port,
                log_level=settings.log_level.lower())
