"""The half of `ReadingRepository` that SQLite cannot check.

`get_buckets` has one dialect-specific line - the epoch expression is
`strftime('%s', ...)` on SQLite and `extract('epoch', ...)` on Postgres - and
the whole suite runs on SQLite, so the branch production actually executes is
never touched by `python -m pytest` on a laptop.

That was tolerable while only the dashboard bucketed. It stopped being
tolerable when the bot's chart cards started going through the same call: a
mistake in the Postgres branch now breaks every card the bot sends, not one
panel of a web page.

These tests skip unless `DATABASE_URL` names a Postgres database. CI's
`migrations` job already stands one up and runs `alembic upgrade head` against
it, which is where they are meant to run.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from common.air_quality import Thresholds
from common.db import Database, Reading, ReadingRepository

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    "postgres" not in DATABASE_URL,
    reason="needs a Postgres DATABASE_URL; the rest of the suite runs on SQLite",
)

# Far enough from anything real that a stray row cannot collide with it.
START = datetime(2001, 1, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def pg():
    db = Database(url=DATABASE_URL)
    with db.session() as s:
        s.query(Reading).filter(
            Reading.timestamp >= START,
            Reading.timestamp < START + timedelta(days=1),
        ).delete()
    yield db
    with db.session() as s:
        s.query(Reading).filter(
            Reading.timestamp >= START,
            Reading.timestamp < START + timedelta(days=1),
        ).delete()
    db.engine.dispose()


@pytest.fixture
def seeded(pg):
    """Twelve readings, one every five minutes, climbing 10 -> 120."""
    with pg.session() as s:
        repo = ReadingRepository(s)
        for i in range(12):
            repo.add(
                pm25=10.0 * (i + 1),
                pm10=5.0 * (i + 1),
                timestamp=START + timedelta(minutes=5 * i),
            )
    return pg


def test_buckets_group_on_the_postgres_epoch_expression(seeded):
    with seeded.session() as s:
        buckets = ReadingRepository(s).get_buckets(
            start=START, end=START + timedelta(hours=1), bucket_seconds=900,
        )

    # Four quarter-hours, three readings each.
    assert [b.count for b in buckets] == [3, 3, 3, 3]
    assert [b.start for b in buckets] == [
        START + timedelta(minutes=15 * i) for i in range(4)
    ]


def test_buckets_carry_the_extremes_not_just_the_mean(seeded):
    """The peak is what the chart's band is drawn from - if this is wrong on
    Postgres, every card understates a spike."""
    with seeded.session() as s:
        buckets = ReadingRepository(s).get_buckets(
            start=START, end=START + timedelta(hours=1), bucket_seconds=900,
        )

    assert buckets[0].pm25_min == 10.0
    assert buckets[0].pm25_max == 30.0
    assert buckets[0].pm25_avg == pytest.approx(20.0)
    assert max(b.pm25_max for b in buckets) == 120.0


def test_buckets_align_to_the_epoch_not_to_the_requested_start(seeded):
    """Panning a chart must not reshuffle the points under it."""
    with seeded.session() as s:
        repo = ReadingRepository(s)
        whole = repo.get_buckets(
            start=START, end=START + timedelta(hours=1), bucket_seconds=900,
        )
        shifted = repo.get_buckets(
            start=START + timedelta(minutes=5),
            end=START + timedelta(hours=1),
            bucket_seconds=900,
        )

    assert [b.start for b in shifted] == [b.start for b in whole]


def test_summarise_matches_the_two_queries_it_replaced(seeded):
    t = Thresholds()
    window = dict(start=START, end=START + timedelta(hours=1))
    limits = dict(
        pm25_warn=t.pm25_warn, pm10_warn=t.pm10_warn,
        pm25_err=t.pm25_err, pm10_err=t.pm10_err,
    )
    with seeded.session() as s:
        repo = ReadingRepository(s)
        agg, levels = repo.summarise(**window, **limits)

        assert agg == repo.get_aggregate(**window)
        assert levels == repo.count_by_level(**window, **limits)
        assert sum(levels.values()) == 12


def test_recent_and_first_since_order_correctly(seeded):
    """The status card reads the newest reading off the end of get_recent."""
    with seeded.session() as s:
        repo = ReadingRepository(s)
        recent = repo.get_recent(4)
        first = repo.get_first_since(START)

    assert [r.pm25 for r in recent] == [90.0, 100.0, 110.0, 120.0]
    assert recent[-1].pm25 == 120.0
    assert first.pm25 == 10.0
