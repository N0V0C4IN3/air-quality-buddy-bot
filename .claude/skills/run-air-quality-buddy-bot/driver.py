#!/usr/bin/env python
"""Drive the air-quality stack without Docker, a sensor, a broker or Telegram.

Four services share one Postgres in production. Locally that is more machinery
than any change needs: this driver stands the same code up on a SQLite file and
reaches every user-visible surface the repo has —

  * the real sensor-reader loop (DRY_RUN, so `FakeSampler` stands in for the
    SDS011) writing real rows,
  * the reporter-bot PNGs, rendered straight from `reports.ReadingReports` with
    no aiogram and no bot token,
  * the web-api dashboard, served by its own `main.py` under uvicorn, with every
    JSON route exercised and the page screenshotted in headless Chrome.

Everything lands under `.run-out/` at the repo root. Nothing here touches
Postgres, RabbitMQ, Redis or the network except the one-off uPlot vendor fetch.

Usage (from the repo root):

    python .claude/skills/run-air-quality-buddy-bot/driver.py all
    python .claude/skills/run-air-quality-buddy-bot/driver.py <step> [...]

Steps: vendor, seed, sensor, charts, web, smoke, shot, exec, all,
plus seed-compose for the docker compose Postgres.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# A Russian-locale Windows console is cp1251, and every reading this repo logs
# carries "µg/m³". Without this the driver dies with UnicodeEncodeError while
# echoing a perfectly healthy service's log line.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / ".run-out"
DB_FILE = OUT / "dev.db"
VENDOR = ROOT / "web-api" / "static" / "vendor"

# The Dockerfile pins this; keep the two in step or the local page and the
# shipped page are running different chart libraries.
UPLOT_VERSION = "1.6.31"
UPLOT_FILES = {
    "uPlot.iife.min.js": f"https://cdn.jsdelivr.net/npm/uplot@{UPLOT_VERSION}/dist/uPlot.iife.min.js",
    "uPlot.min.css": f"https://cdn.jsdelivr.net/npm/uplot@{UPLOT_VERSION}/dist/uPlot.min.css",
}

PORT = int(os.getenv("RUN_WEB_PORT", "8099"))
BASE = f"http://127.0.0.1:{PORT}"
TZ_NAME = "Europe/Kyiv"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
]


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def sqlite_url(path: pathlib.Path) -> str:
    """SQLAlchemy wants forward slashes even on Windows."""
    return "sqlite:///" + str(path).replace("\\", "/")


def service_env(**extra) -> dict:
    """The environment a service container gets, minus the container.

    `PYTHONPATH` is the repo root so `common` resolves; each service is run with
    its own directory as cwd, which reproduces the flat imports the images use
    (`/app` is the service directory).
    """
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        # Without this the services' log lines arrive as μ escapes on a
        # Windows console: every reading log carries "µg/m³".
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
        "DATABASE_URL": sqlite_url(DB_FILE),
        "TIMEZONE": TZ_NAME,
        "PM25_WARN": "35", "PM10_WARN": "50",
        "PM25_ERR": "75", "PM10_ERR": "100",
        "LOG_LEVEL": "INFO",
    })
    env.update({k: str(v) for k, v in extra.items()})
    return env


def say(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def step_vendor(force: bool = False) -> None:
    """Fetch uPlot into web-api/static/vendor.

    The Dockerfile curls these at build time, so the checked-out tree has an
    empty `vendor/` with a .gitkeep. Serving `web-api` straight from the repo
    without this gives a page that loads, renders the shell, and has no charts —
    `uPlot is not defined` in the console, nothing visible in the DOM.
    """
    VENDOR.mkdir(parents=True, exist_ok=True)
    for name, url in UPLOT_FILES.items():
        dest = VENDOR / name
        if dest.exists() and dest.stat().st_size > 0 and not force:
            say("vendor", f"{name} already present ({dest.stat().st_size}b)")
            continue
        say("vendor", f"fetching {url}")
        with urllib.request.urlopen(url, timeout=30) as r:
            dest.write_bytes(r.read())
        say("vendor", f"wrote {dest.relative_to(ROOT)} ({dest.stat().st_size}b)")


# One seed script, two destinations: exec'd in-process against SQLite, or piped
# into a container to fill the compose Postgres. It reads DATABASE_URL and
# SEED_DAYS from the environment and imports only `common.db`, which is present
# in every image, so it must stay standalone — do not import from the driver.
_SEED_SCRIPT = '''
import math, os, random
from datetime import datetime, timedelta, timezone
from common.db import ChatRepository, Database, ReadingRepository

days = int(os.environ.get("SEED_DAYS", "10"))
db = Database(url=os.environ["DATABASE_URL"])
now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
start, rng = now - timedelta(days=days), random.Random(7)

# A diurnal curve with two rush-hour humps, plus one two-day episode that
# crosses warn and touches err. Without the episode every chart is a flat line:
# the threshold bands, the level bar and the warn/high labels go untested.
ep0, ep1 = now - timedelta(days=3), now - timedelta(days=1)

rows, t = 0, start
with db.session() as s:
    repo = ReadingRepository(s)
    while t < now:
        h = (t.hour + 3) % 24
        base = (8 + 5 * math.sin((h - 4) / 24 * 2 * math.pi)
                + 6 * math.exp(-((h - 8) ** 2) / 4)
                + 7 * math.exp(-((h - 19) ** 2) / 5)
                + rng.gauss(0, 1.6))
        if ep0 <= t < ep1:
            base += 55 * math.sin(((t - ep0) / (ep1 - ep0)) * math.pi)
        pm25 = max(0.5, round(base, 1))
        pm10 = max(0.5, round(base * 1.7 + rng.gauss(0, 2.5), 1))
        st = ("err" if pm25 >= 75 or pm10 >= 100
              else "warn" if pm25 >= 35 or pm10 >= 50 else "ok")
        repo.add(pm25=pm25, pm10=pm10, status=st, timestamp=t)
        rows += 1
        t += timedelta(minutes=5)
    # One chat row, so the stored-theme path has something real to read.
    ChatRepository(s).upsert("424242", True)
print(f"seeded {rows} readings over {days} days")
'''


def step_seed_compose(days: int = 10) -> None:
    """Migrate and seed the compose Postgres.

    Run inside the reporter-bot image rather than from Windows: psycopg2 is in
    the image and generally not on the host. sensor-reader would do as well,
    but reporter-bot needs no device mapping.
    """
    base = ["docker", "compose", "run", "--rm", "-T", "--no-deps"]
    say("compose", "alembic upgrade head (in the reporter-bot image)")
    subprocess.run(base + ["reporter-bot", "alembic", "upgrade", "head"],
                   cwd=ROOT, check=True)
    say("compose", f"seeding {days} days into Postgres")
    # -e belongs before the service name, or compose reads it as an argument.
    subprocess.run(base + ["-e", f"SEED_DAYS={days}", "reporter-bot", "python", "-"],
                   cwd=ROOT, input=_SEED_SCRIPT.encode(), check=True)


def step_seed(days: int = 10, fresh: bool = True) -> None:
    """Build the schema with Alembic, then fill it with plausible readings.

    Alembic, not `Database.create_all()` — the migrations are the schema in this
    repo, and running them here means the driver also proves they still apply.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    if fresh and DB_FILE.exists():
        DB_FILE.unlink()

    env = service_env()
    say("seed", "alembic upgrade head")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=ROOT, env=env, check=True)

    # The same script the compose path pipes into a container, run here in
    # process against SQLite — one definition of what "seeded data" means.
    _import_paths()
    os.environ["DATABASE_URL"] = sqlite_url(DB_FILE)
    os.environ["SEED_DAYS"] = str(days)
    exec(compile(_SEED_SCRIPT, "<seed>", "exec"), {"__name__": "__main__"})
    say("seed", f"into {DB_FILE.relative_to(ROOT)}")


def step_sensor(seconds: int = 8, interval: int = 2) -> None:
    """Run the actual sensor-reader loop for a few cycles.

    `DRY_RUN=true` swaps `SerialSampler` for `FakeSampler`, so no serial port is
    opened. There is no broker either: `FakeSampler`'s values are always ok, the
    alert gate never publishes, and `sample_once` catches anything that does go
    wrong. So this appends real rows through the real write path.
    """
    env = service_env(
        DRY_RUN="true",
        READ_INTERVAL_SECONDS=interval,
        RABBITMQ_HOST="localhost", RABBITMQ_USER="guest", RABBITMQ_PASS="guest",
        AQ_EXCHANGE="aq.alerts",
    )
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / "sensor-reader.log"
    before = _count_readings()
    say("sensor", f"sensor-reader main.py for ~{seconds}s (interval={interval}s)")

    # A file, not a pipe: terminate() on Windows is TerminateProcess, and
    # anything still sitting in the pipe when the child dies is simply lost —
    # which turns a crash into an empty capture and no clue why.
    start_at = time.time()
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([sys.executable, "main.py"],
                                cwd=ROOT / "sensor-reader", env=env,
                                stdout=fh, stderr=subprocess.STDOUT)
        try:
            # Wait for rows, not for the clock. A cold start — first import of
            # sqlalchemy, matplotlib and pika on a slow disk — can eat the whole
            # window, and a fixed sleep then reports "wrote nothing" for a
            # service that was merely still booting.
            deadline = time.time() + seconds + 30
            while time.time() < deadline:
                if _count_readings() > before and time.time() > start_at + seconds:
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()

    out = log.read_text(encoding="utf-8", errors="replace")
    for line in out.splitlines():
        if "Reading pm2.5" in line or "Starting sensor-reader" in line:
            say("sensor", line.strip())
    after = _count_readings()
    say("sensor", f"readings {before} -> {after} (+{after - before})")
    if after <= before:
        print(out)
        raise SystemExit(f"sensor-reader wrote nothing (full log: {log})")


def _count_readings() -> int:
    import sqlite3
    if not DB_FILE.exists():
        return 0
    con = sqlite3.connect(DB_FILE)
    try:
        return con.execute("select count(*) from readings").fetchone()[0]
    finally:
        con.close()


def step_charts(out_dir: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Render every PNG the bot can send, both themes, straight to disk.

    `reports`/`charts` import no aiogram and no config — they take their
    dependencies as arguments — so this needs neither a Telegram token nor
    reporter-bot's requirements. This is the layer most reporter-bot changes
    actually touch; look at the files it writes.
    """
    out_dir = out_dir or (OUT / "charts")
    out_dir.mkdir(parents=True, exist_ok=True)
    _import_paths()

    from zoneinfo import ZoneInfo

    from common.air_quality import Thresholds
    from common.db import Database
    from reports import ReadingReports, Window

    tz = ZoneInfo(TZ_NAME)
    db = Database(url=sqlite_url(DB_FILE))
    reports = ReadingReports(db, tz, Thresholds(), reading_interval_seconds=300)

    written: list[pathlib.Path] = []

    def dump(name: str, report) -> None:
        if report is None:
            say("charts", f"{name}: no report (empty database?)")
            return
        if report.chart is None:
            say("charts", f"{name}: text only -> {report.text[:70]!r}")
            return
        blob = report.chart.getvalue()
        assert blob.startswith(b"\x89PNG\r\n\x1a\n"), f"{name} is not a PNG"
        dest = out_dir / f"{name}.png"
        dest.write_bytes(blob)
        written.append(dest)
        say("charts", f"{dest.relative_to(ROOT)} ({len(blob)}b)")

    now = datetime.now(timezone.utc)
    for theme in ("light", "dark"):
        for window in Window:
            dump(f"{window.slug}-{theme}", reports.for_window(window, theme=theme))
        dump(f"status-{theme}", reports.status_report(theme=theme))
        # An err-level alert, so the card's red band and level bar are drawn.
        dump(f"alert-{theme}",
             reports.alert_report(88.0, 141.0, now, theme=theme, now=now))

    say("charts", f"{len(written)} PNGs in {out_dir.relative_to(ROOT)}")
    return written


def _import_paths() -> None:
    """Reproduce the flat imports the images use, in conftest.py's order.

    Repo root first so `common` resolves, then sensor-reader so a bare
    `import config` is its one, then reporter-bot; web-api goes on the end
    because it has a `config` module too.
    """
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("DATABASE_URL", sqlite_url(DB_FILE))
    for path in (ROOT / "reporter-bot", ROOT / "sensor-reader", ROOT):
        entry = str(path)
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)
    sys.path.append(str(ROOT / "web-api"))


def step_exec(code: str | None, file: pathlib.Path | None) -> None:
    """Run a snippet against the repo's modules with sys.path already right.

    Setting PYTHONPATH by hand is a trap here: in Git Bash `$PWD` is a POSIX
    path (/c/Users/...) that Python cannot import from, so the obvious
    one-liner fails with ModuleNotFoundError. This does it from inside Python.
    """
    _import_paths()
    source = file.read_text(encoding="utf-8") if file else code
    if not source:
        raise SystemExit("nothing to run: pass -c CODE or --file PATH")
    exec(compile(source, str(file) if file else "<exec>", "exec"),
         {"__name__": "__main__", "ROOT": ROOT, "DB_URL": sqlite_url(DB_FILE)})


class Web:
    """web-api's own `main.py` under uvicorn, as a child process."""

    def __init__(self, auth_mode: str = "public") -> None:
        self.auth_mode = auth_mode
        self.proc: subprocess.Popen | None = None
        self.log = OUT / "web-api.log"

    def __enter__(self) -> "Web":
        OUT.mkdir(parents=True, exist_ok=True)
        env = service_env(
            WEB_AUTH_MODE=self.auth_mode,
            WEB_HOST="127.0.0.1",
            WEB_PORT=PORT,
            READ_INTERVAL_SECONDS=300,
            PRUNE_MAX_AGE_DAYS=90,
        )
        self._fh = open(self.log, "w", encoding="utf-8")
        say("web", f"starting web-api on {BASE} (auth={self.auth_mode})")
        self.proc = subprocess.Popen([sys.executable, "main.py"],
                                     cwd=ROOT / "web-api", env=env,
                                     stdout=self._fh, stderr=subprocess.STDOUT)
        self._await_health()
        return self

    def _await_health(self, timeout: float = 40.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                print(self.log.read_text(encoding="utf-8", errors="replace"))
                raise SystemExit(f"web-api exited with {self.proc.returncode}")
            try:
                with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as r:
                    if r.status == 200:
                        say("web", "healthy")
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.4)
        print(self.log.read_text(encoding="utf-8", errors="replace"))
        raise SystemExit("web-api never answered /api/health")

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._fh.close()
        say("web", f"stopped (log: {self.log.relative_to(ROOT)})")


def _get(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(path: str, payload: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def step_smoke() -> None:
    """Every dashboard route, plus the two static assets that break silently."""
    checks = [
        ("GET  /api/health", *_get("/api/health")),
        ("GET  /api/meta", *_get("/api/meta")),
        ("GET  /api/latest", *_get("/api/latest")),
        ("GET  /api/series?range=24h", *_get("/api/series?range=24h")),
        ("GET  /api/series?range=7d&bucket=1h", *_get("/api/series?range=7d&bucket=1h")),
        # The point budget is a server-side rule: raw over 90 days is far more
        # than WEB_MAX_POINTS, so this must come back widened, not refused.
        ("GET  /api/series?range=90d&bucket=raw", *_get("/api/series?range=90d&bucket=raw")),
        ("GET  /api/summary?range=7d", *_get("/api/summary?range=7d")),
        ("GET  /api/patterns?range=7d", *_get("/api/patterns?range=7d")),
        ("GET  /", *_get("/")),
        ("GET  /vendor/uPlot.iife.min.js", *_get("/vendor/uPlot.iife.min.js")),
        ("GET  /app.js", *_get("/app.js")),
    ]
    failed = []
    for label, status, body in checks:
        ok = status == 200
        detail = f"{len(body)}b"
        if body[:1] in (b"{", b"["):
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    detail = ", ".join(list(data)[:6]) or "{}"
            except ValueError:
                pass
        print(f"  {'ok ' if ok else 'FAIL'} {status} {label:38} {detail}")
        if not ok:
            failed.append(label)

    # A public viewer has no chat row, so writing a theme is a 403 by design.
    status, _ = _post("/api/theme", {"theme": "dark"})
    print(f"  {'ok ' if status == 403 else 'FAIL'} {status} POST /api/theme"
          f"{'':<24} expected 403 for an unidentified viewer")
    if status != 403:
        failed.append("POST /api/theme")

    # The bucket widening above is only meaningful if the response is small.
    status, body = _get("/api/series?range=90d&bucket=raw")
    if status == 200:
        data = json.loads(body)
        points = len(data.get("t") or data.get("time") or [])
        print(f"  {'ok ' if points <= 1500 else 'FAIL'} 90d raw widened to "
              f"{points} points (budget 1500)")
        if points > 1500:
            failed.append("point budget")

    if failed:
        raise SystemExit(f"smoke failures: {', '.join(failed)}")
    say("smoke", "every route answered")


def _chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if os.path.sep in candidate or ":" in candidate:
            if pathlib.Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    raise SystemExit("no Chrome or Edge found; edit CHROME_CANDIDATES")


def step_shot(name: str = "dashboard", url: str | None = None,
              width: int = 1400, height: int = 1800) -> pathlib.Path:
    """Headless screenshot of the running dashboard.

    A throwaway --user-data-dir keeps this out of the user's real Chrome
    profile; without it Chrome attaches to the running instance and exits 0
    having written nothing.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{name}.png"
    if dest.exists():
        dest.unlink()
    profile = tempfile.mkdtemp(prefix="aq-chrome-")
    cmd = [
        _chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        # The page fetches /api/* and then draws; without a virtual time budget
        # the screenshot is the empty shell.
        "--virtual-time-budget=10000",
        f"--screenshot={dest}",
        url or BASE + "/",
    ]
    say("shot", " ".join(cmd[:2]) + " ... -> " + str(dest.relative_to(ROOT)))
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    shutil.rmtree(profile, ignore_errors=True)
    if not dest.exists() or dest.stat().st_size == 0:
        print(proc.stdout, proc.stderr)
        raise SystemExit("chrome wrote no screenshot")
    say("shot", f"{dest.relative_to(ROOT)} ({dest.stat().st_size}b)")
    return dest


def step_all(args) -> None:
    step_vendor()
    step_seed(days=args.days)
    step_sensor()
    step_charts()
    with Web():
        step_smoke()
        step_shot("dashboard")
        # The default 24h view sits after the seeded episode, so every series is
        # green and the threshold bands go untested. 7d includes it.
        step_shot("dashboard-7d", url=BASE + "/?range=7d")
    print()
    say("all", f"done. artefacts under {OUT.relative_to(ROOT)}")


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("vendor", help="fetch uPlot into web-api/static/vendor")

    p = sub.add_parser("seed", help="alembic upgrade + synthetic readings")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--keep", action="store_true", help="append to an existing DB")

    p = sub.add_parser("sensor", help="run the real sensor-reader loop (DRY_RUN)")
    p.add_argument("--seconds", type=int, default=8)
    p.add_argument("--interval", type=int, default=2)

    p = sub.add_parser("charts", help="render every bot PNG to .run-out/charts")
    p.add_argument("--out", type=pathlib.Path, default=None)

    p = sub.add_parser("web", help="serve the dashboard until Ctrl-C")
    p.add_argument("--auth", default="public", choices=["public", "token", "telegram"])

    sub.add_parser("smoke", help="hit every route (needs `web` running)")

    p = sub.add_parser("shot", help="screenshot the dashboard (needs `web` running)")
    p.add_argument("--name", default="dashboard")
    p.add_argument("--url", default=None)

    p = sub.add_parser("seed-compose", help="migrate + seed the docker compose Postgres")
    p.add_argument("--days", type=int, default=10)

    p = sub.add_parser("exec", help="run a snippet with sys.path already set up")
    p.add_argument("-c", dest="code", default=None, help="code to run")
    p.add_argument("--file", type=pathlib.Path, default=None, help="script to run")

    p = sub.add_parser("all", help="the whole thing, start to finish")
    p.add_argument("--days", type=int, default=10)

    args = ap.parse_args()

    if args.cmd == "vendor":
        step_vendor()
    elif args.cmd == "seed":
        step_seed(days=args.days, fresh=not args.keep)
    elif args.cmd == "sensor":
        step_sensor(seconds=args.seconds, interval=args.interval)
    elif args.cmd == "charts":
        step_charts(args.out)
    elif args.cmd == "web":
        with Web(auth_mode=args.auth):
            say("web", "serving; Ctrl-C to stop")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    elif args.cmd == "smoke":
        step_smoke()
    elif args.cmd == "shot":
        step_shot(args.name, args.url)
    elif args.cmd == "seed-compose":
        step_seed_compose(days=args.days)
    elif args.cmd == "exec":
        step_exec(args.code, args.file)
    elif args.cmd == "all":
        step_all(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
