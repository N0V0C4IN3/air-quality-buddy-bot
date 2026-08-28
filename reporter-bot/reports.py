# reports.py
"""Turns a time window into something the bot can send.

Owns the query, the UTC round-trip, the aggregation, the stats table and the
chart. Handlers know one call and one enum.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from io import BytesIO
from typing import Optional

import pandas as pd

from common.air_quality import Thresholds
from common.db import Database, ReadingRepository

from charts import df_to_line_chart_png
from html_helpers import format_status_block, make_stats_table


class Window(Enum):
    TODAY = "Today"
    LAST_12H = "Last 12h"
    LAST_7D = "Last 7d"

    @property
    def title(self) -> str:
        return self.value

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


class ReadingReports:
    def __init__(self, db: Database, tz, thresholds: Thresholds) -> None:
        self._db = db
        self._tz = tz
        self._thresholds = thresholds

    def latest(self) -> Optional[str]:
        """The status block for the newest reading, or None if there is none."""
        with self._db.session() as s:
            r = ReadingRepository(s).get_latest()
        if not r:
            return None
        return self.status_block(r.pm25, r.pm10, r.timestamp)

    def status_block(self, pm25: float, pm10: float, ts_utc: datetime) -> str:
        return format_status_block(
            pm25, pm10, ts_utc, level=self._thresholds.level(pm25, pm10), tz=self._tz
        )

    def for_window(self, window: Window, *, now: Optional[datetime] = None) -> Report:
        start, end = window.range(now or datetime.now(self._tz))

        with self._db.session() as s:
            rows = ReadingRepository(s).get_range(
                start=start.astimezone(timezone.utc),
                end=end.astimezone(timezone.utc),
            )
            df = pd.DataFrame(
                [{"timestamp": r.timestamp, "pm25": r.pm25, "pm10": r.pm10} for r in rows]
            )

        if df.empty:
            return Report(text=f"No data for {window.title.lower()}.")

        stats = df.agg({"pm25": ["min", "mean", "max"], "pm10": ["min", "mean", "max"]}).round(1)
        text = make_stats_table(
            stats.loc["min", "pm25"], stats.loc["mean", "pm25"], stats.loc["max", "pm25"],
            stats.loc["min", "pm10"], stats.loc["mean", "pm10"], stats.loc["max", "pm10"],
            len(df),
            title=window.title,
        )

        chart = df_to_line_chart_png(df, title=window.title, tz=self._tz)
        chart.name = f"{window.title}.png"
        return Report(text=text, chart=chart)
