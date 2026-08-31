from io import BytesIO
from typing import Union
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
try:
    from zoneinfo import ZoneInfo          
except Exception:                          
    from pytz import timezone as ZoneInfo  

def df_to_line_chart_png(df: pd.DataFrame, title: str = "Air Quality", tz: Union[str, object] = "UTC") -> BytesIO:
    if df.empty:
        raise ValueError("No data to plot")

    # Ensure datetime & sorted
    df = df.copy()
    # Treat incoming timestamps as UTC if naive; keep tz if already set.
    ts_utc = pd.to_datetime(df["timestamp"], utc=True)
    tzinfo = ZoneInfo(tz) if isinstance(tz, str) else tz
    ts_local = ts_utc.dt.tz_convert(tzinfo)
    df["ts_local"] = ts_local
    df.sort_values("ts_local", inplace=True)

    fig, ax = plt.subplots(figsize=(7, 4), dpi=120)

    ax.plot(df["ts_local"], df["pm25"], label="PM2.5 (µg/m³)")
    ax.plot(df["ts_local"], df["pm10"], label="PM10 (µg/m³)")

    ax.set_title(title)
    ax.set_xlabel(f"Time ({getattr(tzinfo, 'key', str(tzinfo))})")
    ax.set_ylabel("µg/m³")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(loc="upper left")

    # ---- tidy x-axis tick labels in the same timezone ----
    single_day = df["ts_local"].dt.normalize().nunique() == 1
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(locator)
    if single_day:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tzinfo))
    else:
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=tzinfo))

    fig.autofmt_xdate(rotation=0, ha="center")
    ax.margins(x=0.02)

    bio = BytesIO()
    fig.savefig(bio, format="png", bbox_inches="tight")
    plt.close(fig)
    bio.seek(0)
    return bio
