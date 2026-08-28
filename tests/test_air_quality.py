import pytest

from common.air_quality import Level, Thresholds


@pytest.mark.parametrize(
    "pm25, pm10, expected",
    [
        (0, 0, Level.OK),
        (34.9, 49.9, Level.OK),
        (35, 0, Level.WARN),        # pm2.5 alone crosses warn
        (0, 50, Level.WARN),        # pm10 alone crosses warn
        (74.9, 99.9, Level.WARN),
        (75, 0, Level.ERR),
        (0, 100, Level.ERR),
        (1000, 1000, Level.ERR),
    ],
)
def test_level_boundaries(thresholds, pm25, pm10, expected):
    assert thresholds.level(pm25, pm10) is expected


def test_thresholds_are_inclusive_at_the_boundary(thresholds):
    """A reading exactly on the threshold alerts; the value below it does not."""
    assert thresholds.level(thresholds.pm25_warn, 0) is Level.WARN
    assert thresholds.level(thresholds.pm25_warn - 0.1, 0) is Level.OK


def test_from_env_reads_all_four(monkeypatch):
    monkeypatch.setenv("PM25_WARN", "10")
    monkeypatch.setenv("PM10_WARN", "20")
    monkeypatch.setenv("PM25_ERR", "30")
    monkeypatch.setenv("PM10_ERR", "40")

    t = Thresholds.from_env()

    assert (t.pm25_warn, t.pm10_warn, t.pm25_err, t.pm10_err) == (10, 20, 30, 40)
    assert t.level(10, 0) is Level.WARN
    assert t.level(30, 0) is Level.ERR


def test_from_env_falls_back_to_defaults(monkeypatch):
    for name in ("PM25_WARN", "PM10_WARN", "PM25_ERR", "PM10_ERR"):
        monkeypatch.delenv(name, raising=False)
    assert Thresholds.from_env() == Thresholds()


def test_configured_thresholds_drive_the_emoji():
    """Regression: the emoji used to be hardcoded and ignored PM25_WARN."""
    tuned = Thresholds(pm25_warn=10, pm10_warn=20, pm25_err=30, pm10_err=40)
    assert tuned.level(12, 0).emoji == Level.WARN.emoji
    assert Thresholds().level(12, 0).emoji == Level.OK.emoji


def test_level_knows_whether_it_alerts():
    assert not Level.OK.is_alerting
    assert Level.WARN.is_alerting
    assert Level.ERR.is_alerting


def test_levels_have_distinct_emoji():
    assert len({lvl.emoji for lvl in Level}) == len(Level)


def test_level_value_is_the_stored_status_string():
    """`Reading.status` and the routing key both use this value."""
    assert [lvl.value for lvl in Level] == ["ok", "warn", "err"]
