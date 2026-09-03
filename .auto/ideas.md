# Ideas not yet tried

Ordered roughly by expected leverage. Prune as they are tried - a stale entry
costs the next agent a re-derivation.

## Data path

- **Bucket the window frame in SQL.** `reports._frame` -> `get_buckets` with a
  width chosen from the span, the way `ranges.choose_bucket` does it for the
  dashboard. Must carry per-bucket max, not just avg (see the fidelity guard).
  Prototyped at 3.4x on the 7d card before the run started.
- **A point budget for the bot**, mirroring `WEB_MAX_POINTS`. Makes the cost of
  a card independent of retention depth, which is the property that stops the
  7d card getting slower every week.
- **Patterns needs 168 rows, not 20,159.** It groups to hour-of-day and throws
  the resolution away; hourly buckets carry the identical heatmap. Probably the
  single largest ratio available.
- **`status()` runs two queries** - `get_latest` then a one-hour range. The
  range already contains the latest reading.

## Fan-out

- **`Subscriptions.themes(ids)`** - one `SELECT chat_id, chart_theme` instead of
  N sessions. `bench.py` already prefers a bulk API when one exists, so this
  moves `fanout_calls` without touching the benchmark.
- **Group subscribers by theme first**, then render, so the render loop is over
  themes rather than over people.

## Rendering

- **dpi.** 150 -> 100 took a 7d card from 84 KB to 49.5 KB in the prototype.
  Check legibility of the threshold labels before believing it.
- **Reuse the figure scaffolding** across cards - axes, spines, band patches
  and the footer layout are identical between renders; only the data changes.
- **`_stats_footer` builds its own axis per card.** Cheap, but it is a third
  subplot on every figure.
- **The smoothing convolution runs on the raw array**; after bucketing it may
  be redundant, and `smooth_window=13` was tuned for raw 30-second data.

## Dashboard

- **`summary()` is 3 queries across 2 sessions** - `get_aggregate` and
  `count_by_level` share a session, then `_buckets` opens another. One session
  would do.
- **`patterns()` and `summary()` both build hourly buckets** for the same range
  and throw one away.
- **No cache headers** on `/api/latest` or `/api/summary`, which change once per
  reading interval and are re-fetched every 30 s per open tab.
