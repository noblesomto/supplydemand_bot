"""Read-only consumer of the scanner's `signals` table (SQLite mode=ro)."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_COLS = "id,system,pair,direction,entry,sl,tp,rr,taken,status,created_at,entry_timestamp"


@dataclass(frozen=True)
class SignalRow:
    id: int
    system: str
    pair: str
    direction: str  # long | short
    entry: float
    sl: float
    tp: float | None
    rr: float | None
    taken: bool
    status: str
    created_at: datetime  # aware UTC: when the scanner first recorded it
    entry_timestamp: str


def open_readonly(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _ts(v) -> datetime:
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(r) -> SignalRow:
    return SignalRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], bool(r[8]), r[9], _ts(r[10]), r[11])


def recent(conn: sqlite3.Connection, since: datetime) -> list[SignalRow]:
    """Signals created since `since`, oldest first. created_at is stored naive UTC or with +00:00."""
    rows = conn.execute(f"SELECT {_COLS} FROM signals WHERE substr(replace(created_at,'T',' '),1,19) >= ? "
                        "ORDER BY id", (since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
    return [_row(r) for r in rows]


def by_id(conn: sqlite3.Connection, signal_id: int) -> SignalRow | None:
    r = conn.execute(f"SELECT {_COLS} FROM signals WHERE id=?", (signal_id,)).fetchone()
    return _row(r) if r else None


def new_signals(conn, state, lookback_min: float, now: datetime) -> list[SignalRow]:
    return [s for s in recent(conn, now - timedelta(minutes=lookback_min)) if not state.is_processed(s.id)]
