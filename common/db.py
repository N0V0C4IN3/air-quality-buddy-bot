# db.py
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Optional
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, String, Float, DateTime, BigInteger, Boolean, Integer,
    case, cast, func,
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column


# ---------- SQLAlchemy Base ----------
class Base(DeclarativeBase):
    pass


# ---------- ORM Models ----------
class Reading(Base):
    __tablename__ = "readings"

    # SQLite only autoincrements an INTEGER primary key, so the tests get that
    # variant; Postgres still gets BIGSERIAL.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    pm25: Mapped[float] = mapped_column(Float, nullable=False)          # μg/m³
    pm10: Mapped[float] = mapped_column(Float, nullable=False)          # μg/m³

    raw_pm25: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_pm10: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")  
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    is_subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Telegram never tells a bot which theme the viewer uses, so charts are
    # rendered to a stored per-chat preference: "light" or "dark".
    chart_theme: Mapped[str] = mapped_column(
        String(16), nullable=False, default="light", server_default="light"
    )

# ---------- Database / Unit of Work ----------
class Database:
    def __init__(self, url: Optional[str] = None, *, echo: bool = False) -> None:
        self.url = url
        self.engine = create_engine(
            self.url,
            echo=echo,
            pool_pre_ping=True,
        )
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    def create_all(self) -> None:
        """Build the schema straight from the models.

        For tests. Production schema belongs to Alembic - `create_all` can
        only add a missing table, never alter an existing one, so using it
        to bootstrap a service turns every later column into a silent no-op.
        """
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

# ---------- Aggregate value objects ----------
@dataclass(frozen=True)
class Bucket:
    """One time bucket of readings, already reduced in SQL.

    The dashboard asks for a range, not for rows: 90 days of five-minute
    samples is ~26k readings, and nothing downstream wants them individually.
    """
    start: datetime
    count: int
    pm25_avg: float
    pm25_min: float
    pm25_max: float
    pm10_avg: float
    pm10_min: float
    pm10_max: float


@dataclass(frozen=True)
class Aggregate:
    """One range reduced to a single row."""
    count: int
    first: datetime
    last: datetime
    pm25_avg: float
    pm25_min: float
    pm25_max: float
    pm10_avg: float
    pm10_min: float
    pm10_max: float


# ---------- Repository ----------
class ReadingRepository:
    def __init__(self, session) -> None:
        self.session = session

    def add(
        self,
        *,
        pm25: float,
        pm10: float,
        status: str = "ok",
        raw_pm25: Optional[float] = None,
        raw_pm10: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> Reading:
        r = Reading(
            pm25=pm25,
            pm10=pm10,
            status=status,
            raw_pm25=raw_pm25,
            raw_pm10=raw_pm10,
            timestamp=timestamp,
        )
        self.session.add(r)
        return r

    def get_latest(self) -> Optional[Reading]:
        return (
            self.session.query(Reading)
            .order_by(Reading.timestamp.desc())
            .limit(1)
            .one_or_none()
        )

    def get_range(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: Optional[int] = None,
    ) -> Iterable[Reading]:
        q = (
            self.session.query(Reading)
            .filter(
                Reading.timestamp >= start,
                Reading.timestamp < end,
            )
            .order_by(Reading.timestamp.asc())
        )
        if limit:
            q = q.limit(limit)
        return q.all()

    # ---------- aggregation ----------
    # Bucketing happens in SQL because the alternative is shipping every row to
    # Python. The epoch expression is the one dialect-specific part: Postgres
    # runs in production, SQLite runs the tests.

    def _epoch_seconds(self):
        if self.session.get_bind().dialect.name == "sqlite":
            return cast(func.strftime("%s", Reading.timestamp), Integer)
        return func.extract("epoch", Reading.timestamp)

    def get_buckets(
        self,
        *,
        start: datetime,
        end: datetime,
        bucket_seconds: int,
    ) -> list[Bucket]:
        """Readings in [start, end) reduced to fixed-width buckets, oldest first.

        Buckets are aligned to the epoch, not to `start`, so the same bucket
        boundaries come back whatever window the caller asked for — panning a
        chart does not reshuffle the points under it.
        """
        if bucket_seconds < 1:
            raise ValueError("bucket_seconds must be >= 1")

        key = func.floor(self._epoch_seconds() / bucket_seconds)
        rows = (
            self.session.query(
                key.label("bucket"),
                func.count(Reading.id),
                func.avg(Reading.pm25),
                func.min(Reading.pm25),
                func.max(Reading.pm25),
                func.avg(Reading.pm10),
                func.min(Reading.pm10),
                func.max(Reading.pm10),
            )
            .filter(Reading.timestamp >= start, Reading.timestamp < end)
            .group_by(key)
            .order_by(key)
            .all()
        )
        return [
            Bucket(
                start=datetime.fromtimestamp(int(k) * bucket_seconds, timezone.utc),
                count=int(n),
                pm25_avg=float(a25), pm25_min=float(l25), pm25_max=float(h25),
                pm10_avg=float(a10), pm10_min=float(l10), pm10_max=float(h10),
            )
            for k, n, a25, l25, h25, a10, l10, h10 in rows
        ]

    def get_aggregate(self, *, start: datetime, end: datetime) -> Optional[Aggregate]:
        """The whole range as one row, or None when it holds no readings."""
        row = (
            self.session.query(
                func.count(Reading.id),
                func.min(Reading.timestamp),
                func.max(Reading.timestamp),
                func.avg(Reading.pm25),
                func.min(Reading.pm25),
                func.max(Reading.pm25),
                func.avg(Reading.pm10),
                func.min(Reading.pm10),
                func.max(Reading.pm10),
            )
            .filter(Reading.timestamp >= start, Reading.timestamp < end)
            .one()
        )
        n, first, last, a25, l25, h25, a10, l10, h10 = row
        if not n:
            return None
        return Aggregate(
            count=int(n),
            first=_utc(first),
            last=_utc(last),
            pm25_avg=float(a25), pm25_min=float(l25), pm25_max=float(h25),
            pm10_avg=float(a10), pm10_min=float(l10), pm10_max=float(h10),
        )

    def count_by_level(
        self,
        *,
        start: datetime,
        end: datetime,
        pm25_warn: float,
        pm10_warn: float,
        pm25_err: float,
        pm10_err: float,
    ) -> dict[str, int]:
        """How many readings in the range sat at each level.

        The thresholds are passed in rather than read here: `common.air_quality`
        owns the rules, and this is the same comparison expressed in SQL so the
        rows never have to leave the database.
        """
        is_err = (Reading.pm25 >= pm25_err) | (Reading.pm10 >= pm10_err)
        is_warn = (Reading.pm25 >= pm25_warn) | (Reading.pm10 >= pm10_warn)

        err = func.sum(case((is_err, 1), else_=0))
        warn = func.sum(case((is_err, 0), (is_warn, 1), else_=0))
        ok = func.sum(case((is_warn, 0), else_=1))

        row = (
            self.session.query(ok, warn, err)
            .filter(Reading.timestamp >= start, Reading.timestamp < end)
            .one()
        )
        return {"ok": int(row[0] or 0), "warn": int(row[1] or 0), "err": int(row[2] or 0)}

    def prune_older_than(self, before: datetime) -> int:
        res = self.session.query(Reading).filter(Reading.timestamp < before).delete()
        return int(res)

class ChatRepository:
    def __init__(self, session):
        self.session = session

    def get_subscribed_ids(self) -> list[str]:
        return [
            chat.chat_id
            for chat in self.session.query(Chat).filter_by(is_subscribed=True).all()
        ]

    def upsert(self, chat_id: str, is_subscribed: bool) -> Chat:
        """Does not commit; the owning `Database.session()` block does."""
        chat = self._get_or_create(chat_id)
        chat.is_subscribed = is_subscribed
        return chat

    def get_theme(self, chat_id: str) -> str:
        chat = self.session.query(Chat).filter_by(chat_id=str(chat_id)).first()
        return chat.chart_theme if chat else "light"

    def set_theme(self, chat_id: str, theme: str) -> Chat:
        """Does not commit; the owning `Database.session()` block does."""
        chat = self._get_or_create(chat_id)
        chat.chart_theme = theme
        return chat

    def _get_or_create(self, chat_id: str) -> Chat:
        chat = self.session.query(Chat).filter_by(chat_id=str(chat_id)).first()
        if chat is None:
            chat = Chat(chat_id=str(chat_id), is_subscribed=False)
            self.session.add(chat)
        return chat

def _utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
