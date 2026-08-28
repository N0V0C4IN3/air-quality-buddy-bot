from datetime import datetime, timedelta, timezone

import pytest

from common.db import ReadingRepository
from reports import ReadingReports, Report, Window

KYIV = timezone(timedelta(hours=3))  # fixed offset: no tz database needed


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
    assert [w.title for w in Window] == ["Today", "Last 12h", "Last 7d"]


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
    assert "Samples: <b>3</b>" in report.text
    assert "10.0" in report.text and "30.0" in report.text  # min and max
    assert report.chart.getvalue().startswith(b"\x89PNG")
    assert report.chart.name == "Today.png"


def test_window_excludes_readings_outside_it(reports, db):
    now = datetime(2026, 8, 27, 14, 30, tzinfo=KYIV)
    add_reading(db, datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc))  # yesterday, local
    add_reading(db, datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc))   # today, local

    report = reports.for_window(Window.TODAY, now=now)

    assert "Samples: <b>1</b>" in report.text


def test_a_reading_just_before_local_midnight_belongs_to_yesterday(reports, db):
    """20:59 UTC is 23:59 local — still yesterday's report at 00:30 local."""
    add_reading(db, datetime(2026, 8, 26, 20, 59, tzinfo=timezone.utc))

    report = reports.for_window(Window.TODAY, now=datetime(2026, 8, 27, 0, 30, tzinfo=KYIV))

    assert report.is_empty


# ---------- status block ----------

def test_latest_returns_none_without_readings(reports):
    assert reports.latest() is None


def test_latest_uses_the_newest_reading(reports, db):
    add_reading(db, datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc), pm25=11.0)
    add_reading(db, datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc), pm25=99.0)

    block = reports.latest()

    assert "99.0" in block
    assert "11.0" not in block


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
