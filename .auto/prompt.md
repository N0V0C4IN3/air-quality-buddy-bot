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

1. A movement under ~15% is not believed without a re-measure.
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

Nothing yet - run 0 is the baseline, logged as the median of three consecutive
measurements so that later single measurements compare against it without bias.

Setup findings worth keeping:

- Three fixes for the noisy wall clock were evaluated during setup. Raising
  scheduler priority worked and is in `bench.py`. `process_time` and
  canary-normalisation were both rejected, with numbers, above. Do not
  re-derive them.
- The fidelity guard was negative-tested during setup: feeding it 5-minute
  averages fails it with `last_7d/pm25: peak lost - plotted 51.9, true 57.3`.
  It works.
- `service_calls` is 12 for a sweep: 8 window cards at 1 query each, plus 2
  status cards at 2 each. `fanout_calls` is 102 for 100 subscribers: 100
  per-chat theme lookups plus one `_recent` per rendered theme.
