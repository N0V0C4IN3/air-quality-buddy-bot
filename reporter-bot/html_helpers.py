"""Telegram HTML formatting.

No thresholds of its own — the level is decided by `common.air_quality` and
handed in. Everything here is text, so the status card costs no render and no
upload.
"""
from __future__ import annotations

from datetime import datetime, timezone

from common.air_quality import Level, Thresholds

METER_FULL = "▓"
METER_EMPTY = "░"
SPARK_TICKS = "▁▂▃▄▅▆▇█"

# Telegram rejects a photo caption over 1024 characters.
CAPTION_LIMIT = 1024


def fmt_num(x: float) -> str:
    # 1 decimal, thin space thousands if needed
    return f"{x:,.1f}".replace(",", " ")


def fmt_dt(dt_utc: datetime, tz=timezone.utc) -> str:
    # Show local time with timezone
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def meter(value: float, ceiling: float, width: int = 10) -> str:
    """Where a reading sits between zero and its high threshold."""
    if ceiling <= 0:
        return METER_EMPTY * width
    filled = round(max(0.0, min(1.0, value / ceiling)) * width)
    if value > 0:
        filled = max(1, filled)          # a real reading always shows something
    return METER_FULL * filled + METER_EMPTY * (width - filled)


def sparkline(values) -> str:
    """Recent history as one line of block characters."""
    points = [float(v) for v in values]
    if not points:
        return ""
    low, high = min(points), max(points)
    if high - low < 1e-9:
        return SPARK_TICKS[0] * len(points)
    span = high - low
    return "".join(
        SPARK_TICKS[min(len(SPARK_TICKS) - 1, int((v - low) / span * (len(SPARK_TICKS) - 1) + 0.5))]
        for v in points
    )


def trend(current: float, previous: float | None, *, deadband: float = 0.05) -> str:
    """Direction against an earlier reading. Rising particulates read as bad."""
    if previous is None or previous <= 0:
        return ""
    change = (current - previous) / previous
    if abs(change) < deadband:
        return "→ steady"
    arrow = "↑" if change > 0 else "↓"
    return f"{arrow} {abs(change) * 100:.0f}%"


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


def format_status_card(
    *,
    pm25: float,
    pm10: float,
    level: Level,
    observed_at: datetime,
    thresholds: Thresholds,
    spark: list[float] | None = None,
    pm25_before: float | None = None,
    pm10_before: float | None = None,
    now: datetime | None = None,
) -> str:
    """Level, magnitude, direction and freshness in one text block."""
    headline = {Level.OK: "good", Level.WARN: "elevated", Level.ERR: "high"}[level]
    pm25_trend = trend(pm25, pm25_before)
    pm10_trend = trend(pm10, pm10_before)

    lines = [
        f"<b>{level.emoji} Air quality — {headline}</b>",
        "<pre>",
        f"PM2.5  {meter(pm25, thresholds.pm25_err)}  {pm25:>5.1f}  {pm25_trend}".rstrip(),
        f"PM10   {meter(pm10, thresholds.pm10_err)}  {pm10:>5.1f}  {pm10_trend}".rstrip(),
    ]
    if spark:
        lines += ["", f"last hour  {sparkline(spark)}"]
    lines += ["</pre>", f"<i>updated {relative_time(observed_at, now)}</i>"]
    if pm25_trend or pm10_trend:
        lines[-1] = f"<i>updated {relative_time(observed_at, now)} · change vs 1h ago</i>"
    return "\n".join(lines)


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


def format_caption(title: str, stats: dict, count: int) -> str:
    """Chart caption: the stats table, small enough to ride along with the photo."""
    body = "\n".join([
        f"PM2.5  {stats['pm25_min']:>5.1f} · <b>{stats['pm25_avg']:.1f}</b> · {stats['pm25_max']:.1f}",
        f"PM10   {stats['pm10_min']:>5.1f} · <b>{stats['pm10_avg']:.1f}</b> · {stats['pm10_max']:.1f}",
        "       <i>min · avg · max</i>  µg/m³",
    ])
    caption = f"<b>{title}</b> · {count} samples\n<pre>{body}</pre>"
    return caption if len(caption) <= CAPTION_LIMIT else caption[:CAPTION_LIMIT]


def make_stats_table(pm25_min, pm25_avg, pm25_max,
                     pm10_min, pm10_avg, pm10_max,
                     count: int, title: str) -> str:
    headers = ["Metric", "Min", "Avg", "Max"]
    rows = [
        ["PM2.5", f"{pm25_min:.1f}", f"{pm25_avg:.1f}", f"{pm25_max:.1f}"],
        ["PM10",  f"{pm10_min:.1f}", f"{pm10_avg:.1f}", f"{pm10_max:.1f}"],
    ]
    colw = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def line(cols):
        return "  ".join(c.ljust(colw[i]) for i, c in enumerate(cols))

    body_lines = [line(headers), line(["—" * colw[i] for i in range(4)])]

    for r in rows:
        body_lines.append(line(r))
    body_text = "\n".join(body_lines)

    return (
        f"<b>{title}</b>\n"
        f"<pre>{body_text}</pre>\n"
        f"Samples: <b>{count}</b>"
    )


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
