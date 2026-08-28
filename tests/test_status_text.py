"""The text-only status card: meter, sparkline, trend, freshness."""
from datetime import datetime, timedelta, timezone

import pytest

from common.air_quality import Level, Thresholds
from html_helpers import (
    TREND_DOWN, TREND_UP, format_stale_card, format_status_card, info_text, meter,
    relative_time, sparkline, trend,
)

AT = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
T = Thresholds()


# ---------- meter ----------

@pytest.mark.parametrize("value, expected_filled", [
    (0, 0), (75, 10), (150, 10), (37.5, 5), (7.5, 1),
])
def test_meter_fills_proportionally(value, expected_filled):
    assert meter(value, 75).count("▓") == expected_filled


def test_meter_is_always_the_declared_width():
    for value in (0, 1, 40, 75, 999):
        assert len(meter(value, 75, width=10)) == 10


def test_meter_never_overflows_past_the_ceiling():
    assert meter(500, 75) == "▓" * 10


def test_a_real_reading_always_shows_at_least_one_block():
    """0.4 µg/m³ rounds to zero blocks, but the sensor did report something."""
    assert meter(0.4, 75).startswith("▓")
    assert meter(0, 75) == "░" * 10


def test_meter_survives_a_zero_ceiling():
    assert meter(10, 0) == "░" * 10


# ---------- sparkline ----------

def test_sparkline_has_one_tick_per_reading():
    assert len(sparkline([1, 2, 3, 4, 5])) == 5


def test_sparkline_maps_extremes_to_the_end_ticks():
    line = sparkline([0, 50, 100])
    assert line[0] == "▁" and line[-1] == "█"


def test_flat_readings_render_flat():
    assert sparkline([7, 7, 7, 7]) == "▁▁▁▁"


def test_sparkline_of_nothing_is_empty():
    assert sparkline([]) == ""


def test_sparkline_handles_a_single_reading():
    assert len(sparkline([12.5])) == 1


# ---------- trend ----------

def test_trend_reports_a_rise():
    assert trend(120, 100) == f"{TREND_UP} ↑ 20%"


def test_trend_reports_a_fall():
    assert trend(80, 100) == f"{TREND_DOWN} ↓ 20%"


def test_trend_markers_do_not_collide_with_the_level_emoji():
    """Circles mean air-quality level; the trend uses squares so a red marker
    beside a value never reads as 'high'."""
    for level in Level:
        assert level.emoji not in (TREND_UP, TREND_DOWN)


def test_the_card_never_repeats_the_level_emoji_as_a_trend():
    text = card(level=Level.ERR, pm25=99.0, pm25_before=50.0)
    assert text.count(Level.ERR.emoji) == 1


def test_rising_particulates_are_red_and_falling_are_green():
    """Telegram HTML cannot colour text, so the emoji carries the colour."""
    assert TREND_UP == "🟥" and TREND_DOWN == "🟩"
    assert TREND_UP in trend(120, 100)
    assert TREND_DOWN in trend(80, 100)
    assert TREND_UP not in trend(80, 100)


def test_steady_carries_no_colour():
    assert TREND_UP not in trend(102, 100)
    assert TREND_DOWN not in trend(102, 100)


def test_card_colours_each_pollutant_independently():
    text = card(pm25=20.0, pm10=10.0, pm25_before=10.0, pm10_before=20.0)
    pm25_line = next(l for l in text.splitlines() if "PM2.5" in l)
    pm10_line = next(l for l in text.splitlines() if l.lstrip().startswith("PM10"))

    assert TREND_UP in pm25_line and TREND_DOWN not in pm25_line
    assert TREND_DOWN in pm10_line and TREND_UP not in pm10_line


def test_small_changes_read_as_steady():
    assert trend(102, 100) == "→ steady"


def test_trend_needs_something_to_compare_against():
    assert trend(50, None) == ""
    assert trend(50, 0) == ""


# ---------- relative time ----------

@pytest.mark.parametrize("delta, expected", [
    (timedelta(seconds=5), "just now"),
    (timedelta(minutes=1), "1 min ago"),
    (timedelta(minutes=47), "47 min ago"),
    (timedelta(hours=2), "2 h ago"),
    (timedelta(days=3), "3 d ago"),
])
def test_relative_time(delta, expected):
    assert relative_time(AT, AT + delta) == expected


def test_a_future_timestamp_does_not_go_negative():
    assert relative_time(AT, AT - timedelta(minutes=5)) == "just now"


# ---------- the card ----------

def card(**overrides):
    kwargs = dict(pm25=12.4, pm10=18.9, level=Level.OK, observed_at=AT,
                  thresholds=T, spark=[10, 12, 14, 12], pm25_before=15.0,
                  pm10_before=21.0, now=AT + timedelta(minutes=2))
    kwargs.update(overrides)
    return format_status_card(**kwargs)


def test_card_names_the_level():
    assert "good" in card(level=Level.OK)
    assert "elevated" in card(level=Level.WARN)
    assert "high" in card(level=Level.ERR)


def test_card_carries_the_level_emoji():
    assert Level.WARN.emoji in card(level=Level.WARN)


def test_card_shows_both_readings_and_freshness():
    text = card()
    assert "12.4" in text and "18.9" in text
    assert "2 min ago" in text


def test_card_includes_the_meter_and_sparkline():
    text = card()
    assert "▓" in text and "▁" in text


def test_card_without_history_omits_the_trend():
    text = card(spark=[], pm25_before=None, pm10_before=None)
    assert "↑" not in text and "↓" not in text and "steady" not in text
    assert "12.4" in text


def test_card_has_no_blank_first_line_in_the_block():
    """A newline straight after <pre> renders as an empty row in Telegram."""
    assert "<pre>" + chr(10) not in card()
    assert card().split("<pre>")[1].startswith("PM2.5")


def test_card_is_valid_telegram_html():
    text = card()
    assert text.count("<pre>") == text.count("</pre>") == 1
    assert text.count("<b>") == text.count("</b>")
    assert text.count("<i>") == text.count("</i>")


# ---------- stale ----------

def test_stale_card_says_how_long_it_has_been_quiet():
    text = format_stale_card(AT, 300, AT + timedelta(minutes=47))
    assert "47 min" in text
    assert "ago" not in text          # reads "for 47 min", not "for 47 min ago"
    assert "expected every 5 min" in text


def test_stale_card_shows_the_last_seen_clock():
    text = format_stale_card(AT, 300, AT + timedelta(hours=2))
    assert "18:00" in text


# ---------- info ----------

def test_info_explains_how_to_read_the_patterns_heatmap():
    text = info_text(T)
    assert "Patterns" in text
    assert "hour" in text.lower()
    assert "Darker" in text or "darker" in text


def test_info_explains_the_chart_bands():
    text = info_text(T)
    assert "Amber band" in text and "red band" in text


def test_info_states_the_configured_thresholds():
    text = info_text(Thresholds(pm25_warn=12, pm10_warn=22, pm25_err=32, pm10_err=42))
    assert "12.0" in text and "42.0" in text


def test_info_fits_a_single_telegram_message():
    """Telegram rejects a text message over 4096 characters."""
    assert len(info_text(T)) <= 4096


def test_info_is_valid_telegram_html():
    text = info_text(T)
    for tag in ("b", "i", "pre"):
        assert text.count(f"<{tag}>") == text.count(f"</{tag}>"), tag
