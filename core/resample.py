"""Aggregate finer candles into coarser timeframes, locally, with no
extra API fetch. Only buckets with a full complement of source bars are
kept, EXCEPT the D1 bucket, which deliberately merges a short Sunday
stub into the following Monday bucket (see _resample_to_d1)."""

from datetime import datetime, timedelta

import pandas as pd

from core.candles import Candle

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_MINUTES = {"m1": 1, "m15": 15, "h1": 60, "h4": 240}
_SOURCE_FOR_TARGET = {"m15": "m1", "h1": "m15", "h4": "h1", "d1": "h4"}


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FORMAT)


def _format(ts: datetime) -> str:
    return ts.strftime(_TS_FORMAT)


def _to_df(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame(
        {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
        for c in candles
    )
    df.index = pd.DatetimeIndex(_parse(c.timestamp) for c in candles)
    return df


def _df_to_candles(df: pd.DataFrame) -> list[Candle]:
    return [
        Candle(
            timestamp=_format(ts.to_pydatetime()),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
        )
        for ts, row in df.iterrows()
    ]


def resample_candles(candles: list[Candle], target_timeframe: str) -> list[Candle]:
    """target_timeframe in {"h1", "h4", "d1"}."""
    if target_timeframe == "d1":
        return resample_to_d1(candles)
    if target_timeframe not in _MINUTES:
        raise ValueError(f"unsupported target_timeframe: {target_timeframe!r}")
    if not candles:
        return []

    target_minutes = _MINUTES[target_timeframe]
    source_tf = _SOURCE_FOR_TARGET[target_timeframe]
    source_minutes = _MINUTES[source_tf]
    bars_per_bucket = target_minutes // source_minutes

    df = _to_df(candles)
    resampler = df.resample(f"{target_minutes}min", origin="start_day")
    agg = resampler.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    counts = resampler.size()
    full = agg[counts == bars_per_bucket]
    return _df_to_candles(full)


def resample_to_d1(candles: list[Candle], *, day_boundary_hour: int = 22) -> list[Candle]:
    """Daily bars on a day boundary at `day_boundary_hour` UTC (default
    22:00, the New York close), built from H4 candles. A trading day
    starting at 22:00 on calendar day D is conventionally labeled D+1
    (e.g. the Sunday-22:00 session open starts "Monday"'s day) -- shifting
    every timestamp forward by (24 - day_boundary_hour) before taking its
    date implements exactly that labeling, and as a side effect any
    short/stub session right at the week's open (however many bars it
    spans) falls naturally into the same bucket as the rest of that
    trading day without needing a separate merge step."""
    if not candles:
        return []

    shift = timedelta(hours=24 - day_boundary_hour)

    def bucket_date(ts: datetime):
        return (ts + shift).date()

    buckets: dict = {}
    order: list = []
    for c in candles:
        key = bucket_date(_parse(c.timestamp))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(c)

    return [
        Candle(
            timestamp=buckets[key][0].timestamp,
            open=buckets[key][0].open,
            high=max(c.high for c in buckets[key]),
            low=min(c.low for c in buckets[key]),
            close=buckets[key][-1].close,
        )
        for key in order
    ]
