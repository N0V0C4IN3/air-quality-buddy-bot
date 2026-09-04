# Autoresearch run: work per user action

Session 1 of the three proposed in `docs/metrics/autoresearch-metrics.html`.
Budget 30, spent 15. Stopped early — see *Why it stopped* below.

## Result

**`card_ms_total` 6665 → 1672 ms, −74.9%.** Confidence **11.22×** (improvement
4993 ms against a 445 ms median absolute deviation; anything above 2× is
likely real).

Both figures are quiet-machine measurements — baseline at `canary_mean 36.3`,
best at `31.5`. Runs 13 and 14 read higher only because the desktop was busy
(`canary_mean` 57 and 72); their real effects were measured by A/B in the same
minute.

| metric | baseline | best | change |
|---|---|---|---|
| `card_ms_total` (primary) | 6665 ms | 1672 ms | **−74.9%** |
| `card_ms_worst` | 1864 ms | 261 ms | −86% |
| `status_ms` | 134 ms | 88 ms | −34% |
| `rows_to_python` | 87,360 | **26** | −99.97% |
| `fanout_calls` (100 subscribers) | 102 | **3** | −97% |
| `fanout_rows` | 340 | 13 | −96% |
| `bytes_total` | 619 KB | 145 KB | −77% |
| `dash_calls` | 12 | 10 | −17% |
| `dash_bytes` | 52.4 KB | 50.7 KB | −3.3% |

The two `fanout` numbers are the ones that changed shape rather than size: the
alert path was linear in subscribers and is now flat in subscribers *and*
themes.

## What was kept, and what each bought

Grouped as shipped, not as run. Per-branch figures are that branch measured
alone against the baseline.

### 1. `perf/chart-rendering` — 6665 → 3300 ms, bytes −75%

Five changes to how a card is drawn and written out.

- **Drop `bbox_inches="tight"`** (run 3, the largest single win in the group:
  3724 → 1984 ms). It renders the whole figure, crops, and renders again —
  ~57 ms of a 376 ms card. Explicit margins replace it.
- **Palette PNGs from the canvas buffer** (run 13). `bytes_total` 488 → 148 KB
  *and* 4% faster. Expected to cost ~18 ms; encoding a palette PNG is enough
  cheaper than encoding RGBA to pay for the quantise outright.
- **125 dpi** (run 5). −5% time and −20% bytes together.
- **Four y gridlines** (run 14). −6% isolated; tick labels were 19 of ~45 text
  objects on a card.
- **Hoist `date2num`** (run 4). −3.6%; the same timestamp series was being
  converted eight times per card.

### 2. `perf/card-data-path` — 6665 → 2602 ms, rows 87,360 → 26

- **Reduce the window frame in SQL** (run 1, 6665 → 3724 ms). The dashboard
  had this rule since it shipped; the bot never got it. Carries per-bucket
  min/max so the peak survives — see *The guard that earned its place*.
- **`Subscriptions.themes()`** (run 2). `fanout_calls` 102 → 3.
- **`MAX_POINTS` 1500 → 800** (run 7). Sized to the card's own plot area
  rather than borrowed from a browser window.
- **One look per alert** (run 8). `get_recent(12)` + `get_first_since()`
  instead of a whole hour; `alert_reports()` renders every theme from one look.
- **`Window.bucket_floor_seconds`** (run 12). Kept on correctness, not speed.

### 3. `perf/dashboard-queries` — `dash_calls` 12 → 10

`summarise()` answers extremes and level split in one scan (run 6); the series
goes out at the sensor's own 1 decimal (run 11).

### 4. `test/postgres-dialect` — no metric movement

Closes a risk this run created. `get_buckets` has one dialect-specific line and
only the SQLite branch runs locally; this session put that call under *every*
card rather than one web panel.

## The guard that earned its place

The obvious way to make these cards cheap is to plot bucket averages, and it
silently destroys what the product exists to show. `.auto/fidelity.py` asserts
the largest plotted value still equals the true maximum, exactly. It was
negative-tested before being trusted, and it caught the real thing during
finalisation: `perf/card-data-path` applied *without* `perf/chart-rendering`
reports the 7-day peak as **51.4 instead of 57.3**.

## What failed

- **`text.hinting="none"`** (run 9, the only discard). 6% faster, 4% more
  bytes, blurrier numerals. Wrong trade on a card people read values off.
  `path.simplify_threshold` moved nothing — the lines are already simplified.
- **Canary normalisation** (setup). Held to 6% over one batch of five runs
  against a 43% raw spread, then the canary moved 41% while the sweep moved 3%
  and the "normalised" metric spread 61% — worse than the raw number. A 50 ms
  canary samples one instant; a 10 s sweep averages ten seconds.
- **`process_time`** (setup). Quantised to Windows' 15.6 ms scheduler tick and
  counts every thread numpy spawns. Noisier than the wall clock it replaced.
- **Figure pooling** (evaluated, not attempted). Scaffolding is only 19 ms of a
  ~210 ms card and reusing figures risks state leaking between renders.

Three of those four are measurement findings rather than code findings, which
is the shape of this run: **the harness took more work than any single
optimisation.** What finally worked was asking Windows for `ABOVE_NORMAL`
scheduling — the same code went 10.5 s → 6.8 s on a busy desktop — plus
A/B-ing against the machine as it is *right now* via `git stash`.

## Why it stopped at 15 of 30

Every tracked metric is close to its floor: `rows_to_python` 26,
`service_calls` 12 (one per card, two per status card), `fanout_calls` 3 and
flat, `bytes_total` 145 KB after palettising. Remaining ideas are all worth
less than the 12% noise floor, and the desktop was at `canary 94` against a
quiet 32 — three times slower — so further wall-clock tuning would have been
logging noise as findings.

## What I would try next

With a quiet machine and another budget, in order:

1. **Session 2 — sensor duty cycle.** Untouched and the largest remaining win
   in the project: ~50 s of fan and laser per reading at a configured 30 s
   interval means 100% duty and ~24 fan-hours a day against a rated life
   commonly cited at ~8,000 h. It is also a correctness bug — `main.py`'s sleep
   computes to zero, so the real period is ~50 s and nothing says so.
2. **`READ_INTERVAL_SECONDS` has two different defaults** — 30 in
   sensor-reader, 300 in reporter-bot and web-api. Unset, the readers' stale
   rule is ten times too lax and `bucket=raw` averages ten readings into one.
3. **Session 3 — image footprint.** ~2.9 GB across four images against a
   deploy rule that says stop below ~4 GB free. No image is multi-stage.
4. **The stats footer** is now the largest text block on a card (~15 of ~45
   objects). Cheaper to draw without a third subplot, but CLAUDE.md is explicit
   that it left a `<pre>` block so decimal points stack in proportional type —
   so this is a design conversation, not an optimisation.

Two traps recorded for whoever picks this up, both in `.auto/prompt.md`:

- **A rendered-card cache would game this benchmark.** Reps 2 and 3 would hit
  it and `min` would report near-zero. It is also speculative for a bot serving
  one household.
- **`_frame`'s SQL cost is partly a SQLite artifact.** `get_buckets` groups on
  `strftime('%s', ...)`, which parses text per row; Postgres uses
  `extract('epoch')` on a real timestamptz.
