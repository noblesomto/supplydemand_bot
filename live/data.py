"""Live candles: incremental cTrader M1 fetch appended to the backtest's own
parquet cache, then resampled with the backtest's resampler. Only complete
buckets survive resampling, so the still-forming bar is never used."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from core.candles import Candle
from core.resample import resample_candles

log = logging.getLogger(__name__)
M1_CACHE = Path(__file__).resolve().parent.parent / "data_cache" / "ctrader_m1"
_TS = "%Y-%m-%d %H:%M:%S"


def symbol(pair: str) -> str:
    return pair.replace("/", "")


def load_m1(pair: str, since: datetime, cache: Path = M1_CACHE) -> pd.DataFrame:
    files = [f for f in sorted((cache / symbol(pair)).glob("*.parquet")) if f.stem >= since.strftime("%Y-%m")]
    if not files:
        return pd.DataFrame()
    df = pd.concat(pd.read_parquet(f) for f in files).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[df.index >= pd.Timestamp(since)]


def append_m1(pair: str, new: pd.DataFrame, cache: Path = M1_CACHE) -> None:
    """Merge `new` into the per-month parquet files (new wins on overlap)."""
    if new.empty:
        return
    d = cache / symbol(pair)
    d.mkdir(parents=True, exist_ok=True)
    for month, part in new.groupby(new.index.strftime("%Y-%m")):
        path = d / f"{month}.parquet"
        if path.exists():
            part = pd.concat([pd.read_parquet(path), part])
            part = part[~part.index.duplicated(keep="last")]
        part.sort_index().to_parquet(path)


def fetch_new_m1(client, pair: str, last: pd.Timestamp, now: datetime) -> pd.DataFrame:
    """M1 bars after `last` up to `now`, requested in 1-day chunks."""
    start = (last + pd.Timedelta(minutes=1)).to_pydatetime()
    parts = []
    while start < now:
        end = min(start + timedelta(days=1), now)
        parts.append(client.trendbars_m1(symbol(pair), start, end))
        start = end
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts) if parts else pd.DataFrame()


def m1_to_m15(df: pd.DataFrame) -> list[Candle]:
    """Same bucketing as core.resample (origin=start_day, complete buckets only)."""
    if df.empty:
        return []
    r = df.resample("15min", origin="start_day")
    agg = r.agg({"bid_open": "first", "bid_high": "max", "bid_low": "min", "bid_close": "last"})
    agg = agg[r.size() == 15]
    idx = agg.index.tz_convert("UTC").strftime(_TS)
    return [Candle(ts, o, h, l, c) for ts, o, h, l, c in
            zip(idx, agg["bid_open"], agg["bid_high"], agg["bid_low"], agg["bid_close"])]


def build_timeframes(m1: pd.DataFrame) -> dict[str, list[Candle]]:
    m15 = m1_to_m15(m1)
    h1 = resample_candles(m15, "h1")
    return {"m15": m15, "h1": h1, "h4": resample_candles(h1, "h4")}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
