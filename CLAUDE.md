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

The tunnel is behind a compose profile: `docker compose --profile tunnel up -d`.

Migrations run automatically on container start (`alembic upgrade head && python …` in the sensor-reader and reporter-bot Dockerfiles). To author one locally, `DATABASE_URL` must be set — `alembic/env.py` reads it from env/`.env` and overrides `sqlalchemy.url`.

### Tests

The suite runs on Windows with no Postgres, Redis, RabbitMQ or sensor attached —
SQLite on a temp file, an in-memory subscriber cache, a fake serial port.

```bash
python -m pip install -r requirements-dev.txt   # pytest alone is not enough
python -m pytest
```

Root `conftest.py` does the path setup: the services are not packages, so their
directories go on `sys.path` to reproduce the flat imports used in the images.
Three services have a `config` module — `sensor-reader` wins a bare
`import config`; reporter-bot's is loaded by file path in `tests/test_config.py`,
and `web-api` goes on the *end* of `sys.path` for the same reason (its
testable modules take their dependencies as arguments and never import config).
Each config module calls `Settings.load()` at import, so `conftest.py` sets the
sensor-reader variables before collection.

No linter. CI is `.github/workflows/ci.yml`: the suite on 3.11 and 3.14, the
suite again with reporter-bot's requirements installed so the aiogram-dependent
tests run rather than skip, all four images built with each entrypoint imported
inside its own image, and migrations applied/rolled back with a models-vs-schema
drift check on Postgres.

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
- **The inline keyboard is the whole menu.** There is no reply keyboard: every card (status, window, alert) carries the same buttons, and a view button edits the card in place. Status is a view beside the windows (`reports.STATUS`, a separate enum from `Window` because it has no range); `reports.View` is either and `ReadingReports.for_view` renders one. A card with nothing to draw is `Report.empty` and goes out as text with the buttons; tapping into a view with nothing to draw shows `Report.notice` as a pop-up and leaves the card alone, and tapping a chart on a text card sends a fresh photo, because a text message cannot be edited into one. Renders run on a one-worker thread pool (`bot.render`), off the event loop. The old reply-keyboard labels still have one handler, which removes that keyboard on first use. The info text is the one message that is not a card: it carries `keyboard_layout.info_rows` - Back deletes it (`x:back`, leaving the card it was opened from in view) and Status swaps it for a status card (`x:status`); past Telegram's 48-hour delete window only its buttons are removed.
- **An alert is never edited.** Its buttons are `card_rows(None, …)`: no view is marked and a view tap opens a new card (`n:`), so the record of what tripped the threshold stays in the chat. Theme and subscribe taps on an alert only redraw its buttons.
- **No `web_app` button outside private chats.** Telegram rejects the whole message, not just the button, so `bot.card_buttons` drops Dashboard for a negative (group/channel) chat id. On an alert that would otherwise mean no alert at all.
- **Inline button labels are width-budgeted.** Telegram divides a row equally and truncates whatever overflows, so `keyboard_layout.card_rows` (no aiogram import, therefore testable; `markup.py` only translates it) keeps every row to three buttons at most and the tests hold each label to its share of `ROW_BUDGET`. Adding a fourth button to the time-window row re-breaks it.
- **Callback data is a wire format.** `callbacks.py` owns `<action>:<slug>[:<arg>]` (documented there), deliberately free of any aiogram import so it stays testable. Settings buttons carry the state they set (`theme:<card>:dark`, `sub:<card>:off`), never "toggle": a label on an old card is frozen. Changing a slug orphans every button already on someone's screen, though `bot.button_handler` answers an undecodable one rather than leaving it spinning; Telegram caps the payload at 64 bytes.
- **One `Database` per process.** Constructed once in `bot.py` / `main.py` and passed in; never build one inside a handler or a loop pass — that creates a fresh engine and pool each time.
- **aiogram is pinned to 2.25.1** — decorator/`executor.start_polling` style, not the v3 Router API.
- **Thresholds have one owner.** `common.air_quality.Thresholds` is built from env once per service and passed around; nothing else should read `PM25_WARN` and friends or re-implement the comparison.
- **Alembic owns the whole schema; `create_all` is test-only.** Until 2026-08-31 the
  baseline migration was empty and `readings` was created by `Database.create_all()` at
  sensor-reader startup, which meant a new column on `Reading` would never reach an
  existing database — `create_all` adds a missing table, it cannot alter one. Migration
  `a1292fda24f3` adopts the table (guarded, so it is a no-op where it already exists) and
  the call is gone from the service. `Database.create_all` now has one caller,
  `tests/conftest.py`. A model change without a migration fails CI's drift check.
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
  the same series and threshold hex values as CSS custom properties, and the amber/red
  hues stay reserved for threshold state. Change one side and change the other, or the
  Telegram card and the web view stop looking like the same product. The frosted
  surfaces (`--bg`, `--card`, the glows) are the dashboard's own.
- **Static files are `no-cache`, and their URLs carry `?v=`.** Telegram's iOS web view
  reuses an asset with no Cache-Control indefinitely, so a redesign once shipped as the
  new `index.html` over the old `app.js` and `styles.css`. `RevalidatedStaticFiles` in
  `web-api/api.py` makes every reuse a 304 check; the `?v=` on every `<link>`, `<script>`
  and module import is for copies cached before that header existed - keep it identical
  everywhere (a test holds it) or a module loads twice.
- **The dashboard draws its own SVG.** No chart library and no build step:
  `static/charts.js` draws the rings, the main chart, the 90-day timeline and the
  pattern panels; `static/clock.js` formats every time in `meta.timezone` through Intl,
  never the browser's zone. Charts colour themselves with `var(--…)`, so a theme switch
  is a CSS change, not a redraw.
- **One filter drives the page.** Range (a preset the server resolves - `today` and
  `yesterday` are local calendar days - or an explicit pair), detail, pollutants and
  shading all live in the URL. The page then steps from the instants the server
  answered with. `/api/days` feeds the timeline (one row per local day, rolled up from
  hourly buckets because SQL buckets are UTC-aligned); `/api/episodes` merges readings
  over a warn limit into stretches, with gaps of up to two intervals bridged.
- **`DASHBOARD_URL` must be HTTPS or absent.** Telegram refuses a `web_app` button with a
  plain-http URL, and that failure would take down every chart card, not just the button —
  so `reporter-bot/config.py` rejects it at boot and an unset value simply hides the button.
- **Config is parsed once, at boot.** Each service's `Settings.load()` reads its whole environment and raises `ConfigError` on a missing required value. Don't reach for `os.getenv` elsewhere — add the field to `Settings`.
