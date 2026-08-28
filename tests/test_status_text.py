"""The text that surrounds a PNG: captions, the quiet-sensor notice, Info.

Everything tabular moved into `charts` — see tests/test_charts.py.
"""
from datetime import datetime, timedelta, timezone

import pytest

from common.air_quality import Level, Thresholds
from html_helpers import (
    format_caption, format_stale_card, info_text, relative_time,
)

AT = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
T = Thresholds()


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


# ---------- caption ----------

def test_the_caption_is_one_line_with_no_block():
    """The numbers are typeset into the PNG; Telegram has no table markup."""
    caption = format_caption("Today", 412)
    assert "\n" not in caption
    assert "<pre>" not in caption


def test_caption_names_the_window_and_the_sample_count():
    caption = format_caption("Last 12h", 412)
    assert "Last 12h" in caption and "412 samples" in caption


def test_caption_fits_a_telegram_photo():
    assert len(format_caption("Today", 412)) <= 1024
    assert len(format_caption("x" * 4000, 1)) <= 1024
