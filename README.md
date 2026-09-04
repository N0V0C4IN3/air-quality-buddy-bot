# Air Quality + Telegram Bot Services
### Service Description
* **sensor-reader** service reads the SDS011 sensor's data (PM2.5 and PM10) writes it to db.
* **reporter-bot** service fetches the reports from db (Today, Last 12h, Last 7d).
* **housekeeper** cleans the db
* **web-api** serves a web dashboard: the same readings with a time frame you pick,
  interactive charts and a layout you choose. Opens as a Telegram Mini App.

### Features:
- Wake → warm up → read → sleep cycle per sample (protects sensor + saves power)
- Stores PM2.5 / PM10 in your DB
- Telegram bot: latest reading, Today / 12h / 7d charts, subscribe/unsubscribe toggle
- Charts stack PM2.5 and PM10 in their own panes, each shaded with its own warn/high bands
- Inline buttons switch window in place; 7d is smoothed with the raw readings kept underneath
- Patterns view: an hour-of-day heatmap showing *when* the air is worst
- /status renders a card PNG: a level bar against that pollutant's own limits, an hour-long sparkline, the trend vs an hour ago, and a text notice when the sensor goes quiet
- Per-chat light or dark charts
- Optional alerts with cooldown, delivered as the same card PNG the status button sends
- Web dashboard: any time frame (presets or a custom from/to), resolution from every
  reading up to daily means, panels you turn on and off, hover values and a linkable URL
- Housekeeper to prune old data
- Timezone-aware charts

## Tests
No services required — the suite runs against SQLite, an in-memory cache and a fake serial port:
```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Setup
To start all services, run this command in root directory:
```bash
docker compose up --build -d
```
To start a separate service, run:
```bash
docker compose up --build db service-name -d
```
Create the .env file in the root directory. Every service parses its whole
environment at boot, so a missing required value fails the container
immediately rather than on the first query or alert.

```bash
TELEGRAM_TOKEN=telegram bot token 
DATABASE_URL=db url
SDS011_PORT=/dev/ttyUSB0
READ_INTERVAL_SECONDS=300
TIMEZONE=Europe/Kyiv

# extremum values for the sensor's readings
PM25_WARN=35
PM10_WARN=50
PM25_ERR=75
PM10_ERR=100

# broker (required by sensor-reader and reporter-bot)
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASS=guest
AQ_EXCHANGE=aq.alerts
AMQP_URL=amqp://guest:guest@rabbitmq:5672/
AQ_QUEUE_REPORTER=reporter-bot-alerts
# optional: defaults to the alerts.warn + alerts.err bindings
# AQ_ROUTING_KEYS=alerts.warn,alerts.err

# cache (required by reporter-bot)
REDIS_HOST=redis
REDIS_PORT=6379

# web dashboard (required by web-api)
WEB_AUTH_MODE=telegram        # telegram | token | public
# WEB_ACCESS_TOKEN=            # required only by WEB_AUTH_MODE=token
WEB_PORT=8080
# WEB_MAX_POINTS=1500         # widen buckets rather than send more points than this
# The HTTPS URL the dashboard is reachable at. Set it and reporter-bot puts a
# Mini App button in the chat menu and under every chart. Must be https.
# DASHBOARD_URL=https://air.example.com
# CLOUDFLARE_TUNNEL_TOKEN=     # only for `--profile tunnel`

# bot setup
ENABLE_ALERTS=true
ALERT_COOLDOWN_SECONDS=1800   # same level stays quiet for this long; warn->err always gets through

# housekeeper setup
PRUNE_ENABLED=true
PRUNE_MAX_AGE_DAYS=90   # keep last 3 months
PRUNE_INTERVAL_HOURS=24  # run once a day

# Sleep→Wake→Read→Sleep behavior
SDS011_WARMUP_SECONDS=30      # wait after WAKE before reading
SDS011_READ_TIMEOUT_S=2       # per-frame read timeout
SDS011_RETRIES=6              # attempts to grab a valid frame
SDS011_PERSIST_CFG=false      # true = write ACTIVE/period to EEPROM
```

## Web dashboard

The Telegram bot sends PNGs, so it has no hover layer and no way to ask for
"last Tuesday afternoon". The dashboard is where that lives.

`web-api` is a read-only FastAPI service over the same Postgres. It aggregates
in SQL — ask for ninety days and you get bucketed means with each bucket's
min/max spread behind them, not 26k points — and serves a small static page
(vanilla JS + uPlot, no build step) from the same container.

```bash
docker compose up --build db web-api -d
# http://localhost:8080  (WEB_AUTH_MODE=public or token while you look around)
```

### Endpoints

| Route | What it answers |
| --- | --- |
| `GET /api/health` | liveness; the only route outside the guard |
| `GET /api/meta` | timezone, reading interval, retention, thresholds, stored theme |
| `GET /api/latest` | newest reading with its level and staleness |
| `GET /api/series` | bucketed PM2.5/PM10 for a range, columnar |
| `GET /api/summary` | avg/min/max, share of time per level, worst and best hour |
| `GET /api/patterns` | weekday x hour grid and the hour-of-day profile |
| `POST /api/theme` | store light/dark on the viewer's chat row |

Every data route takes either `range=<1h|12h|today|24h|7d|30d|90d>` or an
explicit `from=` / `to=` ISO 8601 pair, plus an optional
`bucket=<raw|5m|15m|1h|6h|1d>`. A bucket that would blow the point budget is
widened rather than served.

### Access

`WEB_AUTH_MODE` picks who gets in:

- `telegram` (default) — only a Mini App launch, verified by HMAC over Telegram's
  signed `initData` with the bot token. A plain browser gets a 401.
- `token` — `?token=<WEB_ACCESS_TOKEN>` also works, for a link you paste anywhere.
- `public` — no check. Fine for a LAN-only deployment.

A verified Telegram viewer is identified by chat id, which is how the dashboard
reads and writes the same `chats.chart_theme` the bot renders its PNGs with.

### Exposing it

`cloudflared` is in the compose file behind a profile, so nothing on the Pi has
to listen on a public port:

1. Create a tunnel in the Cloudflare Zero Trust dashboard and copy its token.
2. Add a public hostname routing to `http://web-api:8080`.
3. Put `CLOUDFLARE_TUNNEL_TOKEN=` in `.env` and set `DASHBOARD_URL` to the hostname.
4. `docker compose --profile tunnel up -d`

### Reaching it from Telegram

With `DASHBOARD_URL` set (HTTPS only — Telegram refuses anything else),
reporter-bot puts a **Dashboard** button in the chat menu at startup and an
**Open dashboard** button under every chart card.

For a link in the bot's description, use a `t.me` deep link rather than the raw
URL: a browser opening the site directly carries no `initData` and, under the
default `telegram` mode, will be refused. Either point the description at
`https://t.me/<your_bot>` and let people use the menu button, or set
`WEB_AUTH_MODE=token` and put the tokenised URL there.
