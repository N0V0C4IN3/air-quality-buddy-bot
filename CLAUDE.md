# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Four Python services around a Postgres DB, a RabbitMQ broker and Redis, orchestrated by `docker-compose.yaml`:

- **sensor-reader** — polls an SDS011 particulate sensor over serial, writes `readings` rows, and publishes an AMQP alert when a reading is not `ok`.
- **reporter-bot** — aiogram (v2) Telegram bot: latest reading, Today / Last 12h / Last 7d stats + matplotlib chart, subscribe toggle. Also consumes the alert queue and fans alerts out to subscribers.
- **housekeeper** — infinite loop that prunes `readings` older than `PRUNE_MAX_AGE_DAYS`.
- **web-api** — read-only FastAPI service: JSON over the same `readings` table plus the
  static dashboard page it serves from `web-api/static`. Opens as a Telegram Mini App.

`common/` is shared by all four (copied into each image at `/app/common`) and holds three modules: `db.py` (SQLAlchemy `Base`, the `Reading`/`Chat` models, the `Database` session context manager, the two repositories), `air_quality.py` (`Thresholds` → `Level`, the single owner of the threshold rules), and `alerts.py` (the `Alert` payload + `alerts.<level>` routing-key grammar shared by publisher and consumer). Alembic migrations live at the repo root (`alembic.ini`, `alembic/`) and are shared too — schema changes go there, not into per-service code.

## Commands

All builds use the repo root as Docker build context (`context: .`, `dockerfile: <svc>/Dockerfile`), because each image copies `common/` (and, except web-api, `alembic/`).

```bash
docker compose up --build -d                 # all services
docker compose --profile tunnel up -d        # ...plus the Cloudflare tunnel
docker compose up --build db reporter-bot -d # one service (always bring up its deps)
docker compose logs -f reporter-bot
docker compose down
```

Migrations run automatically on container start (`alembic upgrade head && python …` in the sensor-reader and reporter-bot Dockerfiles). To author one locally, `DATABASE_URL` must be set (`alembic/env.py` reads it from env/`.env` and overrides `sqlalchemy.url`):

```bash
alembic revision --autogenerate -m "message"
alembic upgrade head
alembic downgrade -1
```

### Tests

The suite runs on Windows with no Postgres, Redis, RabbitMQ or sensor attached —
SQLite on a temp file, an in-memory subscriber cache, a fake serial port.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                      # whole suite
python -m pytest tests/test_reports.py # one file
python -m pytest -k cooldown           # one behaviour
python -m pytest tests/test_migrations.py  # runs the real Alembic scripts on SQLite
python -m pytest tests/test_alerting.py::test_escalation_is_never_suppressed
python -m pytest tests/test_web_api.py     # dashboard routes over SQLite, no server
```

Root `conftest.py` does the path setup: the services are not packages, so their
directories go on `sys.path` to reproduce the flat imports used in the images.
Three services have a `config` module — `sensor-reader` wins a bare
`import config`; reporter-bot's is loaded by file path in `tests/test_config.py`,
and `web-api` goes on the *end* of `sys.path` for the same reason (its
testable modules take their dependencies as arguments and never import config).
Each config module calls `Settings.load()` at import, so `conftest.py` sets the
sensor-reader variables before collection.

There is no linter or CI config in this repo.

## Things to know

- **No shared requirements file.** Each service has its own `requirements.txt`; a new dependency in `common/` must be added to every service that imports it (housekeeper deliberately has the smallest set — no pika/redis/aiogram).
- **Imports are flat.** Each Dockerfile copies the service directory to `/app`, so services do `from config import settings` / `from sensor import SensorReader` (not package-relative), while shared code is `from common.db import …`. Running a service outside Docker needs the repo root plus the service directory on `PYTHONPATH`.
- **Timestamps are UTC in the DB, local at the edges.** `Reading.timestamp` is written as `datetime.now(timezone.utc)`. `reports.Window.range()` builds ranges in the configured timezone and `ReadingReports` converts to UTC before querying; the chart axes convert back. Keep new query/display code on both sides of that seam.
- **Subscriptions go through one module.** `reporter-bot/subscriptions.py` owns the write order: Postgres `chats` (the record) first, then the cache. Handlers call `subs.subscribe/unsubscribe/toggle/all` — never Redis or `ChatRepository` directly. The cache has two adapters in `subscriber_cache.py` (Redis, in-memory).
- **Alert path:** `common/alerts.py` owns the payload and the routing key; `sensor-reader/publisher.py` (blocking `pika`) and `reporter-bot/consumer.py` (`aio_pika` robust) are adapters that only move bytes. `sensor-reader/alerting.py` decides *whether* to publish — same level is suppressed for `ALERT_COOLDOWN_SECONDS`, a warn→err escalation always passes, and a return to ok rearms the gate. Change the contract in `common/alerts.py`, not at either end.
- **Charts are PNGs, so there is no hover layer.** Identity comes from a titled pane per series plus a direct end label, and a threshold band always carries a text label — colour never carries meaning alone. `charts.py` owns the palette (validated for colour-vision safety); the amber/red band hues are reserved for threshold state and must never be used for a series.
- **Two pollutants, two panes.** A shared y-axis cannot carry two different warn limits honestly (PM2.5 warns at 35, PM10 at 50), which is why `window_chart` stacks them. `Y_FLOOR` keeps a quiet day from being autoscaled into a crisis.
- **Chart theme is a stored per-chat preference.** Telegram never tells a bot which theme the viewer uses, so `chats.chart_theme` holds it and `Subscriptions.theme()` reads it. The dark palette is stepped for the dark surface, not inverted.
- **Telegram cannot colour text.** The HTML subset is b/i/u/s/code/pre/a — no colour attribute anywhere, which is why anything that needs colour or alignment is rendered as an image. The text that remains is captions, and they carry colour only through the level emoji, always naming the level in words beside it. `Level.label` owns that word so the card and the caption cannot drift apart.
- **Every table is a PNG.** Telegram has no table markup — the tag set is b/i/u/s/code/pre/a/blockquote — so a text table can only be a `<pre>` block, and `<b>` inside one renders in a wider monospace face that shifts the whole row. The window stats, the status card and the worst/best hour are all typeset into the image by `charts.py`; captions are one line. Keep new tabular output on that side of the seam.
- **The status card is a chart too, and so is an alert.** `charts.status_card` draws the level bar (zero to that pollutant's *high* limit, warn marked), the sparkline and the trend; `reports.status_report()` and `reports.alert_report()` both return a `Report` like any window, with an empty `text` — both go out as the bare PNG, because the card already names the level and a caption only repeats it. Fan-out renders and uploads **once per theme** — Telegram returns a `file_id` for an uploaded photo and re-sending that id costs no bytes — so a hundred subscribers still cost two renders. Two paths stay text: a stale sensor (nothing to draw, and a dead reader must not wait on a render) and `reports.alert_text()`, the fallback when a render or send fails, because an alert must still arrive.
- **PNGs have no colour emoji.** Matplotlib's bundled font renders the level circles as tofu, so the status card draws a coloured dot and always names the level in words, and `charts.trend_text` uses arrows rather than the coloured squares the text path uses.
- **Inline button labels are width-budgeted.** Telegram divides a row equally and truncates whatever overflows, so `keyboard_layout.py` (no aiogram import, therefore testable) keeps the three time windows on short ASCII labels in one row and gives Patterns its own. Adding a fourth button to that row re-breaks it.
- **Callback data is a wire format.** `callbacks.py` owns the `w:<slug>` / `theme:<slug>` grammar, deliberately free of any aiogram import so it stays testable. Changing a slug orphans every button already on someone's screen; Telegram caps the payload at 64 bytes.
- **One `Database` per process.** Constructed once in `bot.py` / `main.py` and passed in; never build one inside a handler or a loop pass — that creates a fresh engine and pool each time.
- **aiogram is pinned to 2.25.1** — decorator/`executor.start_polling` style, not the v3 Router API.
- **Thresholds have one owner.** `common.air_quality.Thresholds` is built from env once per service and passed around; nothing else should read `PM25_WARN` and friends or re-implement the comparison.
- **The dashboard aggregates in SQL, not in Python.** `ReadingRepository.get_buckets`
  / `get_aggregate` / `count_by_level` reduce a range before it leaves the database —
  ninety days is ~26k rows and nothing downstream wants them individually. The one
  dialect-specific part is the epoch expression (`extract('epoch')` on Postgres,
  `strftime('%s')` on SQLite so the tests still run). Buckets are aligned to the epoch,
  not to the requested start, so panning a chart does not reshuffle its points.
- **`web-api` is read-only and does not migrate.** sensor-reader and reporter-bot own
  the schema; a reader racing them to `alembic upgrade` buys nothing. Its image copies
  `common/` but not `alembic/`.
- **The point budget is a server-side rule.** `ranges.choose_bucket` widens the bucket
  rather than honouring a request that would return more points than `WEB_MAX_POINTS`.
  `raw` is not a fixed width — it means one bucket per reading interval, which is why
  the pipeline needs no special case for unaggregated data.
- **The dashboard's auth has three modes, and only one of them identifies anyone.**
  `web-api/auth.py` verifies Telegram's `initData` HMAC with the bot token; a verified
  viewer carries a chat id, which is how the page reads and writes the same
  `chats.chart_theme` the bot renders PNGs with. `WEB_AUTH_MODE=token`/`public` visitors
  have no chat row, so `POST /api/theme` is a 403 for them by design — a plain browser
  link cannot carry `initData`, which is why the bot description should point at a
  `t.me` deep link rather than the raw URL.
- **The web palette mirrors `charts.py` exactly.** `web-api/static/styles.css` restates
  the same hex values as CSS custom properties, and the amber/red band hues stay reserved
  for threshold state. Change one side and change the other, or the Telegram card and the
  web view stop looking like the same product.
- **`DASHBOARD_URL` must be HTTPS or absent.** Telegram refuses a `web_app` button with a
  plain-http URL, and that failure would take down every chart card, not just the button —
  so `reporter-bot/config.py` rejects it at boot and an unset value simply hides the button.
- **Config is parsed once, at boot.** Each service's `Settings.load()` reads its whole environment and raises `ConfigError` on a missing required value. Don't reach for `os.getenv` elsewhere — add the field to `Settings`.

## Configuration

All services read a root `.env` via `env_file`. Beyond the variables documented in README.md, the code also requires:

- `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASS`, `AQ_EXCHANGE`, `AQ_EXCHANGE_TYPE`, `AMQP_HEARTBEAT` — sensor-reader publisher
- `AMQP_URL`, `AQ_EXCHANGE`, `AQ_QUEUE_REPORTER`, `AMQP_PREFETCH`, `AMQP_RETRY_DELAY` — reporter-bot consumer. `AQ_ROUTING_KEYS` is optional now; unset means the `alerts.warn` + `alerts.err` bindings from `common.alerts.binding_keys()`.
- `REDIS_HOST`, `REDIS_PORT` — reporter-bot
- `WEB_AUTH_MODE`, `WEB_ACCESS_TOKEN`, `WEB_PORT`, `WEB_MAX_POINTS`,
  `WEB_INIT_DATA_MAX_AGE` — web-api. It also reads `TELEGRAM_TOKEN` (to verify Mini App
  signatures) and `PRUNE_MAX_AGE_DAYS` (to tell the page how far back data goes).
- `DASHBOARD_URL` — optional, reporter-bot. HTTPS only; unset hides the Mini App buttons.
- `CLOUDFLARE_TUNNEL_TOKEN` — only used by `docker compose --profile tunnel`.
- `DRY_RUN=true` — `build_sampler` returns a `FakeSampler` instead of touching serial hardware (also implied when `SDS011_PORT` is unset). Use this to develop without the sensor.
