"""Test setup.

The services are not packages — each Dockerfile copies one service directory to
/app and the code imports flat (`from sampler import Sample`). Tests reproduce
that by putting the service directories on sys.path.

Both services have a `config` module. `sensor-reader` comes first on the path,
so a bare `import config` is the sensor-reader one; reporter-bot's is loaded by
file path in test_config.py. Each config module calls `Settings.load()` at
import, so the sensor-reader variables are set here, before any collection.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent

os.environ.setdefault("MPLBACKEND", "Agg")  # charts must not need a display

# Enough for sensor-reader's Settings.load() to succeed at import time.
for name, value in {
    "DATABASE_URL": "sqlite://",
    "RABBITMQ_HOST": "localhost",
    "RABBITMQ_USER": "guest",
    "RABBITMQ_PASS": "guest",
    "AQ_EXCHANGE": "aq.alerts",
    "DRY_RUN": "true",
}.items():
    os.environ.setdefault(name, value)

# Order matters: repo root first so `common` resolves, then sensor-reader so a
# bare `import config` is its one, then reporter-bot for charts/reports/markup.
for path in (ROOT / "reporter-bot", ROOT / "sensor-reader", ROOT):
    entry = str(path)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
