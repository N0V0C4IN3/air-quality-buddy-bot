"""The time-frame contract: what the dashboard's query string is allowed to say."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ranges import BUCKETS, MAX_POINTS, RangeError, choose_bucket, parse_range

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 3, 14, 9, 30, tzinfo=timezone.utc)


def test_preset_spans_back_from_now():
    r = parse_range(preset="12h", now=NOW, tz=KYIV)
    assert r.span_seconds == 12 * 3600
    assert r.end == NOW


def test_today_starts_at_local_midnight_not_utc_midnight():
    """The seam the whole repo cares about: ranges are local, storage is UTC."""
    r = parse_range(preset="today", now=NOW, tz=KYIV)
    assert r.start.astimezone(KYIV).hour == 0
    assert r.start.tzinfo is timezone.utc
    assert r.start < NOW


def test_explicit_pair_wins_over_preset():
    r = parse_range(preset="7d", start="2026-03-01T00:00", end="2026-03-02T00:00",
                    now=NOW, tz=KYIV)
    assert r.label == "custom"
    assert r.span_seconds == 86400


def test_naive_custom_bounds_are_read_as_local_time():
    r = parse_range(start="2026-03-01T00:00", end="2026-03-01T06:00", now=NOW, tz=KYIV)
    assert r.start.astimezone(KYIV).hour == 0
    assert r.start.utcoffset().total_seconds() == 0


def test_offsets_and_z_are_honoured():
    r = parse_range(start="2026-03-01T00:00:00Z", end="2026-03-01T02:00:00+00:00",
                    now=NOW, tz=KYIV)
    assert r.start == datetime(2026, 3, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("kwargs", [
    {"preset": "nope"},
    {"start": "2026-03-01T00:00"},                                   # half a pair
    {"start": "2026-03-02T00:00", "end": "2026-03-01T00:00"},        # backwards
    {"start": "not-a-date", "end": "2026-03-01T00:00"},
])
def test_bad_ranges_are_refused(kwargs):
    with pytest.raises(RangeError):
        parse_range(now=NOW, tz=KYIV, **kwargs)


def test_raw_means_one_bucket_per_reading():
    seconds, name = choose_bucket(span_seconds=3600, requested="raw",
                                  reading_interval_seconds=300)
    assert (seconds, name) == (300, "raw")


def test_a_request_over_the_point_budget_is_widened_not_served():
    """Asking for raw samples across 90 days must not return 26k points."""
    span = 90 * 86400
    seconds, name = choose_bucket(span_seconds=span, requested="raw",
                                  reading_interval_seconds=300)
    assert name != "raw"
    assert span / seconds <= MAX_POINTS


def test_auto_picks_the_finest_width_that_fits():
    seconds, name = choose_bucket(span_seconds=12 * 3600, requested=None,
                                  reading_interval_seconds=300)
    assert name == "raw"
    assert seconds == 300


def test_the_widest_bucket_is_the_last_resort():
    seconds, _ = choose_bucket(span_seconds=10 * 365 * 86400, requested=None,
                               reading_interval_seconds=300)
    assert seconds == BUCKETS["1d"]


def test_unknown_bucket_is_refused():
    with pytest.raises(RangeError):
        choose_bucket(span_seconds=3600, requested="2s", reading_interval_seconds=300)
