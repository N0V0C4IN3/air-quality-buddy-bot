# Air Quality + Telegram Bot Services
### Service Description
* **sensor-reader** service reads the SDS011 sensor's data (PM2.5 and PM10) writes it to db.
* **reporter-bot** service fetches the reports from db (Today, Last 12h, Last 7d).
* **housekeeper** cleans the db

### Features:
- Wake → warm up → read → sleep cycle per sample (protects sensor + saves power)
- Stores PM2.5 / PM10 in your DB
- Telegram bot: latest reading, Today / 12h / 7d charts, subscribe/unsubscribe toggle
- Optional alerts with cooldown
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
