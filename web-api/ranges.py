"""Time ranges and bucket widths.

Pure module: no DB, no FastAPI, no env. The dashboard's whole "choose a time
frame" contract lives here, so the query string is parsed and validated in one
place and the handlers only ever see a `TimeRange`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Named widths the UI offers. "raw" is not a fixed number of seconds - it means
# "one bucket per reading", which is the reader's interval, so the pipeline
# never needs a special case for unaggregated data.
BUCKETS: dict[str, Optional[int]] = {
    "raw": None,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}

# Above this a line chart is drawing more points than the screen has pixels,
# and the browser pays for all of them.
MAX_POINTS = 1500

# Ranges the buttons offer, as a span back from now. `today` is special: it
# starts at local midnight, so it is expressed as a span of None.
PRESETS: dict[str, Optional[timedelta]] = {
    "1h": timedelta(hours=1),
    "12h": timedelta(hours=12),
    "today": None,
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}

DEFAULT_PRESET = "24h"


class RangeError(ValueError):
    """The caller asked for a range that cannot be served."""


@dataclass(frozen=True)
class TimeRange:
    """A half-open [start, end) interval, always in UTC."""
    start: datetime
    end: datetime
    label: str

    @property
    def span_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def parse_range(
    *,
    preset: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    now: datetime,
    tz,
) -> TimeRange:
    """Build a range from either a preset name or an explicit start/end pair.

    An explicit pair wins; both forms come back as UTC, whatever the caller
    sent, because that is what the readings table stores.
    """
    if start or end:
        if not (start and end):
            raise RangeError("from and to must be given together")
        s, e = _parse_instant(start, "from", tz), _parse_instant(end, "to", tz)
        if e <= s:
            raise RangeError("to must be after from")
        return TimeRange(start=s, end=e, label="custom")

    name = preset or DEFAULT_PRESET
    if name not in PRESETS:
        raise RangeError(f"unknown range {name!r}; expected one of {', '.join(PRESETS)}")

    local_now = now.astimezone(tz)
    span = PRESETS[name]
    if span is None:  # today
        midnight = datetime(local_now.year, local_now.month, local_now.day, tzinfo=tz)
        return TimeRange(
            start=midnight.astimezone(timezone.utc),
            end=local_now.astimezone(timezone.utc),
            label=name,
        )
    return TimeRange(
        start=(local_now - span).astimezone(timezone.utc),
        end=local_now.astimezone(timezone.utc),
        label=name,
    )


def choose_bucket(
    *,
    span_seconds: float,
    requested: Optional[str],
    reading_interval_seconds: int,
    max_points: int = MAX_POINTS,
) -> tuple[int, str]:
    """Pick the bucket width for a span, as (seconds, name).

    `requested` is honoured only while it stays under the point budget: asking
    for raw samples across ninety days is a request the server should answer
    with the finest width that still fits, not with 26k points.
    """
    floor = max(1, reading_interval_seconds)
    widths = [(name, w or floor) for name, w in BUCKETS.items()]

    if requested is not None:
        if requested not in BUCKETS:
            raise RangeError(
                f"unknown bucket {requested!r}; expected one of {', '.join(BUCKETS)}"
            )
        seconds = BUCKETS[requested] or floor
        if span_seconds / seconds <= max_points:
            return seconds, requested

    for name, seconds in widths:
        if span_seconds / seconds <= max_points:
            return seconds, name

    name, seconds = widths[-1]
    return seconds, name


def _parse_instant(raw: str, field: str, tz) -> datetime:
    """Accept ISO 8601, with or without an offset; a bare one means local time."""
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RangeError(f"{field}={raw!r} is not an ISO 8601 timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)
