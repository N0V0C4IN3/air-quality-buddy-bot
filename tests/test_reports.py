from datetime import datetime, timedelta, timezone

import pytest

from common.air_quality import Level
from common.db import ReadingRepository
from reports import ReadingReports, Report, Window

KYIV = timezone(timedelta(hours=3))  # fixed offset: no tz database needed
PNG_MAGIC = bytes([0x89]) + b"PNG"


@pytest.fixture
def reports(db, thresholds):
    return ReadingReports(db, KYIV, thresholds)


def add_reading(db, when_utc, pm25=10.0, pm10=20.0, status="ok"):
    with db.session() as s:
        ReadingRepository(s).add(pm25=pm25, pm10=pm10, status=status, timestamp=when_utc)


# ---------- windows ----------

def test_today_starts_at_local_midnight():
    now = datetime(2026, 8, 27, 14, 30, tzinfo=KYIV)
    start, end = Window.TODAY.range(now)

    assert (start.year, start.month, start.day) == (2026, 8, 27)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert end == start + timedelta(days=1)
    assert start <= now < end


def test_local_midnight_is_not_utc_midnight():
    """The bug this seam exists to prevent."""
    start, _ = Window.TODAY.range(datetime(2026, 8, 27, 14, 30, tzinfo=KYIV))
    as_utc = start.astimezone(timezone.utc)

    assert (as_utc.day, as_utc.hour) == (26, 21)


@pytest.mark.parametrize(
    "window, span", [(Window.LAST_12H, timedelta(hours=12)), (Window.LAST_7D, timedelta(days=7))]
)
def test_rolling_windows_end_now(window, span):
    now = datetime(2026, 8, 27, 14, 30, tzinfo=KYIV)
    start, end = window.range(now)

    assert end == now
    assert start == now - span


def test_window_titles():
    assert [w.title for w in Window] == ["Today", "Last 12h", "Last 7d", "Patterns"]


def test_window_slugs_round_trip():
    """Slugs travel in callback data on live buttons — they must survive."""
    for window in Window:
        assert Window.from_slug(window.slug) is window


def test_only_the_seven_day_window_is_smoothed():
    assert Window.LAST_7D.smooth_window > 1
    assert Window.TODAY.smooth_window == 0
    assert Window.LAST_12H.smooth_window == 0


def test_patterns_is_the_only_heatmap():
    assert Window.PATTERNS.is_heatmap
    assert not any(w.is_heatmap for w in Window if w is not Window.PATTERNS)


# ---------- reports ----------

def test_empty_window_names_that_window(reports):
    """Regression: every window used to answer 'No data for today.'"""
    now = datetime(2026, 8, 27, 14, 30, tzinfo=KYIV)

    messages = {w: reports.for_window(w, now=now).text for w in Window}

    assert messages[Window.TODAY] == "No data for today."
    assert messages[Window.LAST_12H] == "No data for last 12h."
    assert messages[Window.LAST_7D] == "No data for last 7d."
    assert len(set(messages.values())) == len(Window)


def test_empty_report_has_no_chart(reports):
    report = reports.for_window(Window.TODAY, now=datetime(2026, 8, 27, 12, 0, tzinfo=KYIV))
    assert report.is_empty and report.chart is None


def test_report_carries_stats_and_a_png(reports, db):
    now = datetime(2026, 8, 27, 14, 30, tzinfo=KYIV)
    for hour, pm25 in [(9, 10.0), (10, 20.0), (11, 30.0)]:
        add_reading(db, datetime(2026, 8, 27, hour, 0, tzinfo=timezone.utc), pm25=pm25)

    report = reports.for_window(Window.TODAY, now=now)

    assert not report.is_empty
    assert "Today" in report.text
    assert "3 samples" in report.text
    assert "<pre>" not in report.text          # the stats live in the PNG now
    assert report.chart.getvalue().startswith(PNG_MAGIC)
    assert report.chart.name == "today.png"   # slug, stable in callback data


def test_window_excludes_readings_outside_it(reports, db):
    now = datetime(2026, 8, 27, 14, 30, tzinfo=KYIV)
    add_reading(db, datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc))  # yesterday, local
    add_reading(db, datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc))   # today, local

    report = reports.for_window(Window.TODAY, now=now)

    assert "1 samples" in report.text


def test_a_reading_just_before_local_midnight_belongs_to_yesterday(reports, db):
    """20:59 UTC is 23:59 local — still yesterday's report at 00:30 local."""
    add_reading(db, datetime(2026, 8, 26, 20, 59, tzinfo=timezone.utc))

    report = reports.for_window(Window.TODAY, now=datetime(2026, 8, 27, 0, 30, tzinfo=KYIV))

    assert report.is_empty


# ---------- status block ----------

def test_status_returns_none_without_readings(reports):
    assert reports.status() is None
    assert reports.status_report() is None


def test_status_uses_the_newest_reading(reports, db):
    now = datetime(2026, 8, 27, 10, 5, tzinfo=timezone.utc)
    add_reading(db, datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc), pm25=11.0)
    add_reading(db, datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc), pm25=99.0)

    view = reports.status(now=now)

    assert view.pm25 == 99.0
    assert view.level is Level.ERR


def test_status_compares_against_an_hour_ago(reports, db):
    now = datetime(2026, 8, 27, 10, 5, tzinfo=timezone.utc)
    add_reading(db, datetime(2026, 8, 27, 9, 30, tzinfo=timezone.utc), pm25=20.0)
    add_reading(db, datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc), pm25=10.0)

    view = reports.status(now=now)

    assert view.pm25_before == 20.0


def test_status_card_shows_a_sparkline_of_recent_readings(reports, db):
    now = datetime(2026, 8, 27, 10, 5, tzinfo=timezone.utc)
    for minute, pm25 in [(35, 5.0), (45, 10.0), (55, 20.0), (4, 40.0)]:
        hour = 9 if minute > 30 else 10
        add_reading(db, datetime(2026, 8, 27, hour, minute, tzinfo=timezone.utc), pm25=pm25)

    view = reports.status(now=now)
    assert view.spark == [5.0, 10.0, 20.0, 40.0]


def test_a_quiet_sensor_is_reported_as_quiet(reports, db):
    """A dead reader must not keep looking like clean air."""
    add_reading(db, datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc), pm25=5.0)
    now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

    view = reports.status(now=now)
    assert view.is_stale(now)

    report = reports.status_report(now=now)
    assert report.is_empty                     # nothing to render for a dead reader
    assert "Sensor quiet" in report.text
    assert "2 h" in report.text


def test_a_fresh_reading_is_not_stale(reports, db):
    now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    add_reading(db, datetime(2026, 8, 27, 9, 58, tzinfo=timezone.utc), pm25=5.0)

    assert reports.status(now=now).is_stale(now) is False

    report = reports.status_report(now=now)
    assert "Sensor quiet" not in report.text
    assert report.chart.getvalue().startswith(PNG_MAGIC)
    assert report.chart.name == "status.png"


def test_patterns_report_names_the_worst_hour(reports, db):
    now = datetime(2026, 8, 27, 12, 0, tzinfo=KYIV)
    for day in range(24, 27):
        for hour, pm25 in [(6, 5.0), (18, 60.0)]:
            add_reading(db, datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc), pm25=pm25)

    report = reports.for_window(Window.PATTERNS, now=now)

    assert not report.is_empty
    assert report.chart.getvalue().startswith(PNG_MAGIC)
    assert "Patterns" in report.text
    assert "<pre>" not in report.text    # worst/best hour are drawn into the PNG


def test_status_block_colour_follows_the_configured_thresholds(db):
    from common.air_quality import Level, Thresholds

    at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    strict = ReadingReports(db, KYIV, Thresholds(pm25_warn=10, pm10_warn=20,
                                                 pm25_err=30, pm10_err=40))
    lax = ReadingReports(db, KYIV, Thresholds())

    assert Level.WARN.emoji in strict.status_block(12.0, 1.0, at)
    assert Level.OK.emoji in lax.status_block(12.0, 1.0, at)


def test_status_block_renders_time_in_the_configured_zone(reports):
    at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    assert "13:00:00" in reports.status_block(5.0, 5.0, at)  # UTC+3


def test_report_is_immutable():
    with pytest.raises(Exception):
        Report(text="x").text = "y"
