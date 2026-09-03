"""The guard that stops "faster" from quietly becoming "wrong".

The obvious way to make a chart card cheaper is to stop plotting every reading
and plot bucket averages instead. That works, and it silently destroys the one
thing this product exists to show: an averaged bucket hides exactly the spike
the alert fired on. A 5-minute mean of a 90 ug/m3 minute and nine quiet ones is
a comfortable-looking 15.

So the rule is not "don't bucket". It is **bucket, but carry the peak** - the
same shape `ReadingRepository.get_buckets` already returns for the dashboard,
which keeps min and max per bucket alongside the average, and the same shape
the web UI shades as a min-max band.

This check asserts, for every window:

  * the largest plotted value equals the true maximum over the range,
  * the smallest plotted value equals the true minimum,
  * the mean of the plotted series is within 2% of the true mean.

The first is the one that matters. It is exact, and it is what makes the
speed-up honest.
"""
from __future__ import annotations

import sys
from datetime import timezone

sys.path.insert(0, __file__.rsplit("fidelity.py", 1)[0])

from bench import ANCHOR, DB_FILE, TZ_NAME, db_url, install_paths, seed_needed, build_database

install_paths()

from zoneinfo import ZoneInfo                     # noqa: E402

from common.air_quality import Thresholds         # noqa: E402
from common.db import Database, ReadingRepository  # noqa: E402

# Readings are stored rounded to 0.1 ug/m3, so anything under half a step is
# float noise rather than a lost peak.
TOLERANCE = 0.05
MEAN_TOLERANCE = 0.02


def plotted_frame(reports, start, end, floor_seconds=0):
    """The data the chart was built from.

    Instrumentation, not a contract - if an experiment moves this seam, update
    the hook here. What must never weaken is the assertion below.
    """
    getter = getattr(reports, "_frame", None)
    if getter is None:
        raise SystemExit(
            "fidelity: ReadingReports._frame is gone. Point this hook at "
            "whatever now produces the plotted data, then re-run."
        )
    try:
        return getter(start, end, floor_seconds=floor_seconds)
    except TypeError:
        return getter(start, end)


def check() -> int:
    from reports import ReadingReports, Window

    if seed_needed():
        build_database()

    tz = ZoneInfo(TZ_NAME)
    db = Database(url=db_url(DB_FILE))
    reports = ReadingReports(db, tz, Thresholds(), reading_interval_seconds=30)
    local_now = ANCHOR.astimezone(tz)

    failures: list[str] = []

    for window in (Window.TODAY, Window.LAST_12H, Window.LAST_7D, Window.PATTERNS):
        start, end = window.range(local_now)
        with db.session() as session:
            truth = ReadingRepository(session).get_aggregate(
                start=start.astimezone(timezone.utc),
                end=end.astimezone(timezone.utc),
            )
        if truth is None:
            failures.append(f"{window.slug}: the benchmark range holds no readings")
            continue

        # The same arguments for_window uses, so the guard checks what is
        # actually plotted rather than a frame nothing draws.
        frame = plotted_frame(
            reports, start, end,
            floor_seconds=getattr(window, "bucket_floor_seconds", 0),
        )
        if frame is None or len(frame) == 0:
            failures.append(f"{window.slug}: nothing plotted")
            continue

        for pollutant, true_min, true_max, true_avg in (
            ("pm25", truth.pm25_min, truth.pm25_max, truth.pm25_avg),
            ("pm10", truth.pm10_min, truth.pm10_max, truth.pm10_avg),
        ):
            columns = [c for c in frame.columns if str(c).startswith(pollutant)]
            if not columns:
                failures.append(f"{window.slug}/{pollutant}: no column plotted")
                continue

            plotted_max = max(float(frame[c].max()) for c in columns)
            plotted_min = min(float(frame[c].min()) for c in columns)

            if abs(plotted_max - true_max) > TOLERANCE:
                failures.append(
                    f"{window.slug}/{pollutant}: peak lost - plotted {plotted_max:.1f}, "
                    f"true {true_max:.1f}. Carry the per-bucket max, do not plot "
                    f"averages alone."
                )
            if abs(plotted_min - true_min) > TOLERANCE:
                failures.append(
                    f"{window.slug}/{pollutant}: trough lost - plotted {plotted_min:.1f}, "
                    f"true {true_min:.1f}."
                )

            avg_column = pollutant if pollutant in frame.columns else columns[0]
            plotted_avg = float(frame[avg_column].mean())
            if true_avg and abs(plotted_avg - true_avg) / true_avg > MEAN_TOLERANCE:
                failures.append(
                    f"{window.slug}/{pollutant}: mean drifted - plotted "
                    f"{plotted_avg:.2f}, true {true_avg:.2f}"
                )

    db.engine.dispose()

    if failures:
        print("FIDELITY FAILED")
        for line in failures:
            print(f"  - {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(check())
