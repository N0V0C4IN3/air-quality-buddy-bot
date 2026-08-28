"""Telegram HTML formatting.

What is left here is the text that surrounds a PNG: photo captions, the quiet-
sensor notice and the alert fan-out block. Anything tabular lives in `charts`
now — Telegram has no table markup, so alignment can only be honest in an image.

No thresholds of its own — the level is decided by `common.air_quality` and
handed in.
"""
from __future__ import annotations

from datetime import datetime, timezone

from common.air_quality import Level, Thresholds

# Telegram rejects a photo caption over 1024 characters.
CAPTION_LIMIT = 1024


def fmt_num(x: float) -> str:
    # 1 decimal, thin space thousands if needed
    return f"{x:,.1f}".replace(",", " ")


def fmt_dt(dt_utc: datetime, tz=timezone.utc) -> str:
    # Show local time with timezone
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def relative_time(then: datetime, now: datetime | None = None) -> str:
    """'just now', '4 min ago', '2 h ago', '3 d ago'."""
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def format_stale_card(observed_at: datetime, expected_every: int,
                      now: datetime | None = None) -> str:
    """A dead reader must not look like clean air."""
    now = now or datetime.now(timezone.utc)
    return (
        "<b>⚠️ Sensor quiet</b>\n"
        f"<pre>No readings for {relative_time(observed_at, now).replace(' ago', '')}\n"
        f"last seen {observed_at.strftime('%H:%M')} · "
        f"expected every {expected_every // 60} min</pre>"
    )


def format_caption(title: str, count: int) -> str:
    """Chart caption — one line; the numbers are typeset into the PNG.

    Telegram has no table markup at all (the tag set is b/i/u/s/code/pre/a/
    blockquote), so a text table can only ever be a monospace block, and a bold
    cell inside <pre> renders in a wider face and shifts its whole row. The
    image is the only place the columns can line up honestly.
    """
    caption = f"<b>{title}</b> · {count} samples"
    return caption if len(caption) <= CAPTION_LIMIT else caption[:CAPTION_LIMIT]


def info_text(thresholds: Thresholds) -> str:
    # Simple legend + what units mean + typical ranges
    lines = [
        "<b>ℹ️ About the readings</b>",
        "",
        "<b>What are these numbers?</b>",
        "• <b>PM2.5</b> = fine particles ≤2.5 μm.",
        "• <b>PM10</b>  = coarse particles ≤10 μm.",
        "• Units: <b>µg/m³</b> (micrograms per cubic meter).",
        "",
        "<b>Typical indoor values</b> (rough guide):",
        "<pre>Very clean: 0–5 µg/m³",
        "Normal:     5–15 µg/m³",
        "Cooking:    15–50+ µg/m³",
        "Smoky/Smog: 50–100+ µg/m³</pre>",
        "",
        "<b>Alert thresholds (this bot)</b>",
        f"• <b>WARN</b>  if PM2.5 ≥ <b>{fmt_num(thresholds.pm25_warn)}</b> "
        f"or PM10 ≥ <b>{fmt_num(thresholds.pm10_warn)}</b>",
        f"• <b>ERROR</b> if PM2.5 ≥ <b>{fmt_num(thresholds.pm25_err)}</b> "
        f"or PM10 ≥ <b>{fmt_num(thresholds.pm10_err)}</b>",
        "",
        "<b>Reading the chart</b>",
        "• Each pollutant gets its own pane and its own limits.",
        "• Amber band = above warn, red band = above high.",
        "• The number on the right is the latest reading.",
        "• On <b>7d</b> the solid line is a ~1h average; the faint dots are the raw readings.",
        "",
        "<b>Reading 🗓 Patterns</b>",
        "• One row per day, one column per hour of the day.",
        "• Each cell is the <b>average PM2.5</b> for that hour.",
        "• Darker = dirtier air. The scale is on the right.",
        "<pre>      00    06    12    18",
        "Mon  ░░░░  ▓▓░░  ░░▓░  ▓▓▓░</pre>",
        "• Read it <i>down a column</i>: a dark column means that hour is",
        "  reliably bad — cooking, traffic, or an open window.",
        "• Read it <i>across a row</i> to compare one day with the rest.",
        "• Pale cells at the top or bottom edge are partial days, not clean air.",
        "",
        "<b>Tips</b>",
        "• Readings stabilize after 5–10 min warm-up.",
        "• Place away from direct vents/windows for representative air.",
        "• Values jump during cooking, candles, smoking, vacuuming, etc.",
        "",
        "<i>Note:</i> Health guidelines often quote daily/annual averages;",
        "this sensor shows <b>instant</b> concentration, so short peaks are normal.",
    ]

    aqi = [
        "<b>Quick legend</b>",
        f"{Level.OK.emoji} Good (low)   — keep airing/filtration as is",
        f"{Level.WARN.emoji} Elevated     — ventilate / avoid sources",
        f"{Level.ERR.emoji} High         — ventilate, filter, reduce sources",
    ]

    lines.extend(["", *aqi])

    return "\n".join(lines)


def format_status_block(
    pm25: float, pm10: float, ts_utc: datetime, *, level: Level, tz=timezone.utc
) -> str:
    """Compact block used for alert fan-out."""
    return (
        f"<b>{level.emoji} Air Quality</b>\n"
        f"PM2.5: <b>{fmt_num(pm25)}</b> µg/m³\n"
        f"PM10 : <b>{fmt_num(pm10)}</b> µg/m³\n"
        f"<i>at {fmt_dt(ts_utc, tz)}</i>"
    )
