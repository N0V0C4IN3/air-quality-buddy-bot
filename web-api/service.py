"""Everything the dashboard can ask about the readings table.

One module between HTTP and SQL. Handlers pass a `TimeRange` and get back
JSON-ready dicts; nothing above this line knows about sessions, UTC round-trips
or bucket arithmetic, and nothing below it knows about HTTP.

Series come back columnar - parallel arrays rather than a list of objects -
because that is the shape uPlot draws and it is a fraction of the bytes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from common.air_quality import Thresholds
from common.db import Bucket, ChatRepository, Database, ReadingRepository

from ranges import MAX_POINTS, TimeRange, choose_bucket

# A reading older than this many intervals means the reader has gone quiet.
# Same rule the bot's status card uses.
STALE_INTERVALS = 3

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class DashboardService:
    def __init__(
        self,
        db: Database,
        tz,
        thresholds: Thresholds,
        *,
        reading_interval_seconds: int = 300,
        retention_days: int = 90,
        max_points: int = MAX_POINTS,
    ) -> None:
        self._db = db
        self._tz = tz
        self._thresholds = thresholds
        self._interval = reading_interval_seconds
        self._retention_days = retention_days
        self._max_points = max_points

    # ---------- meta ----------

    def meta(self, *, chat_id: Optional[str] = None) -> dict:
        """Everything the page needs to render itself without hardcoding.

        Thresholds included: `common.air_quality.Thresholds` owns them, and a
        band drawn from a number baked into JavaScript is a band that silently
        stops matching the alerts.
        """
        t = self._thresholds
        return {
            "timezone": getattr(self._tz, "key", str(self._tz)),
            "reading_interval_seconds": self._interval,
            "retention_days": self._retention_days,
            "max_points": self._max_points,
            "thresholds": {
                "pm25": {"warn": t.pm25_warn, "err": t.pm25_err},
                "pm10": {"warn": t.pm10_warn, "err": t.pm10_err},
            },
            "theme": self._theme(chat_id),
        }

    def set_theme(self, chat_id: str, theme: str) -> dict:
        """Persist the viewer's light/dark choice on their chat row.

        The bot already stores a per-chat theme for its PNGs; the dashboard
        writes the same column so the two agree instead of each keeping half a
        preference.
        """
        if theme not in ("light", "dark"):
            raise ValueError(f"unknown theme {theme!r}")
        with self._db.session() as s:
            ChatRepository(s).set_theme(chat_id, theme)
        return {"theme": theme}

    # ---------- live ----------

    def latest(self, *, now: Optional[datetime] = None) -> Optional[dict]:
        now = now or datetime.now(timezone.utc)
        with self._db.session() as s:
            latest = ReadingRepository(s).get_latest()
            if latest is None:
                return None
            observed_at = _utc(latest.timestamp)
            pm25, pm10 = latest.pm25, latest.pm10

        level = self._thresholds.level(pm25, pm10)
        age = (now - observed_at).total_seconds()
        return {
            "pm25": pm25,
            "pm10": pm10,
            "level": level.value,
            "level_label": level.label,
            "observed_at": observed_at.isoformat(),
            "age_seconds": age,
            "stale": age > self._interval * STALE_INTERVALS,
            "expected_every": self._interval,
        }

    # ---------- series ----------

    def series(self, rng: TimeRange, *, bucket: Optional[str] = None) -> dict:
        seconds, name = choose_bucket(
            span_seconds=rng.span_seconds,
            requested=bucket,
            reading_interval_seconds=self._interval,
            max_points=self._max_points,
        )
        buckets = self._buckets(rng, seconds)
        return {
            "range": _range_json(rng),
            "bucket": {"name": name, "seconds": seconds},
            "count": sum(b.count for b in buckets),
            "t": [int(b.start.timestamp()) for b in buckets],
            "pm25": {
                "avg": [round(b.pm25_avg, 2) for b in buckets],
                "min": [round(b.pm25_min, 2) for b in buckets],
                "max": [round(b.pm25_max, 2) for b in buckets],
            },
            "pm10": {
                "avg": [round(b.pm10_avg, 2) for b in buckets],
                "min": [round(b.pm10_min, 2) for b in buckets],
                "max": [round(b.pm10_max, 2) for b in buckets],
            },
        }

    # ---------- summary ----------

    def summary(self, rng: TimeRange) -> dict:
        t = self._thresholds
        # One session for the whole answer: the extremes, the level split and
        # the hourly buckets were three round trips across two sessions to
        # describe a single range.
        with self._db.session() as s:
            repo = ReadingRepository(s)
            agg, levels = repo.summarise(
                start=rng.start,
                end=rng.end,
                pm25_warn=t.pm25_warn,
                pm10_warn=t.pm10_warn,
                pm25_err=t.pm25_err,
                pm10_err=t.pm10_err,
            )
            if agg is None:
                return {"range": _range_json(rng), "count": 0, "empty": True}
            hourly = repo.get_buckets(
                start=rng.start, end=rng.end, bucket_seconds=3600,
            )
        return {
            "range": _range_json(rng),
            "count": agg.count,
            "empty": False,
            "first": agg.first.isoformat(),
            "last": agg.last.isoformat(),
            "pm25": {"avg": round(agg.pm25_avg, 1),
                     "min": round(agg.pm25_min, 1),
                     "max": round(agg.pm25_max, 1)},
            "pm10": {"avg": round(agg.pm10_avg, 1),
                     "min": round(agg.pm10_min, 1),
                     "max": round(agg.pm10_max, 1)},
            "levels": levels,
            "level_share": _share(levels),
            "worst_hour": _extreme_hour(hourly, self._tz, worst=True),
            "best_hour": _extreme_hour(hourly, self._tz, worst=False),
        }

    # ---------- patterns ----------

    def patterns(self, rng: TimeRange) -> dict:
        """Hour-of-day by weekday means - the heatmap, as numbers.

        Built from hourly buckets rather than raw rows: a week is 168 of them,
        and the local-hour grouping has to happen in Python anyway because the
        timezone lives in config, not in the database.
        """
        hourly = self._buckets(rng, 3600)
        grid_25: dict[tuple[int, int], list[float]] = defaultdict(list)
        grid_10: dict[tuple[int, int], list[float]] = defaultdict(list)
        hours_25: dict[int, list[float]] = defaultdict(list)
        hours_10: dict[int, list[float]] = defaultdict(list)

        for b in hourly:
            local = b.start.astimezone(self._tz)
            cell = (local.weekday(), local.hour)
            grid_25[cell].append(b.pm25_avg)
            grid_10[cell].append(b.pm10_avg)
            hours_25[local.hour].append(b.pm25_avg)
            hours_10[local.hour].append(b.pm10_avg)

        return {
            "range": _range_json(rng),
            "days": _DAYS,
            "hours": list(range(24)),
            "pm25": {
                "grid": _grid(grid_25),
                "by_hour": [_mean(hours_25.get(h, [])) for h in range(24)],
            },
            "pm10": {
                "grid": _grid(grid_10),
                "by_hour": [_mean(hours_10.get(h, [])) for h in range(24)],
            },
        }

    # ---------- internals ----------

    def _buckets(self, rng: TimeRange, seconds: int) -> list[Bucket]:
        with self._db.session() as s:
            return ReadingRepository(s).get_buckets(
                start=rng.start, end=rng.end, bucket_seconds=seconds
            )

    def _theme(self, chat_id: Optional[str]) -> str:
        if not chat_id:
            return "light"
        with self._db.session() as s:
            return ChatRepository(s).get_theme(chat_id)


# ---------- helpers ----------

def _range_json(rng: TimeRange) -> dict:
    return {"from": rng.start.isoformat(), "to": rng.end.isoformat(), "label": rng.label}


def _share(levels: dict[str, int]) -> dict[str, float]:
    total = sum(levels.values())
    if not total:
        return {k: 0.0 for k in levels}
    return {k: round(100.0 * v / total, 1) for k, v in levels.items()}


def _grid(cells: dict[tuple[int, int], list[float]]) -> list[list[Optional[float]]]:
    """A 7x24 matrix of means, with None where the range held no readings."""
    return [
        [_mean(cells.get((day, hour), [])) for hour in range(24)]
        for day in range(7)
    ]


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _extreme_hour(hourly: list[Bucket], tz, *, worst: bool) -> Optional[dict]:
    """The single dirtiest or cleanest hour in the range, in local time."""
    if not hourly:
        return None
    pick = max(hourly, key=lambda b: b.pm25_avg) if worst \
        else min(hourly, key=lambda b: b.pm25_avg)
    local = pick.start.astimezone(tz)
    return {
        "at": pick.start.isoformat(),
        "local": local.strftime("%a %d %b %H:%M"),
        "pm25": round(pick.pm25_avg, 1),
        "pm10": round(pick.pm10_avg, 1),
    }


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
