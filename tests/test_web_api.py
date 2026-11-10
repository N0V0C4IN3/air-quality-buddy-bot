"""The dashboard end to end: SQL aggregation, the service layer, the routes.

Same conditions as the rest of the suite — SQLite on a temp file, no server,
no Postgres, no Telegram.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from common.db import Database, ReadingRepository

from api import create_app
from auth import AccessGuard, AuthMode
from service import DashboardService
from test_web_auth import TOKEN, init_data

KYIV = ZoneInfo("Europe/Kyiv")
START = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
INTERVAL = 300


@pytest.fixture
def seeded(db: Database):
    """Two days of five-minute readings; PM rises through the afternoon."""
    with db.session() as s:
        repo = ReadingRepository(s)
        for i in range(576):                      # 48h at 5 minutes
            ts = START + timedelta(seconds=i * INTERVAL)
            hour = ts.astimezone(KYIV).hour
            pm25 = 10.0 + (30.0 if 14 <= hour < 18 else 0.0)
            repo.add(pm25=pm25, pm10=pm25 * 1.5, timestamp=ts)
    return db


@pytest.fixture
def service(seeded, thresholds):
    return DashboardService(seeded, KYIV, thresholds,
                            reading_interval_seconds=INTERVAL, retention_days=90)


@pytest.fixture
def client(service):
    app = create_app(
        service=service,
        guard=AccessGuard(AuthMode.TELEGRAM, bot_token=TOKEN),
        tz=KYIV,
    )
    return TestClient(app)


def auth():
    return {"X-Telegram-Init-Data": init_data()}


def window():
    """The seeded span, as the query string a browser would send."""
    return {"from": START.isoformat(), "to": (START + timedelta(days=2)).isoformat()}


# ---------- SQL aggregation ----------

def test_buckets_reduce_rows_in_sql(seeded):
    with seeded.session() as s:
        hourly = ReadingRepository(s).get_buckets(
            start=START, end=START + timedelta(days=2), bucket_seconds=3600
        )
    assert len(hourly) == 48
    assert sum(b.count for b in hourly) == 576
    assert all(b.count == 12 for b in hourly)


def test_buckets_are_aligned_to_the_epoch_not_to_the_start(seeded):
    """Panning a chart must not reshuffle the points under it."""
    with seeded.session() as s:
        repo = ReadingRepository(s)
        a = repo.get_buckets(start=START, end=START + timedelta(hours=6),
                             bucket_seconds=3600)
        b = repo.get_buckets(start=START + timedelta(minutes=17),
                             end=START + timedelta(hours=6), bucket_seconds=3600)
    # b starts mid-hour, so its first bucket is short - but it still begins
    # on the same boundary, not seventeen minutes later.
    assert a[0].start == b[0].start
    assert b[0].count < a[0].count
    assert [x.start for x in a[1:]] == [x.start for x in b[1:]]


def test_bucket_carries_the_spread_not_just_the_mean(seeded):
    with seeded.session() as s:
        daily = ReadingRepository(s).get_buckets(
            start=START, end=START + timedelta(days=2), bucket_seconds=86400
        )
    assert daily[0].pm25_min == 10.0
    assert daily[0].pm25_max == 40.0
    assert 10.0 < daily[0].pm25_avg < 40.0


def test_level_counts_match_the_threshold_rules(seeded, thresholds):
    with seeded.session() as s:
        levels = ReadingRepository(s).count_by_level(
            start=START, end=START + timedelta(days=2),
            pm25_warn=thresholds.pm25_warn, pm10_warn=thresholds.pm10_warn,
            pm25_err=thresholds.pm25_err, pm10_err=thresholds.pm10_err,
        )
    # The afternoon reading is PM2.5 40 / PM10 60 - over both warn limits,
    # under both high ones.
    assert sum(levels.values()) == 576
    assert levels["err"] == 0
    assert levels["warn"] == 96          # four hours a day, twice
    assert levels["ok"] == 480


def test_summarise_agrees_with_the_two_queries_it_replaces(seeded, thresholds):
    window = dict(start=START, end=START + timedelta(days=2))
    limits = dict(
        pm25_warn=thresholds.pm25_warn, pm10_warn=thresholds.pm10_warn,
        pm25_err=thresholds.pm25_err, pm10_err=thresholds.pm10_err,
    )
    with seeded.session() as s:
        repo = ReadingRepository(s)
        agg, levels = repo.summarise(**window, **limits)

        assert agg == repo.get_aggregate(**window)
        assert levels == repo.count_by_level(**window, **limits)


def test_summarise_of_an_empty_range_is_none_with_zero_counts(db, thresholds):
    with db.session() as s:
        agg, levels = ReadingRepository(s).summarise(
            start=START, end=START + timedelta(hours=1),
            pm25_warn=thresholds.pm25_warn, pm10_warn=thresholds.pm10_warn,
            pm25_err=thresholds.pm25_err, pm10_err=thresholds.pm10_err,
        )
    assert agg is None
    assert levels == {"ok": 0, "warn": 0, "err": 0}


def test_aggregate_of_an_empty_range_is_none(db):
    with db.session() as s:
        assert ReadingRepository(s).get_aggregate(
            start=START, end=START + timedelta(hours=1)
        ) is None


# ---------- routes ----------

def test_health_needs_no_credential(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_every_data_route_is_guarded(client):
    for path in ("/api/meta", "/api/latest", "/api/series", "/api/summary",
                 "/api/patterns", "/api/episodes"):
        assert client.get(path).status_code == 401, path


def test_meta_publishes_the_thresholds_it_will_be_drawn_with(client, thresholds):
    body = client.get("/api/meta", headers=auth()).json()
    assert body["thresholds"]["pm25"]["warn"] == thresholds.pm25_warn
    assert body["thresholds"]["pm10"]["err"] == thresholds.pm10_err
    assert body["timezone"] == "Europe/Kyiv"
    assert body["viewer"]["telegram"] is True


def test_series_comes_back_columnar_and_aligned(client):
    body = client.get("/api/series", params={**window(), "bucket": "1h"},
                      headers=auth()).json()
    assert body["bucket"] == {"name": "1h", "seconds": 3600}
    assert len(body["t"]) == len(body["pm25"]["avg"]) == len(body["pm10"]["max"]) == 48
    assert body["count"] == 576


def test_series_widens_the_bucket_rather_than_blowing_the_budget(client):
    body = client.get("/api/series",
                      params={"range": "90d", "bucket": "raw"}, headers=auth()).json()
    assert body["bucket"]["name"] != "raw"
    assert len(body["t"]) <= 1500


def test_a_range_with_no_readings_answers_empty_rather_than_failing(client):
    body = client.get("/api/series",
                      params={"from": "2020-01-01T00:00:00Z",
                              "to": "2020-01-02T00:00:00Z"}, headers=auth()).json()
    assert body["t"] == []
    assert body["count"] == 0


def test_summary_reports_the_worst_hour_in_local_time(client):
    body = client.get("/api/summary", params=window(), headers=auth()).json()
    assert body["count"] == 576
    assert body["pm25"]["max"] == 40.0
    assert body["worst_hour"]["pm25"] == 40.0
    assert body["best_hour"]["pm25"] == 10.0
    assert sum(body["level_share"].values()) == pytest.approx(100.0, abs=0.2)


def test_summary_of_an_empty_range_says_so(client):
    body = client.get("/api/summary",
                      params={"from": "2020-01-01T00:00:00Z",
                              "to": "2020-01-02T00:00:00Z"}, headers=auth()).json()
    assert body["empty"] is True
    assert body["count"] == 0


def test_patterns_grids_by_local_hour(client):
    body = client.get("/api/patterns", params=window(), headers=auth()).json()
    assert len(body["pm25"]["grid"]) == 7
    assert all(len(row) == 24 for row in body["pm25"]["grid"])
    by_hour = body["pm25"]["by_hour"]
    assert by_hour[15] == 40.0       # the seeded afternoon peak, in Kyiv time
    assert by_hour[3] == 10.0


# ---------- the timeline and above-limit periods ----------

def test_exceedances_leave_the_database_and_nothing_else(seeded, thresholds):
    with seeded.session() as s:
        rows = ReadingRepository(s).get_exceedances(
            start=START, end=START + timedelta(days=2),
            pm25_warn=thresholds.pm25_warn, pm10_warn=thresholds.pm10_warn,
        )
    assert len(rows) == 2 * 4 * 12                # 14:00-18:00 local, both days
    assert all(pm25 >= thresholds.pm25_warn or pm10 >= thresholds.pm10_warn
               for _, pm25, pm10 in rows)


def test_episodes_are_the_afternoons_newest_first(client):
    body = client.get("/api/episodes", params=window(), headers=auth()).json()
    assert body["count"] == 2
    newest, oldest = body["episodes"]
    assert newest["from"] > oldest["from"]
    start = datetime.fromisoformat(oldest["from"]).astimezone(KYIV)
    end = datetime.fromisoformat(oldest["to"]).astimezone(KYIV)
    assert (start.hour, start.minute, end.hour, end.minute) == (14, 0, 18, 0)
    assert oldest["readings"] == 48
    assert oldest["level"] == "warn" and oldest["level_label"] == "Elevated"
    assert (oldest["pm25_peak"], oldest["pm10_peak"]) == (40.0, 60.0)
    # 60/50 is further over its limit than 40/35.
    assert oldest["pollutant"] == "pm10"


def test_one_clean_reading_does_not_split_an_episode(db, thresholds):
    with db.session() as s:
        repo = ReadingRepository(s)
        for i, pm25 in enumerate([40, 41, 10, 42, 43, 10, 10, 10, 44]):
            repo.add(pm25=pm25, pm10=20, timestamp=START + timedelta(seconds=i * INTERVAL))
    svc = DashboardService(db, KYIV, thresholds, reading_interval_seconds=INTERVAL)
    from ranges import TimeRange
    eps = svc.episodes(TimeRange(START, START + timedelta(hours=1), "custom"))["episodes"]
    # 40 41 . 42 43 is one stretch; three clean readings end it; 44 is another.
    assert [e["readings"] for e in eps] == [1, 4]
    assert eps[1]["pm25_peak"] == 43.0


def test_a_range_with_nothing_over_the_limit_has_no_episodes(client):
    body = client.get("/api/episodes", params={"from": START.isoformat(),
                      "to": (START + timedelta(hours=6)).isoformat()}, headers=auth()).json()
    assert body == {**body, "count": 0, "episodes": []}


def test_a_bad_range_is_a_400_not_a_500(client):
    r = client.get("/api/series", params={"range": "forever"}, headers=auth())
    assert r.status_code == 400


def test_latest_is_the_newest_reading_with_its_level(client):
    body = client.get("/api/latest", headers=auth()).json()
    assert body["level"] in ("ok", "warn", "err")
    assert body["observed_at"].startswith("2026-03-11")
    assert body["stale"] is True     # the fixture data is not from today


def test_latest_on_an_empty_database_is_404(db, thresholds):
    app = create_app(
        service=DashboardService(db, KYIV, thresholds,
                                 reading_interval_seconds=INTERVAL),
        guard=AccessGuard(AuthMode.TELEGRAM, bot_token=TOKEN),
        tz=KYIV,
    )
    assert TestClient(app).get("/api/latest", headers=auth()).status_code == 404


# ---------- theme ----------

def test_a_telegram_viewer_writes_the_same_theme_column_the_bot_reads(client, seeded):
    from common.db import ChatRepository

    assert client.post("/api/theme", json={"theme": "dark"},
                       headers=auth()).json() == {"theme": "dark"}
    with seeded.session() as s:
        assert ChatRepository(s).get_theme("4242") == "dark"

    body = client.get("/api/meta", headers=auth()).json()
    assert body["theme"] == "dark"


def test_an_unknown_theme_is_refused(client):
    assert client.post("/api/theme", json={"theme": "neon"},
                       headers=auth()).status_code == 400


def test_a_browser_visitor_has_nowhere_to_store_a_theme(service):
    app = create_app(
        service=service,
        guard=AccessGuard(AuthMode.TOKEN, bot_token=TOKEN, access_token="s3cret"),
        tz=KYIV,
    )
    r = TestClient(app).post("/api/theme", params={"token": "s3cret"},
                             json={"theme": "dark"})
    assert r.status_code == 403


# ---------- the page itself ----------

STATIC = __import__("pathlib").Path(__file__).resolve().parents[1] / "web-api" / "static"


def test_static_files_must_be_revalidated_not_reused_on_a_guess(service):
    """Without Cache-Control, Telegram's iOS web view kept a weeks-old app.js and
    styles.css under a new index.html: the redesign shipped unstyled."""
    app = create_app(service=service, guard=AccessGuard(AuthMode.PUBLIC, bot_token=TOKEN),
                     tz=KYIV, static_dir=str(STATIC))
    c = TestClient(app)
    for path in ("/", "/app.js?v=2", "/charts.js", "/styles.css"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path


def test_every_local_asset_url_carries_the_same_version():
    """One version everywhere: a module imported under two URLs loads twice."""
    import re
    refs = re.findall(r'(?:href|src)="([\w./]+\.(?:css|js)[^"]*)"', (STATIC / "index.html").read_text("utf-8"))
    for js in STATIC.glob("*.js"):
        refs += re.findall(r'from "\./([^"]+)"', js.read_text("utf-8"))
    versions = {r.partition("?")[2] for r in refs}
    assert refs and len(versions) == 1 and versions != {""}, refs
