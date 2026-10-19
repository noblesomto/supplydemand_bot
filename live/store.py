"""SQLite storage for live paper signals (spec section 6)."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("system", "pair", "entry_timestamp", "direction"),)

    id = Column(Integer, primary_key=True)
    system = Column(String(32), nullable=False, index=True)
    pair = Column(String(16), nullable=False)
    direction = Column(String(8), nullable=False)
    zone_type = Column(String(16))
    grade = Column(String(4))
    entry_timestamp = Column(String(19), nullable=False)
    entry = Column(Float, nullable=False)
    sl = Column(Float, nullable=False)
    tp = Column(Float)
    rr = Column(Float)
    taken = Column(Boolean, nullable=False)
    skip_reason = Column(String(32))
    status = Column(String(12), nullable=False, default="OPEN")
    exit_kind = Column(String(16))
    exit_timestamp = Column(String(19))
    realized_r = Column(Float)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    alerted_at = Column(DateTime(timezone=True))
    closed_alerted_at = Column(DateTime(timezone=True))


class Meta(Base):
    __tablename__ = "meta"
    key = Column(String(32), primary_key=True)
    value = Column(String(64))


def make_session_factory(db_url: str):
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def get_meta(s, k: str) -> str | None:
    row = s.get(Meta, k)
    return row.value if row else None


def set_meta(s, k: str, v: str) -> None:
    row = s.get(Meta, k)
    if row:
        row.value = v
    else:
        s.add(Meta(key=k, value=v))


def signals_for(s, system: str) -> list[Signal]:
    return list(s.scalars(select(Signal).where(Signal.system == system)))
