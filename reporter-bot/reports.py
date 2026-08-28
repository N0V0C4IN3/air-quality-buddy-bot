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

from charts import hour_heatmap, palette_for, window_chart
from html_helpers import format_caption, format_stale_card, format_status_card

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

    def status_text(self, *, now: Optional[datetime] = None) -> Optional[str]:
        now = now or datetime.now(timezone.utc)
        view = self.status(now=now)
        if view is None:
            return None
        if view.is_stale(now):
            return format_stale_card(view.observed_at, view.expected_every, now)
        return format_status_card(
            pm25=view.pm25,
            pm10=view.pm10,
            level=view.level,
            observed_at=view.observed_at,
            thresholds=self._thresholds,
            spark=view.spark,
            pm25_before=view.pm25_before,
            pm10_before=view.pm10_before,
            now=now,
        )

    def status_block(self, pm25: float, pm10: float, ts_utc: datetime) -> str:
        """Compact block for alert fan-out."""
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
            text = self._patterns_caption(df)
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
            text = format_caption(window.title, _stats(df), len(df))

        chart.name = f"{window.slug}.png"
        return Report(text=text, chart=chart)

    # ---------- internals ----------

    def _frame(self, start: datetime, end: datetime) -> pd.DataFrame:
        with self._db.session() as s:
            rows = ReadingRepository(s).get_range(
                start=start.astimezone(timezone.utc),
                end=end.astimezone(timezone.utc),
            )
            return pd.DataFrame(
                [{"timestamp": r.timestamp, "pm25": r.pm25, "pm10": r.pm10} for r in rows]
            )

    def _patterns_caption(self, df: pd.DataFrame) -> str:
        local = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(self._tz)
        by_hour = df.assign(hour=local.dt.hour).groupby("hour")["pm25"].mean()
        worst_hour = int(by_hour.idxmax())
        best_hour = int(by_hour.idxmin())
        return (
            "<b>Patterns</b> · last 7 days\n"
            f"<pre>worst hour  {worst_hour:02d}:00   {by_hour.max():.0f} µg/m³\n"
            f"best hour   {best_hour:02d}:00   {by_hour.min():.0f} µg/m³</pre>"
        )


def _stats(df: pd.DataFrame) -> dict:
    agg = df.agg({"pm25": ["min", "mean", "max"], "pm10": ["min", "mean", "max"]})
    return {
        "pm25_min": float(agg.loc["min", "pm25"]),
        "pm25_avg": float(agg.loc["mean", "pm25"]),
        "pm25_max": float(agg.loc["max", "pm25"]),
        "pm10_min": float(agg.loc["min", "pm10"]),
        "pm10_avg": float(agg.loc["mean", "pm10"]),
        "pm10_max": float(agg.loc["max", "pm10"]),
    }


def _as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
