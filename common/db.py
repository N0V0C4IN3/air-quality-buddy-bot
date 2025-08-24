# db.py
import os
from contextlib import contextmanager
from typing import Iterable, Optional
from datetime import datetime
import config

from sqlalchemy import (
    create_engine, Column, String, Float, DateTime, BigInteger,
    func, Index
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column


# ---------- SQLAlchemy Base ----------
class Base(DeclarativeBase):
    pass


# ---------- ORM Models ----------
class Reading(Base):
    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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

# ---------- Database / Unit of Work ----------
class Database:
    def __init__(self, url: Optional[str] = None, *, echo: bool = False) -> None:
        self.url = url or config.DATABASE_URL
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

    def prune_older_than(self, before: datetime) -> int:
        res = self.session.query(Reading).filter(Reading.timestamp < before).delete()
        return int(res)
