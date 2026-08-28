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

from common.air_quality import Thresholds

# Reserved status hues — never used for a series.
BAND_WARN = "#fab219"
BAND_HIGH = "#d03b3b"
BAND_ALPHA = 0.13

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


def _span(times) -> tuple:
    """x-limits that stay valid when a window holds a single reading."""
    first, last = times.iloc[0], times.iloc[-1]
    if first == last:
        pad = pd.Timedelta(minutes=30)
        return first - pad, last + pad
    return first, last


def _time_axis(ax, times, tz, *, multiday: bool) -> None:
    locator = mdates.AutoDateLocator(minticks=4, maxticks=7)
    ax.xaxis.set_major_locator(locator)
    fmt = "%a %d" if multiday else "%H:%M"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=tz))
    ax.set_xlim(*_span(times))


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


def _to_png(fig) -> BytesIO:
    bio = BytesIO()
    fig.savefig(bio, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
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
    times = _local_times(frame, tz)

    fig, axes = plt.subplots(
        2, 1, figsize=(7, 4.6), dpi=150, sharex=True,
        facecolor=p.surface, gridspec_kw={"hspace": 0.32},
    )

    panes = (
        (axes[0], frame["pm25"].to_numpy(float), p.pm25, "PM2.5",
         thresholds.pm25_warn, thresholds.pm25_err),
        (axes[1], frame["pm10"].to_numpy(float), p.pm10, "PM10",
         thresholds.pm10_warn, thresholds.pm10_err),
    )

    for ax, values, colour, label, warn, high in panes:
        _style_axes(ax, p)
        top = max(high * 1.12, float(values.max()) * 1.2, Y_FLOOR)

        ax.axhspan(warn, high, color=BAND_WARN, alpha=BAND_ALPHA, linewidth=0, zorder=0)
        ax.axhspan(high, top, color=BAND_HIGH, alpha=BAND_ALPHA, linewidth=0, zorder=0)
        ax.text(times.iloc[0], warn, f"  warn ≥{warn:g}", va="bottom", ha="left",
                color=p.muted, fontsize=8)
        ax.text(times.iloc[0], high, f"  high ≥{high:g}", va="bottom", ha="left",
                color=p.muted, fontsize=8)

        if smooth_window > 1 and len(values) > smooth_window:
            ax.plot(times, values, color=colour, linewidth=0, marker=".",
                    markersize=2, alpha=0.28, zorder=2)
            ax.plot(times, _smooth(values, smooth_window), color=colour,
                    linewidth=2.2, zorder=3, solid_capstyle="round")
        else:
            ax.plot(times, values, color=colour, linewidth=2, zorder=3,
                    solid_capstyle="round")

        ax.set_ylim(0, top)
        ax.set_title(label, color=p.ink_2, fontsize=10, fontweight="bold",
                     loc="left", pad=4)
        ax.annotate(f"{values[-1]:.0f}", xy=(times.iloc[-1], values[-1]),
                    xytext=(6, 0), textcoords="offset points", va="center",
                    color=colour, fontsize=9.5, fontweight="bold")

    _time_axis(axes[1], times, tz, multiday=multiday)
    axes[0].set_xlim(*_span(times))
    fig.suptitle(title, color=p.ink, fontsize=12, fontweight="bold",
                 x=0.125, ha="left", y=0.99)

    return _to_png(fig)


# ---------------- hour-of-day heatmap ----------------

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

    return _to_png(fig)
