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
            latest = repo.get_latest()
            if not latest:
                return None
            recent = list(repo.get_range(start=now - timedelta(hours=1), end=now))

        observed_at = _as_utc(latest.timestamp)
        earliest = recent[0] if recent else None
        spark = [r.pm25 for r in recent][-SPARK_POINTS:]

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

    def alert_report(self, pm25: float, pm10: float, ts_utc: datetime, *,
                     theme: str = "light", now: Optional[datetime] = None) -> Report:
        """One alert as the same card the status button sends.

        The values come from the alert payload, not from a fresh query: the
        message must describe the reading that tripped the threshold, even if a
        newer one has landed since. The sparkline and the hour-ago comparison do
        come from the DB — they are context, not the subject.
        """
        now = now or datetime.now(timezone.utc)
        level = self._thresholds.level(pm25, pm10)
        spark, pm25_before, pm10_before = self._recent(now)

        chart = status_card(
            pm25=pm25,
            pm10=pm10,
            level=level.value,
            thresholds=self._thresholds,
            freshness=f"reading at {ts_utc.astimezone(self._tz):%H:%M}",
            spark=spark,
            pm25_before=pm25_before,
            pm10_before=pm10_before,
            palette=palette_for(theme),
        )
        chart.name = "alert.png"
        return Report(text="", chart=chart)

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
        df = self._frame(start, end)

        if df.empty:
            return Report(text=f"No data for {window.title.lower()}.")

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
                smooth_window=window.smooth_window,
                multiday=window.multiday,
            )
            text = format_caption(window.title, len(df))

        chart.name = f"{window.slug}.png"
        return Report(text=text, chart=chart)

    # ---------- internals ----------

    def _recent(self, now: datetime):
        """The last hour as (sparkline points, pm25 an hour ago, pm10 an hour ago)."""
        with self._db.session() as s:
            recent = list(ReadingRepository(s).get_range(
                start=now - timedelta(hours=1), end=now,
            ))
        if not recent:
            return [], None, None
        earliest = recent[0]
        return ([r.pm25 for r in recent][-SPARK_POINTS:],
                earliest.pm25, earliest.pm10)

    def _frame(self, start: datetime, end: datetime) -> pd.DataFrame:
        with self._db.session() as s:
            rows = ReadingRepository(s).get_range(
                start=start.astimezone(timezone.utc),
                end=end.astimezone(timezone.utc),
            )
            return pd.DataFrame(
                [{"timestamp": r.timestamp, "pm25": r.pm25, "pm10": r.pm10} for r in rows]
            )


def _as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
