"""Chart rendering.

Every chart is a PNG sent to Telegram, so there is no hover layer to lean on:
identity comes from a titled pane per series and a direct end label, and
threshold state always carries a text label beside the colour.

Palette is the validated categorical pair (blue / orange) with the reserved
status hues for the warn and high bands; the dark steps are chosen for the dark
surface, not inverted from the light ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # no display on the Pi

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

from common.air_quality import Level, Thresholds

# Reserved status hues — never used for a series.
BAND_OK = "#2e9e6b"
BAND_WARN = "#fab219"
BAND_HIGH = "#d03b3b"
BAND_ALPHA = 0.13

# The hue per level. Matplotlib's bundled font has no colour emoji, so the card
# draws a dot in this colour and names the level in words beside it; the word
# itself comes from `Level.label`, which owns it.
LEVEL_COLOUR = {"ok": BAND_OK, "warn": BAND_WARN, "err": BAND_HIGH}

# Sequential single-hue ramp for the heatmap (light -> dark).
SEQUENTIAL = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]

# A quiet day must look quiet: never scale the y-axis tighter than this.
Y_FLOOR = 25.0


@dataclass(frozen=True)
class Palette:
    name: str
    surface: str
    ink: str
    ink_2: str
    muted: str
    grid: str
    axis: str
    pm25: str
    pm10: str


LIGHT = Palette(
    name="light", surface="#fcfcfb", ink="#0b0b0b", ink_2="#52514e",
    muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
    pm25="#2a78d6", pm10="#eb6834",
)

DARK = Palette(
    name="dark", surface="#1a1a19", ink="#ffffff", ink_2="#c3c2b7",
    muted="#898781", grid="#2c2c2a", axis="#383835",
    pm25="#3987e5", pm10="#d95926",
)

THEMES = {LIGHT.name: LIGHT, DARK.name: DARK}


def palette_for(theme: Optional[str]) -> Palette:
    return THEMES.get((theme or "").lower(), LIGHT)


# ---------------- shared helpers ----------------

def _local_times(df: pd.DataFrame, tz) -> pd.Series:
    """Timestamps as tz-aware local time; naive values are read as UTC."""
    return pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(tz)


def _style_axes(ax, p: Palette) -> None:
    ax.set_facecolor(p.surface)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(p.axis)
        ax.spines[side].set_linewidth(1)
    ax.grid(True, axis="y", color=p.grid, linewidth=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors=p.muted, labelsize=9, length=0)
    ax.set_ylabel("µg/m³", color=p.muted, fontsize=9)


# Matplotlib's date axis is a float count of days, which is why everything
# below works in that unit: converting the timestamps once and passing floats
# to every plot call avoids re-running date2num for each of them.
HALF_HOUR = 30 / (24 * 60)


def _span(x) -> tuple:
    """x-limits that stay valid when a window holds a single reading."""
    first, last = float(x[0]), float(x[-1])
    if first == last:
        return first - HALF_HOUR, last + HALF_HOUR
    return first, last


def _time_axis(ax, x, tz, *, multiday: bool) -> None:
    locator = mdates.AutoDateLocator(minticks=4, maxticks=7)
    ax.xaxis.set_major_locator(locator)
    fmt = "%a %d" if multiday else "%H:%M"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=tz))
    ax.set_xlim(*_span(x))


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Centred rolling mean that keeps the ends anchored to real readings."""
    if window < 2 or len(values) <= window:
        return values
    kernel = np.ones(window) / window
    smoothed = np.convolve(values, kernel, mode="same")
    half = window // 2
    smoothed[:half] = values[:half].mean()
    smoothed[-half:] = values[-half:].mean()
    return smoothed


# ---------------- stats footer ----------------

# Where each column sits in the footer axis (0 = left edge, 1 = right edge).
# Numbers are right-aligned on these, so the decimal points stack whatever the
# magnitude — the reason the table moved out of a Telegram <pre> block.
COL_X = {"label": 0.0, "min": 0.46, "avg": 0.63, "max": 0.80, "peak": 1.0}


@dataclass(frozen=True)
class StatRow:
    """One pollutant's summary, already rendered to strings."""
    label: str
    minimum: str
    average: str
    maximum: str
    peak: str          # "ok" / "warn" / "high" — the word, not just the colour
    colour: str        # status hue for the peak, or None-ish muted

    @property
    def cells(self) -> tuple[str, str, str, str]:
        return self.minimum, self.average, self.maximum, self.peak


def stats_rows(df: pd.DataFrame, thresholds: Thresholds,
               palette: Palette = LIGHT) -> list[StatRow]:
    """min / avg / max per pollutant, plus where the peak landed.

    Pure and string-valued so the table can be tested without rendering a PNG.
    The peak is named in words as well as coloured — colour never carries
    meaning alone.
    """
    rows = []
    counts = df["n"].astype(float) if "n" in df.columns else None
    for label, key, warn, high in (
        ("PM2.5", "pm25", thresholds.pm25_warn, thresholds.pm25_err),
        ("PM10", "pm10", thresholds.pm10_warn, thresholds.pm10_err),
    ):
        values = df[key].astype(float)
        # A bucketed frame carries the true extremes beside the average; the
        # min/max of the averages would understate both.
        low_col, high_col = f"{key}_min", f"{key}_max"
        bottom = (float(df[low_col].min()) if low_col in df.columns
                  else float(values.min()))
        top = (float(df[high_col].max()) if high_col in df.columns
               else float(values.max()))
        if counts is not None and float(counts.sum()) > 0:
            # Count-weighted, so a partial bucket at either edge does not pull
            # the mean the way an average of averages would.
            average = float((values * counts).sum() / counts.sum())
        else:
            average = float(values.mean())
        if top >= high:
            peak, colour = "high", BAND_HIGH
        elif top >= warn:
            peak, colour = "warn", BAND_WARN
        else:
            peak, colour = "ok", palette.muted
        rows.append(StatRow(
            label=label,
            minimum=f"{bottom:.1f}",
            average=f"{average:.1f}",
            maximum=f"{top:.1f}",
            peak=peak,
            colour=colour,
        ))
    return rows


def _stats_footer(ax, rows: list[StatRow], p: Palette, *, samples: int) -> None:
    """Draw the summary table into its own axis — real type, no code block."""
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    headers = ("min", "avg", "max", "peak")
    for name in headers:
        ax.text(COL_X[name], 0.86, name, ha="right", va="center",
                color=p.muted, fontsize=8.5)
    ax.text(COL_X["label"], 0.86, f"{samples} samples · µg/m³", ha="left",
            va="center", color=p.muted, fontsize=8.5)
    ax.axhline(0.66, color=p.grid, linewidth=1)

    for i, row in enumerate(rows):
        y = 0.42 - i * 0.32
        ax.text(COL_X["label"], y, row.label, ha="left", va="center",
                color=p.ink_2, fontsize=9.5, fontweight="bold")
        for name, value in zip(headers, row.cells):
            emphasis = name in ("avg", "peak")
            ax.text(COL_X[name], y, value, ha="right", va="center",
                    color=row.colour if name == "peak" else p.ink,
                    fontsize=9.5, fontweight="bold" if emphasis else "normal")


def _to_png(fig) -> BytesIO:
    """Save at the figure's own size.

    `bbox_inches="tight"` costs a whole extra render pass - it draws once to
    find out how big everything came out, then crops and draws again - and
    measured at ~57 ms of a ~376 ms card. Every figure here sets its own
    margins instead, which is cheaper and makes the output size predictable
    rather than a function of how long the tick labels happened to be.
    """
    bio = BytesIO()
    fig.savefig(bio, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    bio.seek(0)
    return bio


# ---------------- window chart ----------------

def window_chart(
    df: pd.DataFrame,
    *,
    title: str,
    thresholds: Thresholds,
    tz,
    palette: Palette = LIGHT,
    smooth_window: int = 0,
    multiday: bool = False,
) -> BytesIO:
    """Stacked panes: one per pollutant, each on its own scale with its own bands.

    A shared axis cannot carry two different warn limits honestly (PM2.5 warns at
    35, PM10 at 50), which is why the panes are split.
    """
    if df.empty:
        raise ValueError("No data to plot")

    p = palette
    frame = df.sort_values("timestamp")
    # Once per card, not once per plot call. The profile showed date2num and
    # the pandas iteration behind it costing ~50ms of a ~380ms card, because
    # every plot, fill_between, annotate and text call converted afresh.
    x = mdates.date2num(_local_times(frame, tz))

    fig = plt.figure(figsize=(7, 5.4), dpi=150, facecolor=p.surface)
    gs = fig.add_gridspec(3, 1, height_ratios=[1, 1, 0.42], hspace=0.32,
                          left=0.105, right=0.935, top=0.872, bottom=0.075)
    axes = [fig.add_subplot(gs[0]), fig.add_subplot(gs[1])]
    footer = fig.add_subplot(gs[2])
    axes[1].sharex(axes[0])
    axes[0].tick_params(labelbottom=False)

    def _series(key: str):
        """The line, and the band it stands for. Identical arrays on raw data."""
        values = frame[key].to_numpy(float)
        low = (frame[f"{key}_min"].to_numpy(float)
               if f"{key}_min" in frame.columns else values)
        high = (frame[f"{key}_max"].to_numpy(float)
                if f"{key}_max" in frame.columns else values)
        return values, low, high

    panes = (
        (axes[0], _series("pm25"), p.pm25, "PM2.5",
         thresholds.pm25_warn, thresholds.pm25_err),
        (axes[1], _series("pm10"), p.pm10, "PM10",
         thresholds.pm10_warn, thresholds.pm10_err),
    )

    for ax, (values, lows, highs), colour, label, warn, high in panes:
        _style_axes(ax, p)
        top = max(high * 1.12, float(highs.max()) * 1.2, Y_FLOOR)

        ax.axhspan(warn, high, color=BAND_WARN, alpha=BAND_ALPHA, linewidth=0, zorder=0)
        ax.axhspan(high, top, color=BAND_HIGH, alpha=BAND_ALPHA, linewidth=0, zorder=0)
        ax.text(x[0], warn, f"  warn ≥{warn:g}", va="bottom", ha="left",
                color=p.muted, fontsize=8)
        ax.text(x[0], high, f"  high ≥{high:g}", va="bottom", ha="left",
                color=p.muted, fontsize=8)

        # Where a point stands for several readings, shade what it covers.
        # Without this the averaged line would quietly flatten every spike -
        # and a spike is the thing this product exists to show.
        if bool((highs > lows).any()):
            ax.fill_between(x, lows, highs, color=colour, alpha=0.20,
                            linewidth=0, zorder=1)

        if smooth_window > 1 and len(values) > smooth_window:
            ax.plot(x, values, color=colour, linewidth=0, marker=".",
                    markersize=2, alpha=0.28, zorder=2)
            ax.plot(x, _smooth(values, smooth_window), color=colour,
                    linewidth=2.2, zorder=3, solid_capstyle="round")
        else:
            ax.plot(x, values, color=colour, linewidth=2, zorder=3,
                    solid_capstyle="round")

        ax.set_ylim(0, top)
        ax.set_title(label, color=p.ink_2, fontsize=10, fontweight="bold",
                     loc="left", pad=4)
        ax.annotate(f"{values[-1]:.0f}", xy=(x[-1], values[-1]),
                    xytext=(6, 0), textcoords="offset points", va="center",
                    color=colour, fontsize=9.5, fontweight="bold")

    _time_axis(axes[1], x, tz, multiday=multiday)
    axes[0].set_xlim(*_span(x))
    total = int(frame["n"].sum()) if "n" in frame.columns else len(frame)
    _stats_footer(footer, stats_rows(frame, thresholds, p), p, samples=total)
    fig.suptitle(title, color=p.ink, fontsize=12, fontweight="bold",
                 x=0.105, ha="left", y=0.965)

    return _to_png(fig)


# ---------------- status card ----------------

BAR_X0, BAR_X1 = 0.37, 0.62      # where the level bar starts and ends
BAR_HEIGHT = 0.05


def _level_of(value: float, warn: float, high: float) -> tuple[str, str]:
    if value >= high:
        return "High", BAND_HIGH
    if value >= warn:
        return "Elevated", BAND_WARN
    return "Good", BAND_OK


def trend_text(current: float, previous: Optional[float], *,
               deadband: float = 0.05) -> str:
    """Direction against an earlier reading, in glyphs the PNG font has.

    The text card uses coloured squares for this; a PNG can draw the arrow
    itself, and the word still repeats the direction.
    """
    if previous is None or previous <= 0:
        return ""
    change = (current - previous) / previous
    if abs(change) < deadband:
        return "→ steady"
    arrow = "↑" if change > 0 else "↓"
    return f"{arrow} {abs(change) * 100:.0f}% vs 1h"


def status_card(
    *,
    pm25: float,
    pm10: float,
    level: str,
    thresholds: Thresholds,
    freshness: str,
    spark: Optional[list] = None,
    pm25_before: Optional[float] = None,
    pm10_before: Optional[float] = None,
    palette: Palette = LIGHT,
) -> BytesIO:
    """The latest reading as a card: level, magnitude, direction, freshness.

    Replaces the ▓░ meter that Telegram rendered in a <pre> block. The bar runs
    from zero to the *high* threshold with the warn limit marked, so the value
    is placed against its own limits rather than against the other pollutant's.
    """
    p = palette
    headline = Level(level).label
    level_colour = LEVEL_COLOUR[level]

    fig = plt.figure(figsize=(7, 3.7), dpi=150, facecolor=p.surface)
    ax = fig.add_axes([0.04, 0.04, 0.92, 0.92])
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    ax.plot([0.012], [0.93], marker="o", markersize=15, color=level_colour,
            clip_on=False)
    ax.text(0.045, 0.93, f"Air quality — {headline.lower()}", ha="left",
            va="center", color=p.ink, fontsize=21.5, fontweight="bold")

    rows = (
        ("PM2.5", pm25, pm25_before, thresholds.pm25_warn, thresholds.pm25_err),
        ("PM10", pm10, pm10_before, thresholds.pm10_warn, thresholds.pm10_err),
    )
    for i, (label, value, before, warn, high) in enumerate(rows):
        y = 0.62 - i * 0.26
        state, colour = _level_of(value, warn, high)

        ax.text(0.0, y, label, ha="left", va="center", color=p.ink_2,
                fontsize=14.5, fontweight="bold")
        ax.text(0.34, y, f"{value:.1f}", ha="right", va="center", color=colour,
                fontsize=27.5, fontweight="bold")

        span = BAR_X1 - BAR_X0
        ax.add_patch(Rectangle((BAR_X0, y - BAR_HEIGHT / 2), span, BAR_HEIGHT,
                               color=p.grid, linewidth=0, zorder=1))
        filled = min(1.0, value / high) if high > 0 else 0.0
        if value > 0:
            filled = max(filled, 0.012)
        ax.add_patch(Rectangle((BAR_X0, y - BAR_HEIGHT / 2), span * filled,
                               BAR_HEIGHT, color=colour, linewidth=0, zorder=2))

        mark = BAR_X0 + span * (warn / high if high > 0 else 0)
        ax.plot([mark, mark], [y - BAR_HEIGHT, y + BAR_HEIGHT], color=p.surface,
                linewidth=1.6, zorder=3)
        # Centred on the mark unless that would run into the "high" label,
        # which happens whenever warn sits close to high.
        crowded = mark > BAR_X1 - 0.17
        ax.text(mark - 0.01 if crowded else mark, y + BAR_HEIGHT * 1.4,
                f"warn {warn:g}", ha="right" if crowded else "center",
                va="bottom", color=p.muted, fontsize=11)
        ax.text(BAR_X1, y + BAR_HEIGHT * 1.4, f"high {high:g}", ha="right",
                va="bottom", color=p.muted, fontsize=11)

        # The state word repeats what the bar's colour says.
        ax.text(0.645, y, state.lower(), ha="left", va="center", color=colour,
                fontsize=13, fontweight="bold")
        ax.text(1.0, y, trend_text(value, before), ha="right", va="center",
                color=p.muted, fontsize=12)

    ax.text(0.0, 0.06, freshness, ha="left", va="center", color=p.muted,
            fontsize=12.5)
    if spark:
        _spark_axes(fig, spark, p)

    return _to_png(fig)


def _spark_axes(fig, spark: list, p: Palette) -> None:
    """The last hour as a hairline trace, bottom right of the status card."""
    values = np.asarray([float(v) for v in spark], dtype=float)
    ax = fig.add_axes([0.62, 0.06, 0.34, 0.13])
    ax.set_axis_off()
    ax.plot(range(len(values)), values, color=p.pm25, linewidth=1.4,
            solid_capstyle="round")
    ax.plot([len(values) - 1], [values[-1]], marker="o", markersize=3,
            color=p.pm25)
    low, high = float(values.min()), float(values.max())
    pad = max((high - low) * 0.25, 0.5)
    ax.set_ylim(low - pad, high + pad)
    ax.text(0, 1.0, "PM2.5 · last hour", transform=ax.transAxes, ha="left",
            va="bottom", color=p.muted, fontsize=11)


# ---------------- hour-of-day heatmap ----------------

@dataclass(frozen=True)
class HourExtremes:
    """The hour of day that is reliably dirtiest, and the one that is cleanest."""
    worst_hour: int
    worst_value: float
    best_hour: int
    best_value: float

    def label(self, which: str) -> str:
        hour = self.worst_hour if which == "worst" else self.best_hour
        value = self.worst_value if which == "worst" else self.best_value
        return f"{hour:02d}:00 · {value:.0f} µg/m³"


def hour_extremes(df: pd.DataFrame, tz) -> HourExtremes:
    """Mean PM2.5 per hour of day, reduced to its two ends. Pure: no render."""
    local = _local_times(df, tz)
    by_hour = df.assign(hour=local.dt.hour).groupby("hour")["pm25"].mean()
    return HourExtremes(
        worst_hour=int(by_hour.idxmax()), worst_value=float(by_hour.max()),
        best_hour=int(by_hour.idxmin()), best_value=float(by_hour.min()),
    )


def hour_heatmap(
    df: pd.DataFrame,
    *,
    tz,
    palette: Palette = LIGHT,
    title: str = "PM2.5 by hour of day",
) -> BytesIO:
    """Day × hour grid of mean PM2.5 — answers *when* the air is bad."""
    if df.empty:
        raise ValueError("No data to plot")

    p = palette
    frame = df.copy()
    local = _local_times(frame, tz)
    frame["day"] = local.dt.date
    frame["hour"] = local.dt.hour

    grid = (
        frame.pivot_table(index="day", columns="hour", values="pm25", aggfunc="mean")
        .reindex(columns=range(24))
        .sort_index()
    )

    cmap = LinearSegmentedColormap.from_list("air-quality", SEQUENTIAL)
    fig, ax = plt.subplots(figsize=(7, 3.1), dpi=150, facecolor=p.surface)
    # Explicit margins because the figure is saved at its own size: the
    # worst/best-hour line sits below the axes and would otherwise be cropped.
    fig.subplots_adjust(left=0.098, right=0.94, top=0.88, bottom=0.26)
    ax.set_facecolor(p.surface)

    values = grid.to_numpy(dtype=float)
    vmax = float(np.nanpercentile(values, 98)) if np.isfinite(values).any() else 1.0
    image = ax.imshow(values, aspect="auto", cmap=cmap, vmin=0, vmax=max(vmax, 1.0))

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks(range(0, 24, 3))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 3)])
    ax.set_yticks(range(len(grid.index)))
    # A 7-day window touches 8 calendar days, so a bare weekday name repeats.
    ax.set_yticklabels([d.strftime("%a %d") for d in grid.index])
    ax.tick_params(colors=p.muted, labelsize=9, length=0)
    ax.set_title(title, color=p.ink, fontsize=12, fontweight="bold", loc="left", pad=10)

    bar = fig.colorbar(image, ax=ax, pad=0.015, fraction=0.03)
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=p.muted, labelsize=8, length=0)
    bar.set_label("µg/m³", color=p.muted, fontsize=8)

    extremes = hour_extremes(frame, tz)
    for x, which, colour in ((0.0, "worst", BAND_HIGH), (0.46, "best", BAND_OK)):
        ax.annotate(f"{which} hour", xy=(x, -0.24), xycoords="axes fraction",
                    ha="left", va="top", color=p.muted, fontsize=8.5)
        ax.annotate(extremes.label(which), xy=(x + 0.16, -0.24),
                    xycoords="axes fraction", ha="left", va="top", color=colour,
                    fontsize=9, fontweight="bold")

    return _to_png(fig)
