"""The HTTP surface.

Thin by design: every route parses a query string into a `TimeRange`, asks
`DashboardService` for a dict, and returns it. The two things that do live here
are the auth dependency and the mapping of domain errors onto status codes.

`create_app` takes its dependencies rather than importing config, so the tests
can build an app over a SQLite `Database` without an environment.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import AccessGuard, AuthError, Viewer
from ranges import RangeError, TimeRange, parse_range
from service import DashboardService


class ThemeChoice(BaseModel):
    theme: str


def create_app(
    *,
    service: DashboardService,
    guard: AccessGuard,
    tz,
    static_dir: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="Air Quality Dashboard", docs_url=None, redoc_url=None)

    def viewer(
        x_telegram_init_data: Optional[str] = Header(default=None),
        init_data: Optional[str] = Query(default=None),
        token: Optional[str] = Query(default=None),
        x_access_token: Optional[str] = Header(default=None),
    ) -> Viewer:
        try:
            return guard.check(
                init_data=x_telegram_init_data or init_data,
                token=token or x_access_token,
            )
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    def window(
        range: Optional[str] = Query(default=None, alias="range"),
        start: Optional[str] = Query(default=None, alias="from"),
        end: Optional[str] = Query(default=None, alias="to"),
    ) -> TimeRange:
        try:
            return parse_range(
                preset=range, start=start, end=end,
                now=datetime.now(timezone.utc), tz=tz,
            )
        except RangeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Liveness only - deliberately outside the guard so a probe never needs a
    # token, and deliberately without any reading in it.
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/meta")
    def meta(who: Viewer = Depends(viewer)) -> dict:
        return {
            "viewer": {
                "telegram": who.is_telegram,
                "name": who.first_name or who.username,
            },
            **service.meta(chat_id=who.chat_id),
        }

    @app.get("/api/latest")
    def latest(who: Viewer = Depends(viewer)):
        data = service.latest()
        if data is None:
            return JSONResponse({"detail": "no readings yet"}, status_code=404)
        return data

    @app.get("/api/series")
    def series(
        rng: TimeRange = Depends(window),
        bucket: Optional[str] = Query(default=None),
        who: Viewer = Depends(viewer),
    ) -> dict:
        try:
            return service.series(rng, bucket=bucket)
        except RangeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/summary")
    def summary(rng: TimeRange = Depends(window), who: Viewer = Depends(viewer)) -> dict:
        return service.summary(rng)

    @app.get("/api/patterns")
    def patterns(rng: TimeRange = Depends(window), who: Viewer = Depends(viewer)) -> dict:
        return service.patterns(rng)

    @app.post("/api/theme")
    def theme(choice: ThemeChoice, who: Viewer = Depends(viewer)) -> dict:
        # Only a verified Telegram viewer has a chat row to write to; everyone
        # else keeps their choice in the browser.
        if not who.is_telegram:
            raise HTTPException(
                status_code=403, detail="theme is stored per Telegram chat"
            )
        try:
            return service.set_theme(who.chat_id, choice.theme)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if static_dir:
        # Mounted last: a bare "/" mount swallows every path that did not match
        # a route above it.
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
