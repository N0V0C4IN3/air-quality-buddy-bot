from io import BytesIO
import pandas as pd
import matplotlib.pyplot as plt

def df_to_line_chart_png(df: pd.DataFrame, title: str = "Air Quality") -> BytesIO:
    if df.empty:
        raise ValueError("No data to plot")
    fig = plt.figure()
    ax = fig.gca()
    ax.plot(df["timestamp"], df["pm25"], label="PM2.5 (µg/m³)")
    ax.plot(df["timestamp"], df["pm10"], label="PM10 (µg/m³)")
    ax.set_title(title)
    ax.set_xlabel("Time")
    ax.set_ylabel("µg/m³")
    ax.grid(True)
    ax.legend()
    
    bio = BytesIO()
    fig.savefig(bio, format="png", bbox_inches="tight")
    plt.close(fig)
    bio.seek(0)
    return bio
