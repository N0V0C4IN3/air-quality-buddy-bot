# Autoresearch: work per user action

## Objective

Reduce what the Raspberry Pi spends to serve one of everything a person can ask
this stack for: the eight Telegram chart cards (four windows x two themes), the
status card, an alert fan-out to 100 subscribers, and two dashboard refreshes.

The workload is `.auto/bench.py`, run against a fixed 10-day SQLite database
seeded at the deployed 30-second reading interval (28,800 rows) and a pinned
clock, so "Last 7d" means the same 7 days in run 1 and run 400.

**The known headroom, measured before the run started:** the bot's window path
pulls every row into Python and hands ~20,000 points to matplotlib for a single
7-day card. Routing it through `ReadingRepository.get_buckets` - which already
exists, already handles both dialects, and is already what the dashboard uses -
took that card from 1,767 ms to 525 ms and its PNG from 118 KB to 84 KB in a
throwaway prototype. Most of the win is inside matplotlib, not the database:
20,158 points across a 7-inch figure at 150 dpi is roughly nineteen strokes per
horizontal pixel, all of which the anti-aliaser then discards.

## Metrics

- **Primary**: `card_ms_total` (ms, lower is better) - the eight chart cards,
  each the `min` of 3 repetitions, summed. A sum rather than a median because
  it cannot be gamed by speeding up a cheap card, and it moves when the
  expensive one moves.
- **Secondary, zero noise** - these have no measurement variance at all and are
  what should arbitrate when the stopwatch is ambiguous:
  - `rows_to_python` - ORM instances hydrated per sweep (counted via
    SQLAlchemy's `loaded_as_persistent`, so it collapses the moment a path
    stops hydrating objects and starts reducing in SQL)
  - `service_calls` - queries per sweep
  - `fanout_calls` / `fanout_cache_ops` / `fanout_renders` - the alert path
  - `bytes_total` - PNG bytes across the eight cards
  - `dash_calls` / `dash_bytes` - one dashboard refresh, twice
- **Diagnostic**: `canary_ms`, `canary_mean_ms` - a fixed tiny render
  interleaved between cards. Reported, never divided out. See the noise floor
  below.

### The noise floor - read this before trusting a small movement

The benchmark runs on a Windows desktop that its owner also uses. Measured
repeatedly on unchanged code:

- quiet machine, back-to-back: 6880.5 and 6877.3 ms (0.05% apart)
- same code, owner browsing: 9559 - 13707 ms
- owner still browsing, after `raise_priority()`: 6754 - 7592 ms

That last line is the fix. `bench.py` asks Windows for ABOVE_NORMAL scheduling,
which puts it above browser renderers competing for the same core, and it
recovered the quiet-machine numbers on a busy box: the same code that measured
10547 - 11674 ms measured 6754 - 7592 ms one minute later. Not `HIGH` - that
makes the desktop sluggish, which is a rude thing for a background loop to do.

What is left is a **~12% noise floor**, and a single reading 10% below the last
one is not evidence of anything. Rules that follow:

1. A movement under ~15% is not believed without a re-measure. If it is still
   ambiguous, **A/B it against the machine as it is right now** rather than
   against a number recorded an hour ago:

   ```
   git stash push -q -- <changed paths>
   bash .auto/measure.sh        # the old code, this machine, this minute
   git stash pop -q
   bash .auto/measure.sh        # the new code
   ```

   This settled run 4, where the raw number looked 45% worse than run 3 and the
   controlled comparison showed a 3.6% improvement - the whole difference was
   the owner starting to use the desktop again.
2. When the stopwatch is ambiguous, `rows_to_python`, `service_calls` and
   `bytes_total` decide - they are exact.
3. `canary_mean_ms` sits near 40 on this machine with priority raised. Above
   ~55 means something is competing anyway - re-measure before logging a
   regression.

Every experiment worth doing in this session moves the primary metric by more
than 3x, so this floor is survivable. It is only a problem for micro-tuning,
which is not where the value is here.

**Do not try to normalise by the canary.** It was tried and it failed: over one
batch of five runs the ratio held to 6% against a 43% raw spread, and over the
very next batch the canary moved 41% while the sweep moved 3%, spreading the
"normalised" metric 61% - worse than the raw number. A 50 ms canary samples one
instant; a 10 s sweep averages ten seconds of machine state. `process_time` was
also tried and is worse still: on Windows it is quantised to the 15.6 ms
scheduler tick and counts every thread numpy spawns.

## How to run

```
bash .auto/measure.sh > .auto/run.log 2>&1; echo "exit=$?"
grep '^METRIC ' .auto/run.log
bash .auto/checks.sh > .auto/checks-run.log 2>&1; echo "exit=$?"
```

`python .auto/bench.py --verbose` gives a per-card breakdown by hand. The
seeded database is cached and rebuilt only when `FINGERPRINT_KEYS` changes; it
is built with `alembic upgrade head`, not `create_all`, so a model change with
no migration fails loudly here instead of benchmarking a schema production will
never have.

## Files in scope

| File | What it does, and why it is in scope |
|---|---|
| `reporter-bot/reports.py` | Owns the query, the UTC round-trip and the frame every card is drawn from. `_frame` is the 20k-row path. |
| `reporter-bot/charts.py` | All rendering. Figure construction, dpi, the smoothing window, the stats footer. |
| `reporter-bot/subscriptions.py` | `theme()` opens a session per call; the fan-out calls it once per subscriber. A bulk `themes()` is the obvious fix. |
| `reporter-bot/subscriber_cache.py` | The cache seam behind `Subscriptions`. |
| `reporter-bot/bot.py` | Only `_on_alert`'s fan-out loop, and only to keep it in step with a new `Subscriptions` API. |
| `common/db.py` | `ReadingRepository`. New query shapes belong here, beside `get_buckets`. |
| `web-api/service.py` | `summary()` runs 3 queries across 2 sessions; `_buckets` opens its own. |
| `tests/` | Tests for behaviour you change or add. Never delete a test to make a check pass. |

## Off limits

- `.auto/` - never in scope for an experiment, so a discard cannot destroy the
  log that records it.
- `alembic/`, `alembic.ini` - unless a scope change genuinely needs a schema
  change, in which case the migration ships in the same commit or CI's drift
  check fails.
- `reporter-bot/callbacks.py` slugs - a wire format. Renaming one orphans every
  button already on someone's screen. The module may be read, not re-slugged.
- `common/air_quality.py` - `Thresholds` is the single owner of the comparison.
  Inlining it for speed is a regression even if the metric improves.
- `sensor-reader/` - that is session two's territory. Changing the sampling
  interval would move these numbers for reasons that have nothing to do with
  this session's work.
- `docs/`, `CLAUDE.md`, `.gitignore` - the owner has uncommitted work in the
  last two. Never `git add -A`; add explicit paths only.

## Constraints

1. **The suite passes.** `.auto/checks.sh` runs it every iteration.
2. **The chart still tells the truth.** `.auto/fidelity.py` asserts the largest
   plotted value equals the true maximum over the range, exactly. Bucketing is
   encouraged; bucketing to *averages alone* is not, because it hides the spike
   the alert fired on - a 5-minute mean of one 90 ug/m3 minute and nine quiet
   ones reads as a comfortable 15. Carry the per-bucket max, the way
   `get_buckets` already returns it and the way the dashboard already shades it.
3. **Threshold hues stay reserved.** Amber and red mean threshold state; a
   series never borrows one, and the level word always sits beside the colour.
4. **One `Database` per process.** Never construct one inside a handler or a
   loop pass. The easiest way to fake a latency win is to build an engine
   somewhere it should not be built.
5. **No new runtime dependencies** without saying so in the log line - there is
   no shared requirements file, so a new import in `common/` has to be added to
   every service that imports it.
6. **`bot.py` must still import.** aiogram 2.25.1 does not install on the
   Python this repo is developed on, so nothing local exercises it; CI's
   `images` job is what proves it. Keep changes there mechanical.

## What's been tried

Run 0 is the baseline (6665 ms, median of three). Runs 1-10 below; the log has
the numbers and the `asi` for each.

**Kept, in order of what they were worth:**

1. **Reduce the window frame in SQL (run 1)** - the big one. `reports._frame`
   now picks a bucket width from the span and goes through `get_buckets`.
   6665 -> 3724 ms, `rows_to_python` 87360 -> 3120. Every point carries the
   min/max behind it and the chart shades that band, because bucketing to
   averages alone hides the spike an alert fired on.
2. **Drop `bbox_inches="tight"` (run 3)** - it is a second full render pass.
   3724 -> 1984 ms, status card 180 -> 92 ms. Every figure sets explicit
   margins instead, and those margins are load-bearing.
3. **dpi 150 -> 125 (run 5)** - the only lever that moves time and bytes at
   once. 1875 -> 1781 ms and 611 -> 487 KB under a matched-canary A/B.
4. **`Subscriptions.themes()` (run 2)** - `fanout_calls` 102 -> 3, flat in
   subscriber count instead of linear.
5. **`MAX_POINTS` 1500 -> 800 (run 7)** - sized to the card's own plot area
   rather than borrowed from the dashboard. `rows_to_python` 3120 -> 240.
6. **One look per alert (run 8)** - `get_recent(12)` + `get_first_since()`
   instead of a whole hour, and `alert_reports()` renders every theme from one
   look. `rows_to_python` 240 -> 26, `fanout_rows` 340 -> 13.
7. **`summarise()` (run 6)** - extremes and level split in one scan.
   `dash_calls` 12 -> 10.
8. **`date2num` hoisted (run 4)** - 3.6% under a controlled A/B.
9. **Palette PNGs from the canvas buffer (run 13)** - the best byte result of
   the run and free on time. `bytes_total` 488 -> 148 KB *and* card_ms_total
   2964 -> 2845, because encoding a palette PNG is cheaper than encoding RGBA
   by more than the quantise costs. Use FASTOCTREE, not the default median cut.
10. **Four y gridlines (run 14)** - tick labels were 19 of ~45 text objects.
    -6% isolated, and the gridlines now land on the warn/high limits.
11. **Postgres dialect tests (run 10)** - no metric movement; closes a risk this
    session created by putting `get_buckets` under every card.
12. **`Window.bucket_floor_seconds` (run 12)** - the heatmap asks for hourly
    buckets. Too small for the sweep to see; kept because a count-weighted
    hourly mean is more correct than averaging four bucket means in pandas.
13. **Series at 1dp (run 11)** - `dash_bytes` 52.4 -> 50.7 KB, and the second
    decimal was invented on the way out.

**Rejected:**

- **`text.hinting="none"` (run 9)** - 6% faster, 4% more bytes, blurrier
  numerals. Wrong trade on a card people read values off.
- **`process_time`, canary-normalisation** - see the noise floor section.
- **Figure pooling** - measured: scaffolding is only 19 ms of a ~210 ms card,
  and reusing figures risks state leaking between renders. Not worth it.

**Where the time goes now.** Text layout and glyph rendering is the largest
single item; `_frame` is next but is partly a SQLite artifact; figure
scaffolding is ~19 ms and not worth pooling. After run 14 the tick labels are
down to ~15 text objects, leaving the stats footer (15) as the biggest
remaining block - and that one is deliberate design (CLAUDE.md: the table left
a `<pre>` block precisely so decimal points stack in proportional type).

**Every tracked metric is now close to its floor**: `rows_to_python` 26,
`service_calls` 12 (one per card, two per status card), `fanout_calls` 3 and
flat in both subscribers and themes, `bytes_total` 145 KB after palettising.
Further work on the primary means micro-tuning below the noise floor, which is
why the remaining ideas in `ideas.md` are all marked small.

**Measure only on a quiet machine.** Check `canary_ms` before believing
anything: ~32 is quiet, >55 is contended, and it reached 94 during this run.
For anything worth less than ~15%, isolate the affected cards and time them
directly rather than re-running the 8-card sweep - that is how runs 12 and 14
were settled.

**Two traps recorded for whoever picks this up:**

- **A rendered-card cache would game this benchmark.** Reps 2 and 3 would hit
  the cache and `min` would report near-zero. It is also speculative for a bot
  serving one household. If it is ever wanted, measure the cold path first.
- **`_frame`'s SQL cost is partly a SQLite artifact.** `get_buckets` groups on
  `strftime('%s', ...)`, which parses text per row; Postgres uses
  `extract('epoch')` on a real timestamptz. Do not spend iterations optimising
  a cost production may not pay.
