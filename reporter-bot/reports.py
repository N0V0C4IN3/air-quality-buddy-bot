"""Turns a time window into something the bot can send.

Owns the query, the UTC round-trip, the aggregation, the caption and the chart.
Handlers know one call and one enum.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from io import BytesIO
from typing import Optional

import pandas as pd

from common.air_quality import Level, Thresholds
from common.db import Database, ReadingRepository

from charts import hour_heatmap, palette_for, status_card, window_chart
from html_helpers import format_caption, format_stale_card, relative_time

# How many recent readings the /status sparkline draws.
SPARK_POINTS = 12
# A reading older than this many intervals means the reader has gone quiet.
STALE_INTERVALS = 3

# One point per pixel, at most. A card is 7 inches at charts.DPI, of which the
# plot area is roughly five sixths - about 730 px at 125 dpi - so anything past
# ~800 points is strokes the anti-aliaser averages away again.
#
# Deliberately tighter than the dashboard's 1500: that budget is sized for a
# browser window, and this one is sized for the PNG it actually draws.
MAX_POINTS = 800

# Widths the bot reduces to, narrowest first.
BUCKET_WIDTHS = (300, 900, 3600, 21600, 86400)


def bucket_seconds(span_seconds: float, interval_seconds: int,
                   max_points: int = MAX_POINTS) -> Optional[int]:
    """Narrowest bucket that keeps a card under the point budget; None for raw.

    `ranges.choose_bucket` is the same rule on the web-api side. The bot never
    had one, which is why a seven-day card pulled twenty thousand rows into
    pandas to draw a picture a thousand pixels wide.
    """
    floor = max(1, interval_seconds)
    if span_seconds / floor <= max_points:
        return None
    for width in BUCKET_WIDTHS:
        if width >= floor and span_seconds / width <= max_points:
            return width
    return BUCKET_WIDTHS[-1]


class Window(Enum):
    TODAY = "Today"
    LAST_12H = "Last 12h"
    LAST_7D = "Last 7d"
    PATTERNS = "Patterns"

    @property
    def title(self) -> str:
        return self.value

    @property
    def slug(self) -> str:
        """Stable id for callback data — must not change once buttons are live."""
        return self.name.lower()

    @classmethod
    def from_slug(cls, slug: str) -> "Window":
        return cls[slug.upper()]

    @property
    def is_heatmap(self) -> bool:
        return self is Window.PATTERNS

    @property
    def multiday(self) -> bool:
        return self in (Window.LAST_7D, Window.PATTERNS)

    @property
    def bucket_floor_seconds(self) -> int:
        """Finest resolution this window can actually use, 0 for no floor.

        The heatmap groups readings into hour-of-day cells, so anything finer
        than an hour is averaged away again on arrival. Asking SQL for hourly
        buckets is both less work and more correct: a count-weighted mean per
        hour rather than pandas averaging four bucket means as though they
        stood for equal numbers of readings.
        """
        return 3600 if self is Window.PATTERNS else 0

    @property
    def smooth_window(self) -> int:
        """Points in the rolling mean; 0 leaves the raw line alone."""
        return 13 if self is Window.LAST_7D else 0

    def range(self, now: datetime) -> tuple[datetime, datetime]:
        """Start/end in the caller's timezone (`now` carries it)."""
        if self is Window.TODAY:
            start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
            return start, start + timedelta(days=1)
        if self is Window.LAST_12H:
            return now - timedelta(hours=12), now
        return now - timedelta(days=7), now


@dataclass(frozen=True)
class Report:
    text: str
    chart: Optional[BytesIO] = None

    @property
    def is_empty(self) -> bool:
        return self.chart is None


@dataclass(frozen=True)
class StatusView:
    pm25: float
    pm10: float
    level: Level
    observed_at: datetime
    pm25_before: Optional[float]
    pm10_before: Optional[float]
    spark: list[float]
    expected_every: int

    def age(self, now: Optional[datetime] = None) -> timedelta:
        return (now or datetime.now(timezone.utc)) - self.observed_at

    def is_stale(self, now: Optional[datetime] = None) -> bool:
        return self.age(now).total_seconds() > self.expected_every * STALE_INTERVALS


class ReadingReports:
    def __init__(self, db: Database, tz, thresholds: Thresholds,
                 *, reading_interval_seconds: int = 300) -> None:
        self._db = db
        self._tz = tz
        self._thresholds = thresholds
        self._interval = reading_interval_seconds

    # ---------- status ----------

    def status(self, *, now: Optional[datetime] = None) -> Optional[StatusView]:
        now = now or datetime.now(timezone.utc)
        with self._db.session() as s:
            repo = ReadingRepository(s)
            # Only the sparkline's own points, not the whole hour behind them.
            recent = repo.get_recent(SPARK_POINTS)
            if not recent:
                return None
            earliest = repo.get_first_since(now - timedelta(hours=1))

        latest = recent[-1]
        observed_at = _as_utc(latest.timestamp)
        spark = [r.pm25 for r in recent]

        return StatusView(
            pm25=latest.pm25,
            pm10=latest.pm10,
            level=self._thresholds.level(latest.pm25, latest.pm10),
            observed_at=observed_at,
            pm25_before=earliest.pm25 if earliest else None,
            pm10_before=earliest.pm10 if earliest else None,
            spark=spark,
            expected_every=self._interval,
        )

    def status_report(self, *, now: Optional[datetime] = None,
                      theme: str = "light") -> Optional[Report]:
        """The latest reading as a card PNG.

        A quiet sensor stays text: a dead reader must not have to wait on a
        render, and there is nothing to draw.
        """
        now = now or datetime.now(timezone.utc)
        view = self.status(now=now)
        if view is None:
            return None
        if view.is_stale(now):
            return Report(text=format_stale_card(view.observed_at,
                                                 view.expected_every, now))

        chart = status_card(
            pm25=view.pm25,
            pm10=view.pm10,
            level=view.level.value,
            thresholds=self._thresholds,
            freshness=f"updated {relative_time(view.observed_at, now)}",
            spark=view.spark,
            pm25_before=view.pm25_before,
            pm10_before=view.pm10_before,
            palette=palette_for(theme),
        )
        chart.name = "status.png"
        # No caption: the card names the level itself, and a caption under it
        # only repeats what the PNG already says.
        return Report(text="", chart=chart)

    def alert_reports(self, pm25: float, pm10: float, ts_utc: datetime, *,
                      themes, now: Optional[datetime] = None) -> dict:
        """One card per theme, from a single look at the recent readings.

        The values come from the alert payload, not from a fresh query: the
        message must describe the reading that tripped the threshold, even if
        a newer one has landed since. The sparkline and the hour-ago
        comparison do come from the DB - they are context, not the subject -
        and every theme wants the identical copy of them, so they are fetched
        once for the whole fan-out rather than once per card.
        """
        now = now or datetime.now(timezone.utc)
        level = self._thresholds.level(pm25, pm10)
        spark, pm25_before, pm10_before = self._recent(now)
        freshness = f"reading at {ts_utc.astimezone(self._tz):%H:%M}"

        cards = {}
        for theme in themes:
            chart = status_card(
                pm25=pm25,
                pm10=pm10,
                level=level.value,
                thresholds=self._thresholds,
                freshness=freshness,
                spark=spark,
                pm25_before=pm25_before,
                pm10_before=pm10_before,
                palette=palette_for(theme),
            )
            chart.name = "alert.png"
            cards[theme] = Report(text="", chart=chart)
        return cards

    def alert_report(self, pm25: float, pm10: float, ts_utc: datetime, *,
                     theme: str = "light", now: Optional[datetime] = None) -> Report:
        """One alert as the same card the status button sends."""
        return self.alert_reports(
            pm25, pm10, ts_utc, themes=(theme,), now=now,
        )[theme]

    def alert_text(self, pm25: float, pm10: float, ts_utc: datetime) -> str:
        """Text fallback — used when a render fails; an alert must still arrive."""
        from html_helpers import format_status_block

        return format_status_block(
            pm25, pm10, ts_utc, level=self._thresholds.level(pm25, pm10), tz=self._tz
        )

    # ---------- windows ----------

    def for_window(self, window: Window, *, now: Optional[datetime] = None,
                   theme: str = "light") -> Report:
        start, end = window.range(now or datetime.now(self._tz))
        df = self._frame(start, end, floor_seconds=window.bucket_floor_seconds)

        if df.empty:
            return Report(text=f"No data for {window.title.lower()}.")

        # The caption counts readings, not plotted points - "672 samples" for a
        # week of 30-second data would be a lie about how much was measured.
        samples = int(df["n"].sum())
        reduced = bool((df["n"] > 1).any())

        palette = palette_for(theme)
        if window.is_heatmap:
            chart = hour_heatmap(df, tz=self._tz, palette=palette)
            text = f"<b>{window.title}</b> · last 7 days"
        else:
            chart = window_chart(
                df,
                title=window.title,
                thresholds=self._thresholds,
                tz=self._tz,
                palette=palette,
                # Bucketing already averages. The rolling mean was tuned for
                # raw 30-second samples; on top of 15-minute buckets it would
                # smear three hours together.
                smooth_window=0 if reduced else window.smooth_window,
                multiday=window.multiday,
            )
            text = format_caption(window.title, samples)

        chart.name = f"{window.slug}.png"
        return Report(text=text, chart=chart)

    # ---------- internals ----------

    def _recent(self, now: datetime):
        """The last hour as (sparkline points, pm25 an hour ago, pm10 an hour ago).

        Two narrow queries rather than one wide one: the card draws twelve
        points and compares against a single reading an hour back, so pulling
        the intervening hour was work nothing ever looked at.
        """
        with self._db.session() as s:
            repo = ReadingRepository(s)
            recent = repo.get_recent(SPARK_POINTS)
            earliest = repo.get_first_since(now - timedelta(hours=1))
        if not recent:
            return [], None, None
        if earliest is None:
            return [r.pm25 for r in recent], None, None
        return ([r.pm25 for r in recent], earliest.pm25, earliest.pm10)

    def _frame(self, start: datetime, end: datetime,
               *, floor_seconds: int = 0) -> pd.DataFrame:
        """The plotted data for a range, reduced in SQL when it is too dense.

        The columns are the same either way, so nothing downstream needs to
        know which path produced them: `pm25`/`pm10` carry the representative
        value, `*_min`/`*_max` the extremes behind it, and `n` how many
        readings each point stands for. On the raw path the extremes are the
        value itself and `n` is 1.

        Carrying the extremes is not decoration. Plotting bucket averages alone
        would hide the spike an alert fired on - a five-minute mean of one
        90 ug/m3 minute and nine quiet ones reads as a comfortable 15.
        """
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        width = bucket_seconds((end - start).total_seconds(), self._interval)
        if floor_seconds:
            width = max(width or 0, floor_seconds)

        with self._db.session() as s:
            repo = ReadingRepository(s)
            if width is None:
                return pd.DataFrame([
                    {"timestamp": r.timestamp,
                     "pm25": r.pm25, "pm25_min": r.pm25, "pm25_max": r.pm25,
                     "pm10": r.pm10, "pm10_min": r.pm10, "pm10_max": r.pm10,
                     "n": 1}
                    for r in repo.get_range(start=start_utc, end=end_utc)
                ])
            buckets = repo.get_buckets(
                start=start_utc, end=end_utc, bucket_seconds=width,
            )

        return pd.DataFrame([
            {"timestamp": b.start,
             "pm25": b.pm25_avg, "pm25_min": b.pm25_min, "pm25_max": b.pm25_max,
             "pm10": b.pm10_avg, "pm10_min": b.pm10_min, "pm10_max": b.pm10_max,
             "n": b.count}
            for b in buckets
        ])


def _as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
