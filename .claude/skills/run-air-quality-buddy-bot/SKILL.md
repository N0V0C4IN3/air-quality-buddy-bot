---
name: run-air-quality-buddy-bot
description: Build, run, drive and screenshot the air-quality stack — either locally with no Docker, sensor, Postgres, RabbitMQ, Redis or Telegram token, or on the real docker compose stack. Use when asked to run, start, launch, serve, smoke-test, screenshot or eyeball this project — the dashboard, the bot's chart/status/alert PNGs, the sensor-reader loop, or the containers.
---

# Running the air-quality stack

Four services around Postgres, RabbitMQ and Redis, normally started with
`docker compose up`. You usually don't need that: **`driver.py` stands the same
code up on a SQLite file** and reaches every user-visible surface the repo has.

| Surface | How the driver reaches it |
| --- | --- |
| sensor-reader's write loop | its real `main.py`, `DRY_RUN=true` → `FakeSampler` |
| reporter-bot's PNGs | `reports.ReadingReports` direct — no aiogram, no bot token |
| the web dashboard | web-api's own `main.py` under uvicorn + headless Chrome |

**What neither path runs:** the bot itself (no polling, no handlers, no
keyboards — aiogram 2.25.1 does not install on 3.14), the alert path end to end
(`FakeSampler` is always `ok`, so nothing is ever published), and subscriptions
(no Redis on the local path). The tests cover those seams; CI covers the images
and the Postgres drift check.

Start local. Go to [docker compose](#run-docker-compose) when the change touches
Postgres dialect (locally only the SQLite `strftime` branch of `get_buckets`
runs), the broker, or an image.

All paths below are relative to the repo root, and every command was run there.

## Prerequisites

Python 3.11+ (developed on 3.14) and the dev requirements. Nothing else — no
Docker, no broker, no database server.

```bash
python -m pip install -r requirements-dev.txt
```

Chrome or Edge is needed only for `shot`; the driver finds either at its
standard Windows path.

## Run (agent path)

One command does everything and leaves artefacts on disk to look at:

```bash
python .claude/skills/run-air-quality-buddy-bot/driver.py all
```

That vendors uPlot, migrates and seeds a SQLite DB, runs the real sensor-reader
loop for four cycles, renders 12 PNGs, serves the dashboard, checks every route
and screenshots the page — about 90 seconds. Output lands in `.run-out/`
(gitignored):

```
.run-out/dev.db              seeded SQLite database
.run-out/charts/*.png        today / last_12h / last_7d / patterns / status / alert, light + dark
.run-out/dashboard.png       the dashboard, default 24h view
.run-out/dashboard-7d.png    the dashboard over the seeded pollution episode
.run-out/web-api.log         uvicorn's stdout
```

**Read the PNGs.** They are the deliverable — `all` passing only means nothing
raised.

### Individual steps

Each is also a subcommand, so you can re-run just the part you changed:

```bash
python .claude/skills/run-air-quality-buddy-bot/driver.py vendor          # fetch uPlot into web-api/static/vendor
python .claude/skills/run-air-quality-buddy-bot/driver.py seed --days 10  # alembic upgrade head + synthetic readings
python .claude/skills/run-air-quality-buddy-bot/driver.py sensor          # the real sensor-reader loop, ~8s
python .claude/skills/run-air-quality-buddy-bot/driver.py charts          # every bot PNG, both themes
```

For iterating on the dashboard, leave the server up in one shell and drive it
from another:

```bash
python .claude/skills/run-air-quality-buddy-bot/driver.py web             # serves http://127.0.0.1:8099 until Ctrl-C
python .claude/skills/run-air-quality-buddy-bot/driver.py smoke           # every route, in another shell
python .claude/skills/run-air-quality-buddy-bot/driver.py shot --name manual --url "http://127.0.0.1:8099/?range=30d&bucket=6h"
```

`web` takes `--auth public|token|telegram` (default `public`).

### Direct invocation

Most changes here are internal, and `reports`/`charts`/`ranges`/`service` all
take their dependencies as arguments rather than importing config — so you can
call them without any of the above. `exec` sets `sys.path` up the way
`conftest.py` does (three services have a `config` module, so the order
matters) and hands you `DB_URL` for the seeded database:

```bash
python .claude/skills/run-air-quality-buddy-bot/driver.py exec -c "
from datetime import timezone, timedelta
from common.air_quality import Thresholds
from common.db import Database
from reports import ReadingReports, Window
r = ReadingReports(Database(url=DB_URL), timezone(timedelta(hours=3)), Thresholds())
rep = r.for_window(Window.LAST_7D, theme='dark')
open('.run-out/one.png','wb').write(rep.chart.getvalue()); print(rep.text)
"
```

`--file script.py` runs a file the same way. **Do not set `PYTHONPATH` by hand
in Git Bash** — `$PWD` there is a POSIX path (`/c/Users/…`) that Python cannot
import from, and you get `ModuleNotFoundError: No module named 'reports'`.

## Run (docker compose)

Use this when the change touches Postgres dialect, the broker, or anything that
only exists in an image. Needs Docker Desktop running and a `.env` at the repo
root — one with dev values already exists (gitignored); README.md has the real
variable list.

Bring the infrastructure up first, then migrate and seed Postgres:

```bash
docker compose up -d db redis rabbitmq
python .claude/skills/run-air-quality-buddy-bot/driver.py seed-compose --days 10
docker compose up -d web-api housekeeper sensor-reader
docker compose ps --format '{{.Service}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'
```

The dashboard is then on `127.0.0.1:8080`, and the same driver steps point at
it:

```bash
RUN_WEB_PORT=8080 python .claude/skills/run-air-quality-buddy-bot/driver.py smoke
RUN_WEB_PORT=8080 python .claude/skills/run-air-quality-buddy-bot/driver.py shot --name docker-dashboard --url "http://127.0.0.1:8080/?range=7d"
```

Useful pokes, all verified:

```bash
docker compose logs housekeeper | tail -8
docker compose exec -T db psql -U postgres -d airqualitydb -c "select count(*), max(timestamp) from readings;"
docker compose exec -T rabbitmq rabbitmqctl list_exchanges name type
docker compose down            # add -v to drop the pgdata/rabbitmq/redis volumes
```

### Image checks (what CI's `images` job does)

Catches what pytest structurally cannot — a dependency `common/` uses that is
missing from one service's `requirements.txt`, or an import-time error in code
no test imports:

```bash
for svc in sensor-reader reporter-bot housekeeper web-api; do
  docker build -f "$svc/Dockerfile" -t "$svc:local" .
done
docker run --rm -e DATABASE_URL=sqlite:// -e DRY_RUN=true -e RABBITMQ_HOST=localhost \
  -e RABBITMQ_USER=guest -e RABBITMQ_PASS=guest -e AQ_EXCHANGE=aq.alerts \
  sensor-reader:local python -c "import main; print('sensor-reader ok')"
docker run --rm -e DATABASE_URL=sqlite:// housekeeper:local python -c "import main; print('housekeeper ok')"
```

All four build (housekeeper 262 MB, sensor-reader 724 MB, web-api 779 MB,
reporter-bot 1.12 GB) and all four import clean.

## Test

```bash
python -m pytest
```

303 passed, 1 skipped in ~22s. The skip is `test_markup.py` — `markup.py` is
the only module importing aiogram, which is deliberately not a test dependency.
CI un-skips it in a separate `aiogram-tests` job by installing
`reporter-bot/requirements.txt` **on 3.11**; aiogram is pinned to 2.25.1, which
predates the 3.14 this repo is developed on, so do not expect that install to
work locally. Let CI cover it.

## Gotchas

- **`web-api/static/vendor/` is empty in a fresh checkout** — only a `.gitkeep`.
  The Dockerfile curls uPlot at *build* time, so serving web-api straight from
  the repo gives a page that loads its shell, logs `uPlot is not defined`, and
  draws no charts. `driver.py vendor` fetches the same pinned 1.6.31. If you
  bump the version in `web-api/Dockerfile`, bump `UPLOT_VERSION` in the driver
  too.
- **A Russian-locale Windows console is cp1251, and every reading logs `µg/m³`.**
  Echoing a service's perfectly healthy log line kills the driver with
  `UnicodeEncodeError: 'charmap' codec can't encode character 'μ'`. The
  driver reconfigures its own stdout *and* sets `PYTHONIOENCODING=utf-8` for
  children — both are needed; fixing only the child moves the crash to the
  parent.
- **Headless Chrome needs a throwaway `--user-data-dir`.** Without one it
  attaches to the user's already-running Chrome, exits 0 and writes no file.
  It also needs `--virtual-time-budget`: the page fetches `/api/*` and *then*
  draws, so a screenshot without it captures the empty shell.
- **Seed data must cross the thresholds or half the UI is untested.** The seeder
  plants a two-day episode 1–3 days back, which is why `dashboard-7d.png` shows
  amber and red bands and a 77/14/8 level split while the default 24h view is
  all green. A flat series exercises no band, no level bar and no `warn`/`high`
  label.
- **The driver seeds with Alembic, not `create_all`.** `Database.create_all` is
  test-only in this repo (see CLAUDE.md) — it cannot alter an existing table, so
  a model change would silently not reach the DB. Running `alembic upgrade head`
  here means the driver also proves the migrations still apply.
- **sensor-reader needs no broker to run locally.** `FakeSampler`'s values are
  always `ok`, so the alert gate never publishes, and `sample_once` catches what
  does go wrong. Force an alert path by seeding high values instead, not by
  starting RabbitMQ.
- **`POST /api/theme` returning 403 is correct** under `WEB_AUTH_MODE=public`.
  A viewer with no verified `initData` has no chat row to write a theme onto.
  The smoke step asserts the 403, not a 200.
- **`?range=90d&bucket=raw` must come back small.** The point budget is a
  server-side rule: `choose_bucket` widens rather than honouring the request.
  The smoke step asserts ≤1500 points (it returns 41 on 10 days of seed).
- **Killing the shell that ran `driver.py web` may leave uvicorn listening.**
  Check with `Get-NetTCPConnection -LocalPort 8099 -State Listen`. `driver.py all`
  is not affected — it terminates the child in a `finally`.
- **A stray sensor-reader holds `dev.db` open**, and Windows will then refuse to
  delete it: `rm: cannot remove '.run-out/dev.db': Device or resource busy`. It
  also keeps appending rows, which quietly moves the numbers under you. Find it
  with `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId,CommandLine`
  and `Stop-Process`. Backgrounding the service through a shell pipeline is how
  you get one — `kill %1` ends the pipeline, not the Python process.
- **reporter-bot is the one service compose cannot fake.** It boots, migrates,
  connects Redis and RabbitMQ, then dies on
  `aiogram.utils.exceptions.Unauthorized` because the dev `.env` token is CI's
  dummy — and `restart: unless-stopped` turns that into a crashloop. Either put
  a real token in `.env` or leave it out of `docker compose up` and render its
  PNGs with `driver.py charts` instead. The dummy token is fine for every other
  service and for the boot-guard check.
- **sensor-reader *does* start on Windows** despite `devices: /dev/ttyUSB0` and
  `privileged: true` in the compose file — Docker Desktop tolerates the missing
  device, and `DRY_RUN=true` means it is never opened. Don't assume otherwise.
- **The alert path never fires locally, in Docker or out.** `FakeSampler` only
  ever produces `ok` levels, so the gate never publishes and `aq.alerts` is
  never even declared — `rabbitmqctl list_exchanges` shows no `aq.` entry after
  a clean run. Seed high values if you need to exercise it; do not read an empty
  exchange as a broken broker.
- **`web-api` does not migrate.** If you bring it up before anything has run
  `alembic upgrade head` against Postgres, it serves 500s on every data route.
  `driver.py seed-compose` runs the migration for you, inside the reporter-bot
  image (psycopg2 is in the image, not on Windows).
- **The sensor step waits for rows, not for the clock.** A cold start (first
  import of sqlalchemy, matplotlib and pika) can outlast a fixed sleep, which
  reported "wrote nothing" for a service that was merely still booting. Expect
  the log to show fewer readings than the row count — `terminate()` cuts the
  last lines mid-flush.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `UnicodeEncodeError: 'charmap' codec can't encode character 'μ'` | You are on a cp1251 console. Already handled in `driver.py`; if you wrote your own script, `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`. |
| `web-api never answered /api/health` | Read `.run-out/web-api.log` — it is uvicorn's stdout. Usually `ConfigError` from a missing env var; `service_env()` in the driver is the full list. |
| Dashboard screenshot renders the shell but no charts | uPlot missing. `driver.py vendor`. |
| `chrome wrote no screenshot` | Another Chrome held the profile, or the binary is not at a path in `CHROME_CANDIDATES` — edit that list in `driver.py`. |
| `sensor-reader wrote nothing` | Run `driver.py seed` first; `main.py` does not migrate, and it logs to stdout which the driver echoes. |
| Port 8099 already in use | `RUN_WEB_PORT=8100 python .claude/skills/run-air-quality-buddy-bot/driver.py all` |
| Compose data routes all 500 | Nothing migrated Postgres. `driver.py seed-compose`. |
| `reporter-bot` restarting forever | `Unauthorized` — the `.env` token is CI's dummy. `docker compose stop reporter-bot`. |
| `ModuleNotFoundError: psycopg2` | You are talking to Postgres from Windows. Go through a container (`seed-compose` does). |
