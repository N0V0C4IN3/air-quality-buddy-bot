"""Telegram HTML formatting. No thresholds of its own — the level is decided by
`common.air_quality` and handed in.
"""
from __future__ import annotations

from datetime import datetime, timezone

from common.air_quality import Level, Thresholds


def fmt_num(x: float) -> str:
    # 1 decimal, thin space thousands if needed
    return f"{x:,.1f}".replace(",", " ")


def fmt_dt(dt_utc: datetime, tz=timezone.utc) -> str:
    # Show local time with timezone
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def format_status_block(
    pm25: float, pm10: float, ts_utc: datetime, *, level: Level, tz=timezone.utc
) -> str:
    return (
        f"<b>{level.emoji} Air Quality</b>\n"
        f"PM2.5: <b>{fmt_num(pm25)}</b> µg/m³\n"
        f"PM10 : <b>{fmt_num(pm10)}</b> µg/m³\n"
        f"<i>at {fmt_dt(ts_utc, tz)}</i>"
    )


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
