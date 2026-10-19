"""Range-aware on-disk CSV cache, keyed by (pair, timeframe). Unlike
harmonic_bot's whole-series cache (one file per (pair, timeframe), no
notion of which date range it holds), this tracks coverage and only
fetches the missing range on a request that extends past what's cached.
"""

import csv
from datetime import datetime
from pathlib import Path

from core.candles import Candle

CACHE_DIR = Path("data_cache")
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _cache_path(pair: str, timeframe: str, cache_dir: Path) -> Path:
    safe_pair = pair.replace("/", "_")
    return cache_dir / f"{safe_pair}_{timeframe}.csv"


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FORMAT)


def save_candles(pair: str, timeframe: str, candles: list[Candle], *, cache_dir: Path = CACHE_DIR) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(pair, timeframe, cache_dir)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close"])
        for c in sorted(candles, key=lambda c: c.timestamp):
            writer.writerow([c.timestamp, c.open, c.high, c.low, c.close])


def load_candles(pair: str, timeframe: str, *, cache_dir: Path = CACHE_DIR) -> list[Candle] | None:
    path = _cache_path(pair, timeframe, cache_dir)
    if not path.exists():
        return None
    candles = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            candles.append(Candle(
                timestamp=row["timestamp"],
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
            ))
    return candles


def merge_candles(existing: list[Candle], new: list[Candle]) -> list[Candle]:
    """De-duplicate by timestamp (new wins on conflict -- a re-fetched bar
    may have settled since it was last cached), sorted ascending."""
    by_ts = {c.timestamp: c for c in existing}
    for c in new:
        by_ts[c.timestamp] = c
    return [by_ts[ts] for ts in sorted(by_ts)]


def fetch_candles_cached(
    pair: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    api_key: str,
    *,
    fetch_fn=None,
    cache_dir: Path = CACHE_DIR,
) -> list[Candle]:
    """Loads whatever's cached, fetches only the range(s) not yet
    covered, merges, persists, and returns candles within [start, end].
    A cold cache fetches the whole requested range in one call.
    """
    # Every Candle.timestamp in this codebase is naive UTC (see
    # core.candles.Candle) -- strip any tzinfo on the requested range so
    # the comparisons below never raise on naive-vs-aware datetimes.
    start = start.replace(tzinfo=None) if start.tzinfo is not None else start
    end = end.replace(tzinfo=None) if end.tzinfo is not None else end
    cached = load_candles(pair, timeframe, cache_dir=cache_dir) or []

    if fetch_fn is None:
        import core.market_data as market_data
        fetch_fn = market_data.fetch_candles

    missing_ranges = _missing_ranges(cached, start, end)
    if missing_ranges:
        fetched: list[Candle] = []
        for m_start, m_end in missing_ranges:
            fetched.extend(fetch_fn(pair, timeframe, m_start, m_end, api_key))
        if fetched:
            cached = merge_candles(cached, fetched)
            save_candles(pair, timeframe, cached, cache_dir=cache_dir)

    return [c for c in cached if start <= _parse_ts(c.timestamp) <= end]


def _missing_ranges(cached: list[Candle], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Simplified coverage check: only handles the common cases of a cold
    cache, extending before the earliest cached bar, and extending after
    the latest cached bar. Does not detect an interior gap -- acceptable
    for this project's append-mostly fetch pattern (see
    scripts/fetch_history.py), not a general-purpose range index."""
    if not cached:
        return [(start, end)]

    earliest = _parse_ts(cached[0].timestamp)
    latest = _parse_ts(cached[-1].timestamp)

    missing = []
    if start < earliest:
        missing.append((start, earliest))
    if end > latest:
        missing.append((latest, end))
    return missing
