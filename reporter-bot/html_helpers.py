from zoneinfo import ZoneInfo
from datetime import datetime, timezone
import math
from config import settings

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:
    KYIV = timezone.utc  # graceful fallback

def fmt_num(x: float) -> str:
    # 1 decimal, thin space thousands if needed
    return f"{x:,.1f}".replace(",", " ")

def fmt_dt(dt_utc: datetime) -> str:
    # Show local time with timezone
    local = dt_utc.astimezone(KYIV)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")

def status_emoji(pm25: float, pm10: float, warn25=35, err25=75, warn10=50, err10=100) -> str:
    level = max(
        (pm25 >= err25) or (pm10 >= err10),
        (pm25 >= warn25) or (pm10 >= warn10)
    )
    # level is bool; we map explicitly:
    if (pm25 >= err25) or (pm10 >= err10):
        return "🔴"
    if (pm25 >= warn25) or (pm10 >= warn10):
        return "🟠"
    return "🟢"

def format_status_block(pm25: float, pm10: float, ts_utc: datetime) -> str:
    e = status_emoji(pm25, pm10)
    return (
        f"<b>{e} Air Quality</b>\n"
        f"PM2.5: <b>{fmt_num(pm25)}</b> µg/m³\n"
        f"PM10 : <b>{fmt_num(pm10)}</b> µg/m³\n"
        f"<i>at {fmt_dt(ts_utc)}</i>"
    )

def make_stats_table(pm25_min, pm25_avg, pm25_max,
                     pm10_min, pm10_avg, pm10_max,
                     count: int, title: str) -> str:
    headers = ["Metric", "Min", "Avg", "Max"]
    rows = [
        ["PM2.5", f"{pm25_min:.1f}", f"{pm25_avg:.1f}", f"{pm25_max:.1f}"],
        ["PM10",  f"{pm10_min:.1f}", f"{pm10_avg:.1f}", f"{pm10_max:.1f}"],
    ]
    colw = [max(len(h), max(len(r[i]) for r in rows)) for i,h in enumerate(headers)]

    def line(cols): return "  ".join(c.ljust(colw[i]) for i,c in enumerate(cols))
    body_lines = [line(headers), line(["—"*colw[i] for i in range(4)])]
    
    for r in rows: body_lines.append(line(r))
    body_text = "\n".join(body_lines)

    return (
        f"<b>{title}</b>\n"
        f"<pre>{body_text}</pre>\n"
        f"Samples: <b>{count}</b>"
    )

def info_text() -> str:
    # pull thresholds from your env-configured settings
    pm25_w, pm25_e = settings.pm25_warn, settings.pm25_err
    pm10_w, pm10_e = settings.pm10_warn, settings.pm10_err

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
        f"• <b>WARN</b>  if PM2.5 ≥ <b>{fmt_num(pm25_w)}</b> or PM10 ≥ <b>{fmt_num(pm10_w)}</b>",
        f"• <b>ERROR</b> if PM2.5 ≥ <b>{fmt_num(pm25_e)}</b> or PM10 ≥ <b>{fmt_num(pm10_e)}</b>",
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
        "🟢 Good (low)   — keep airing/filtration as is",
        "🟠 Elevated     — ventilate / avoid sources",
        "🔴 High         — ventilate, filter, reduce sources",
    ]

    lines.extend(["", *aqi])
    
    return "\n".join(lines)