"""Executor's own state DB (executor/executor.db). Never writes the scanner DB."""
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "executor.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_log (
    signal_id INTEGER PRIMARY KEY, system TEXT NOT NULL, pair TEXT NOT NULL,
    outcome TEXT NOT NULL,          -- skipped | accepted | deferred | placed | rejected
    reason TEXT NOT NULL DEFAULT '', ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_msg_ids (client_msg_id TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS trades (
    signal_id INTEGER PRIMARY KEY, system TEXT NOT NULL, pair TEXT NOT NULL, direction TEXT NOT NULL,
    position_id INTEGER NOT NULL, lots REAL NOT NULL, signal_entry REAL NOT NULL, fill_price REAL NOT NULL,
    sl REAL NOT NULL, tp REAL NOT NULL, risk_pct REAL NOT NULL, risk_amount REAL NOT NULL,
    stage TEXT NOT NULL DEFAULT 'open',   -- open | closed
    opened_ts REAL NOT NULL, closed_ts REAL, realized_money REAL, realized_r REAL,
    commission REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""
_COLS = ("signal_id,system,pair,direction,position_id,lots,signal_entry,fill_price,sl,tp,risk_pct,risk_amount,"
         "stage,opened_ts,closed_ts,realized_money,realized_r,commission")


class State:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def is_processed(self, signal_id: int) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM signal_log WHERE signal_id=? AND outcome != 'deferred'",
                                      (signal_id,)).fetchone() is not None

    def record(self, signal_id: int, system: str, pair: str, outcome: str, reason: str = "") -> None:
        """'deferred' and 'accepted' may be overwritten; other outcomes are final."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO signal_log VALUES(?,?,?,?,?,?) ON CONFLICT(signal_id) DO UPDATE SET "
                "outcome=excluded.outcome, reason=excluded.reason, ts=excluded.ts "
                "WHERE signal_log.outcome IN ('accepted','deferred')",
                (signal_id, system, pair, outcome, reason, time.time()))
            self._conn.commit()

    def outcome(self, signal_id: int) -> tuple[str, str] | None:
        with self._lock:
            r = self._conn.execute("SELECT outcome, reason FROM signal_log WHERE signal_id=?", (signal_id,)).fetchone()
            return tuple(r) if r else None

    def skip_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._conn.execute(
                "SELECT reason, COUNT(*) FROM signal_log WHERE outcome='skipped' GROUP BY reason").fetchall())

    def claim_msg_id(self, client_msg_id: str) -> bool:
        """Persist an order id BEFORE sending. False = already sent."""
        with self._lock:
            try:
                self._conn.execute("INSERT INTO sent_msg_ids VALUES(?,?)", (client_msg_id, time.time()))
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get(self, k: str, default: str | None = None) -> str | None:
        with self._lock:
            r = self._conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return r[0] if r else default

    def set(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._conn.commit()

    def add_trade(self, **t) -> None:
        t.setdefault("stage", "open")
        t.setdefault("opened_ts", time.time())
        t.setdefault("commission", 0.0)
        cols = ["signal_id", "system", "pair", "direction", "position_id", "lots", "signal_entry", "fill_price",
                "sl", "tp", "risk_pct", "risk_amount", "stage", "opened_ts", "commission"]
        with self._lock:
            self._conn.execute(f"INSERT OR REPLACE INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                               [t[c] for c in cols])
            self._conn.commit()

    def _trades(self, where: str = "", args=()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(f"SELECT {_COLS} FROM trades {where}", args)
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def open_trades(self) -> list[dict]:
        return self._trades("WHERE stage='open' ORDER BY signal_id")

    def all_trades(self) -> list[dict]:
        return self._trades("ORDER BY opened_ts")

    def close_trade(self, signal_id: int, realized_money: float, realized_r: float) -> None:
        with self._lock:
            self._conn.execute("UPDATE trades SET stage='closed', closed_ts=?, realized_money=?, realized_r=? "
                               "WHERE signal_id=?", (time.time(), realized_money, realized_r, signal_id))
            self._conn.commit()

    def closed_since(self, ts: float) -> list[dict]:
        return self._trades("WHERE stage='closed' AND closed_ts >= ? ORDER BY closed_ts", (ts,))
