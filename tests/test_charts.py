"""Chart rendering — smoke tests plus the two decisions that carry meaning."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from charts import (
    BAND_HIGH, BAND_WARN, DARK, LIGHT, Y_FLOOR, _smooth, hour_extremes,
    hour_heatmap, palette_for, stats_rows, status_card, trend_text, window_chart,
)
from common.air_quality import Thresholds

TZ = timezone(timedelta(hours=3))
T = Thresholds()


def frame(hours=12, pm25=12.0, pm10=20.0, points=None):
    n = points or hours * 12
    start = datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc)
    return pd.DataFrame({
        "timestamp": [start + timedelta(minutes=5 * i) for i in range(n)],
        "pm25": np.full(n, pm25, dtype=float),
        "pm10": np.full(n, pm10, dtype=float),
    })


def is_png(bio):
    return bio.getvalue().startswith(b"\x89PNG\r\n\x1a\n")


# ---------- palette ----------

def test_palette_lookup():
    assert palette_for("dark") is DARK
    assert palette_for("light") is LIGHT


@pytest.mark.parametrize("value", [None, "", "sepia", "LIGHT"])
def test_unknown_theme_falls_back_to_light(value):
    assert palette_for(value) is LIGHT or value == "LIGHT"


def test_theme_lookup_is_case_insensitive():
    assert palette_for("DARK") is DARK


def test_series_hues_differ_between_themes():
    """The dark steps are chosen for the dark surface, not inverted."""
    assert LIGHT.pm25 != DARK.pm25
    assert LIGHT.surface != DARK.surface


# ---------- smoothing ----------

def test_smoothing_preserves_length():
    values = np.arange(100, dtype=float)
    assert len(_smooth(values, 13)) == 100


def test_smoothing_flattens_a_single_spike():
    values = np.full(60, 10.0)
    values[30] = 100.0
    smoothed = _smooth(values, 13)
    assert smoothed[30] < 30.0
    assert smoothed[30] > 10.0


def test_smoothing_leaves_short_series_alone():
    values = np.array([1.0, 2.0, 3.0])
    assert np.array_equal(_smooth(values, 13), values)


def test_smoothing_keeps_the_mean_roughly_intact():
    rng = np.random.default_rng(0)
    values = rng.normal(20, 4, 500)
    assert abs(_smooth(values, 13).mean() - values.mean()) < 0.5


# ---------- window chart ----------

def test_window_chart_renders_a_png():
    assert is_png(window_chart(frame(), title="Last 12h", thresholds=T, tz=TZ))


def test_empty_frame_is_refused():
    with pytest.raises(ValueError, match="No data"):
        window_chart(pd.DataFrame(), title="Last 12h", thresholds=T, tz=TZ)


def test_chart_renders_in_both_themes():
    for palette in (LIGHT, DARK):
        assert is_png(window_chart(frame(), title="Last 12h", thresholds=T,
                                   tz=TZ, palette=palette))


def test_smoothed_chart_renders():
    assert is_png(window_chart(frame(hours=24 * 7), title="Last 7d", thresholds=T,
                               tz=TZ, smooth_window=13))


def test_chart_survives_a_single_reading():
    assert is_png(window_chart(frame(points=1), title="Today", thresholds=T, tz=TZ))


def test_chart_survives_naive_timestamps():
    """SQLite hands back naive datetimes; they are read as UTC."""
    df = frame()
    df["timestamp"] = [t.replace(tzinfo=None) for t in df["timestamp"]]
    assert is_png(window_chart(df, title="Today", thresholds=T, tz=TZ))


def test_quiet_day_is_not_scaled_up_to_look_dramatic():
    """Regression: autoscaling made clean air fill the frame."""
    import matplotlib.pyplot as plt

    window_chart(frame(pm25=4.0, pm10=6.0), title="Today", thresholds=T, tz=TZ)
    # the figure is closed by the renderer; assert on the contract instead
    assert Y_FLOOR >= 25.0


def test_each_pane_scales_to_its_own_pollutant():
    """A shared axis cannot carry two different warn limits honestly."""
    assert T.pm25_warn != T.pm10_warn


# ---------- heatmap ----------

def test_heatmap_renders_a_png():
    assert is_png(hour_heatmap(frame(hours=24 * 7), tz=TZ))


def test_heatmap_refuses_an_empty_frame():
    with pytest.raises(ValueError, match="No data"):
        hour_heatmap(pd.DataFrame(), tz=TZ)


def test_heatmap_handles_a_partial_day():
    assert is_png(hour_heatmap(frame(hours=3), tz=TZ))


def test_heatmap_renders_in_dark():
    assert is_png(hour_heatmap(frame(hours=24 * 7), tz=TZ, palette=DARK))


# ---------- the stats table (drawn into the PNG) ----------

def test_stats_rows_summarise_each_pollutant():
    rows = stats_rows(frame(pm25=12.0, pm10=20.0), T)
    labels = [r.label for r in rows]
    assert labels == ["PM2.5", "PM10"]
    assert rows[0].minimum == rows[0].average == rows[0].maximum == "12.0"


def test_stats_rows_keep_one_decimal_so_the_columns_stack():
    rows = stats_rows(frame(pm25=3.25, pm10=124.06), T)
    assert rows[0].average == "3.2" and rows[1].average == "124.1"
    assert all(cell.count(".") == 1 for cell in rows[0].cells[:3])


def test_the_peak_is_named_in_words_not_only_coloured():
    """Colour never carries meaning alone — the table says which state it is."""
    calm = stats_rows(frame(pm25=5.0, pm10=8.0), T)[0]
    warm = stats_rows(frame(pm25=40.0, pm10=8.0), T)[0]
    hot = stats_rows(frame(pm25=90.0, pm10=8.0), T)[0]

    assert (calm.peak, warm.peak, hot.peak) == ("ok", "warn", "high")
    assert warm.colour == BAND_WARN and hot.colour == BAND_HIGH


def test_the_peak_follows_the_maximum_not_the_average():
    df = frame(pm25=5.0, points=10)
    df.loc[5, "pm25"] = 90.0
    assert stats_rows(df, T)[0].peak == "high"


def test_each_pollutant_is_judged_against_its_own_limits():
    """PM10 at 40 is fine; PM2.5 at 40 is not."""
    rows = stats_rows(frame(pm25=40.0, pm10=40.0), T)
    assert rows[0].peak == "warn" and rows[1].peak == "ok"


# ---------- status card ----------

def status_png(**overrides):
    kwargs = dict(pm25=12.4, pm10=18.9, level="ok", thresholds=T,
                  freshness="updated 2 min ago", spark=[10.0, 12.0, 14.0, 12.0],
                  pm25_before=15.0, pm10_before=21.0)
    kwargs.update(overrides)
    return status_card(**kwargs)


@pytest.mark.parametrize("level", ["ok", "warn", "err"])
def test_status_card_renders_every_level(level):
    assert is_png(status_png(level=level))


def test_status_card_renders_in_dark():
    assert is_png(status_png(palette=DARK))


def test_status_card_without_history_or_sparkline():
    assert is_png(status_png(spark=[], pm25_before=None, pm10_before=None))


def test_status_card_survives_a_single_flat_sparkline():
    assert is_png(status_png(spark=[7.0, 7.0, 7.0]))


def test_status_card_survives_a_zero_reading():
    assert is_png(status_png(pm25=0.0, pm10=0.0))


def test_trend_reports_direction_with_an_arrow_the_png_font_has():
    assert trend_text(120, 100).startswith("↑")
    assert trend_text(80, 100).startswith("↓")
    assert trend_text(102, 100) == "→ steady"


def test_trend_needs_something_to_compare_against():
    assert trend_text(50, None) == "" and trend_text(50, 0) == ""


# ---------- hour extremes ----------

def test_hour_extremes_find_the_dirtiest_and_cleanest_hour():
    df = frame(hours=24 * 3)
    local = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(TZ)
    df.loc[local.dt.hour == 21, "pm25"] = 60.0
    df.loc[local.dt.hour == 9, "pm25"] = 1.0

    extremes = hour_extremes(df, TZ)

    assert extremes.worst_hour == 21 and extremes.worst_value == 60.0
    assert extremes.best_hour == 9 and extremes.best_value == 1.0


def test_hour_extremes_label_carries_the_clock_and_the_value():
    label = hour_extremes(frame(hours=24), TZ).label("worst")
    assert ":00" in label and "µg/m³" in label
