"""The benchmark behind `.auto/measure.sh`.

Measures what the Pi spends to serve one of everything: the eight chart cards
(four windows x two themes), the status card, an alert fan-out to 100
subscribers, and two dashboard refreshes.

Four things make the numbers comparable across runs weeks apart:

  * **A CPU clock.** Timings use `time.perf_counter()`, which counts only the
    CPU this process actually burned. This benchmark's real home is a desktop
    someone is also using - a busy machine moved a wall-clock reading of the
    same code from 6.9s to 10.0s, which would have swamped every experiment.
    CPU time does not count the time we spent descheduled.

  * **A pinned clock.** The seeded database is built relative to ANCHOR, not to
    wall-clock now, and every call takes `now=ANCHOR`. Without this the windows
    slide over the data and the metric drifts on its own overnight.
  * **A cached database.** Seeding is ~3s and the loop runs hundreds of times,
    so the DB is built once and reused while its fingerprint matches.
  * **A canary, interleaved, as a diagnostic only.** A fixed tiny render run
    between cards, whose cost never changes with the code under test. It is
    reported, NOT divided out. Normalising by it was tried and abandoned: over
    one batch of five runs the ratio held to 6% against a 43% raw spread, and
    over the next batch the canary moved 41% while the sweep moved 3% and the
    "normalised" metric spread 61% - worse than the raw number it replaced.
    A 50 ms canary samples one instant; a 10 s sweep averages ten seconds of
    machine state. They do not track the same thing.

    So: `canary_mean_ms` shifting more than ~15% between runs means the machine
    changed underneath the measurement. Re-measure before believing a
    regression, and let the zero-noise counters arbitrate.
  * **Counters, not just clocks.** `rows_to_python` counts ORM instances via
    SQLAlchemy's `loaded_as_persistent`, so it drops to near zero the moment a
    path stops hydrating objects and starts reducing in SQL. It has no
    measurement noise at all, which is what makes it able to break a tie the
    stopwatch cannot.

Run it directly for a human-readable breakdown:

    python .auto/bench.py --verbose
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTO = ROOT / ".auto"
DB_FILE = AUTO / "bench.db"
FINGERPRINT = AUTO / "bench.fingerprint.json"

# The clock the whole benchmark runs against. Fixed, so "Last 7d" means the
# same 7 days in run 1 and run 400.
ANCHOR = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

TZ_NAME = "Europe/Kyiv"
INTERVAL = 30           # READ_INTERVAL_SECONDS as deployed
SEED_DAYS = 10
SEED_RNG = 7
SUBSCRIBERS = 100
DARK_SHARE = 0.3        # a realistic theme mix, so fan-out renders two cards

FINGERPRINT_KEYS = {
    "anchor": ANCHOR.isoformat(),
    "interval": INTERVAL,
    "days": SEED_DAYS,
    "rng": SEED_RNG,
    "version": 3,
}


# --------------------------------------------------------------------------
# import layout - the services are not packages (see conftest.py)
# --------------------------------------------------------------------------

def install_paths() -> None:
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for name, value in {
        "DATABASE_URL": "sqlite://",
        "RABBITMQ_HOST": "localhost",
        "RABBITMQ_USER": "guest",
        "RABBITMQ_PASS": "guest",
        "AQ_EXCHANGE": "aq.alerts",
        "DRY_RUN": "true",
    }.items():
        os.environ.setdefault(name, value)

    for path in (ROOT / "reporter-bot", ROOT / "sensor-reader", ROOT):
        entry = str(path)
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)
    sys.path.append(str(ROOT / "web-api"))


install_paths()

import matplotlib                                          # noqa: E402
matplotlib.use("Agg")

from sqlalchemy import event                               # noqa: E402
from sqlalchemy.orm import Session                         # noqa: E402
from zoneinfo import ZoneInfo                              # noqa: E402

from common.air_quality import Thresholds                  # noqa: E402
from common.db import Database, Reading                    # noqa: E402


# --------------------------------------------------------------------------
# seeding
# --------------------------------------------------------------------------

def db_url(path: pathlib.Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def seed_needed() -> bool:
    if not DB_FILE.exists() or not FINGERPRINT.exists():
        return True
    try:
        return json.loads(FINGERPRINT.read_text()) != FINGERPRINT_KEYS
    except (json.JSONDecodeError, OSError):
        return True


def build_database() -> None:
    """Migrate and seed the benchmark DB. Alembic, not `create_all`.

    `Database.create_all` is test-only in this repo because it cannot alter an
    existing table. Going through the migrations means a model changed without
    one fails here loudly instead of quietly benchmarking a schema production
    will never have.
    """
    if DB_FILE.exists():
        DB_FILE.unlink()
    FINGERPRINT.unlink(missing_ok=True)

    env = dict(os.environ, DATABASE_URL=db_url(DB_FILE))
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "alembic upgrade failed while building the benchmark database:\n"
            f"{proc.stdout}\n{proc.stderr}"
        )

    db = Database(url=db_url(DB_FILE))
    rng = random.Random(SEED_RNG)
    start = ANCHOR - timedelta(days=SEED_DAYS)
    rows = []
    for i in range(int(SEED_DAYS * 86400 / INTERVAL)):
        ts = start + timedelta(seconds=i * INTERVAL)
        hour = ts.hour + ts.minute / 60
        diurnal = 6 + 5 * math.sin((hour - 4) / 24 * 2 * math.pi)
        # A two-day episode 1-3 days back, so the warn and high bands, the
        # level split and the peak labels are all exercised. A flat series
        # tests none of them.
        age_days = (ANCHOR - ts).total_seconds() / 86400
        episode = 40 if 1.0 < age_days < 3.0 else 0.0
        pm25 = max(0.0, round(diurnal + episode + rng.gauss(0, 2), 1))
        pm10 = max(0.0, round(pm25 * 1.55 + rng.gauss(0, 3), 1))
        level = ("err" if pm25 >= 75 or pm10 >= 100
                 else "warn" if pm25 >= 35 or pm10 >= 50 else "ok")
        rows.append(Reading(pm25=pm25, pm10=pm10, status=level, timestamp=ts))

    with db.session() as s:
        s.bulk_save_objects(rows)
    db.engine.dispose()
    FINGERPRINT.write_text(json.dumps(FINGERPRINT_KEYS, indent=2))


# --------------------------------------------------------------------------
# counters
# --------------------------------------------------------------------------

class Counters:
    """Queries issued, ORM instances hydrated, cache calls made."""

    def __init__(self, db: Database) -> None:
        self.queries = 0
        self.rows = 0
        self.cache_ops = 0
        self._db = db

        @event.listens_for(db.engine, "before_cursor_execute")
        def _count_query(conn, cursor, statement, params, ctx, many):  # noqa: ANN001
            self.queries += 1

        @event.listens_for(Session, "loaded_as_persistent")
        def _count_row(session, instance):                             # noqa: ANN001
            # Only instances loaded from *this* engine's sessions count.
            if session.get_bind() is db.engine:
                self.rows += 1

    def reset(self) -> None:
        self.queries = self.rows = self.cache_ops = 0

    def snapshot(self) -> tuple[int, int, int]:
        return self.queries, self.rows, self.cache_ops


class CountingCache:
    """Wraps a subscriber cache so its calls are counted like any other
    external service. In production this is Redis over a socket."""

    def __init__(self, inner, counters: Counters) -> None:
        self._inner = inner
        self._c = counters

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            self._c.cache_ops += 1
            return attr(*args, **kwargs)

        return wrapped


# --------------------------------------------------------------------------
# the workload
# --------------------------------------------------------------------------

def raise_priority() -> str:
    """Ask the OS to schedule this process above the desktop.

    The benchmark's home is a machine its owner is also using, and a browser
    renderer at normal priority is a direct competitor for the same core. One
    notch above normal - not `HIGH`, which makes the desktop feel sluggish and
    would be a rude thing for a background loop to do.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ABOVE_NORMAL = 0x00008000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.kernel32.SetPriorityClass(handle, ABOVE_NORMAL):
                return "above-normal"
        except Exception:
            pass
        return "default"
    try:
        os.nice(-5)
        return "nice-5"
    except (AttributeError, OSError, PermissionError):
        return "default"


def canary_ms(rounds: int = 5) -> float:
    """A fixed, tiny render whose cost never changes with the code under test.

    `min` of N is a very stable estimator right up until the machine is busy for
    the whole run, which contaminates every repetition at once and cannot be
    seen from inside a single measurement. This costs ~50 ms and exercises the
    same resource the real workload does, so a reading well above its usual
    value means the run was measured on a loaded box - re-measure before
    believing a regression.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    values = np.linspace(0, 1, 256)
    best = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        fig = plt.figure(figsize=(2, 1.5), dpi=72)
        ax = fig.add_subplot(111)
        ax.plot(values, values ** 2)
        fig.canvas.draw()
        plt.close(fig)
        best = min(best, (time.perf_counter() - t0) * 1000)
    return round(best, 2)


def resolve_themes(subs, chat_ids):
    """Themes for a set of chats, through whatever API Subscriptions offers.

    Prefers a bulk lookup when one exists, so an experiment that adds
    `Subscriptions.themes()` moves `fanout_calls` without the benchmark having
    to be rewritten to notice.
    """
    bulk = getattr(subs, "themes", None)
    if callable(bulk):
        return bulk(chat_ids)
    return {cid: subs.theme(cid) for cid in chat_ids}


def run(reps: int, verbose: bool) -> dict:
    from reports import ReadingReports, Window
    from subscriber_cache import InMemorySubscriberCache
    from subscriptions import Subscriptions
    from service import DashboardService
    from ranges import parse_range

    tz = ZoneInfo(TZ_NAME)
    thresholds = Thresholds()
    db = Database(url=db_url(DB_FILE))
    counters = Counters(db)

    reports = ReadingReports(db, tz, thresholds, reading_interval_seconds=INTERVAL)
    local_now = ANCHOR.astimezone(tz)

    windows = [Window.TODAY, Window.LAST_12H, Window.LAST_7D, Window.PATTERNS]
    themes = ["light", "dark"]

    # ---- the eight chart cards, plus status ----
    timings: dict[str, list[float]] = {}
    card_bytes: dict[str, int] = {}

    # Warm-up, discarded. The first render in a process pays for matplotlib's
    # font cache and a pile of lazy imports - charging that to whichever card
    # happens to go first was most of a 25% run-to-run spread.
    reports.for_window(Window.LAST_12H, now=local_now, theme="light")
    canary_samples: list[float] = []

    counters.reset()

    for rep in range(reps):
        for window in windows:
            for theme in themes:
                key = f"{window.slug}:{theme}"
                t0 = time.perf_counter()
                report = reports.for_window(window, now=local_now, theme=theme)
                elapsed = (time.perf_counter() - t0) * 1000
                timings.setdefault(key, []).append(elapsed)
                if report.chart is None:
                    raise SystemExit(f"{key} produced no chart - empty window?")
                card_bytes[key] = len(report.chart.getvalue())
                canary_samples.append(canary_ms(rounds=1))

        for theme in themes:
            key = f"status:{theme}"
            t0 = time.perf_counter()
            status = reports.status_report(now=ANCHOR, theme=theme)
            elapsed = (time.perf_counter() - t0) * 1000
            timings.setdefault(key, []).append(elapsed)
            if status is None or status.chart is None:
                raise SystemExit("status card produced no chart")
            card_bytes[key] = len(status.chart.getvalue())

    sweep_queries, sweep_rows, _ = counters.snapshot()
    # Counters accumulate over every rep; report one sweep's worth.
    sweep_queries //= reps
    sweep_rows //= reps

    # `min`, not `median`: scheduler noise on a CPU-bound render is strictly
    # additive, so the fastest of N observations is the closest estimate of
    # the real cost - and it is far more stable across processes.
    medians = {k: min(v) for k, v in timings.items()}
    card_keys = [f"{w.slug}:{t}" for w in windows for t in themes]

    # ---- alert fan-out to 100 subscribers ----
    cache = CountingCache(InMemorySubscriberCache(), counters)
    subs = Subscriptions(db, cache)
    rng = random.Random(SEED_RNG)
    for cid in range(1, SUBSCRIBERS + 1):
        subs.subscribe(cid)
        subs.set_theme(cid, "dark" if rng.random() < DARK_SHARE else "light")

    counters.reset()
    t0 = time.perf_counter()
    subscriber_ids = sorted(subs.all())
    theme_by_chat = resolve_themes(subs, subscriber_ids)
    # Mirror bot._on_alert: one render per distinct theme, one look at the
    # recent readings for the whole fan-out. Prefers the bulk API when it
    # exists so the benchmark tracks production rather than a copy of it.
    make = getattr(reports, "alert_reports", None)
    if callable(make):
        cards = make(88.0, 130.0, ANCHOR,
                     themes=set(theme_by_chat.values()), now=ANCHOR)
    else:
        cards = {}
        for chat_id in subscriber_ids:
            theme = theme_by_chat[chat_id]
            if theme not in cards:
                cards[theme] = reports.alert_report(
                    88.0, 130.0, ANCHOR, theme=theme, now=ANCHOR,
                )
    fanout_ms = (time.perf_counter() - t0) * 1000
    fanout_queries, fanout_rows, fanout_cache = counters.snapshot()
    fanout_renders = len(cards)

    # ---- two dashboard refreshes ----
    service = DashboardService(
        db, tz, thresholds,
        reading_interval_seconds=INTERVAL, retention_days=90,
    )
    dash_calls = dash_bytes = 0
    dash_ms = 0.0
    for preset in ("24h", "7d"):
        rng_ = parse_range(preset=preset, now=ANCHOR, tz=tz)
        counters.reset()
        t0 = time.perf_counter()
        payload = [
            service.latest(now=ANCHOR),
            service.series(rng_),
            service.summary(rng_),
            service.patterns(rng_),
        ]
        dash_ms += (time.perf_counter() - t0) * 1000
        calls, _, _ = counters.snapshot()
        dash_calls += calls
        dash_bytes += len(json.dumps(payload, default=str))

    db.engine.dispose()

    card_total = sum(medians[k] for k in card_keys)
    card_worst = max(medians[k] for k in card_keys)
    canary = min(canary_samples)
    result = {
        "card_ms_total": round(card_total, 1),
        "card_ms_worst": round(card_worst, 1),
        "card_ms_p50": round(statistics.median([medians[k] for k in card_keys]), 1),
        "status_ms": round(max(medians[f"status:{t}"] for t in themes), 1),
        "rows_to_python": sweep_rows,
        "service_calls": sweep_queries,
        "fanout_calls": fanout_queries,
        "fanout_rows": fanout_rows,
        "fanout_cache_ops": fanout_cache,
        "fanout_renders": fanout_renders,
        "fanout_ms": round(fanout_ms, 1),
        "bytes_total": sum(card_bytes[k] for k in card_keys),
        "dash_calls": dash_calls,
        "dash_bytes": dash_bytes,
        "dash_ms": round(dash_ms, 1),
        "canary_ms": round(canary, 2),
        "canary_mean_ms": round(statistics.mean(canary_samples), 2),
    }

    if verbose:
        print("\ncard                 median ms    spread      KB")
        for key in card_keys + [f"status:{t}" for t in themes]:
            samples = timings[key]
            spread = max(samples) - min(samples)
            print(f"  {key:<20} {medians[key]:8.1f}  {spread:8.1f}  "
                  f"{card_bytes[key] / 1024:6.1f}")
        print(f"\nfan-out to {SUBSCRIBERS}: {fanout_queries} queries, "
              f"{fanout_cache} cache ops, {fanout_renders} renders, "
              f"{fanout_ms:.0f} ms")
        print(f"dashboard x2:    {dash_calls} queries, "
              f"{dash_bytes / 1024:.1f} KB, {dash_ms:.0f} ms\n")

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--reseed", action="store_true")
    args = parser.parse_args()

    priority = raise_priority()
    if args.reseed or seed_needed():
        build_database()

    metrics = run(reps=args.reps, verbose=args.verbose)
    if args.verbose:
        print(f"scheduler priority: {priority}")
    for name, value in metrics.items():
        print(f"METRIC {name}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
