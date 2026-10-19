from datetime import datetime, timedelta, timezone

import pandas as pd

from core.candles import Candle

# "5min"/"15min"/"1day" (Twelve Data's native naming, used as the
# canonical timeframe string everywhere else in this codebase) are
# accepted as aliases for yfinance's own "5m"/"15m"/"1d" -- without them
# every "15min"/"1day" call would silently return [] and fall through to
# Twelve Data (which has a daily credit cap) for data yfinance has for
# free. Mirrors harmonic_bot's core/yfinance_feed.py, extended here with
# "1d"/"1day" since this project's D1 zone-timeframe profile needs daily
# history yfinance can supply back to ~2015 at no cost.
_SUPPORTED_TIMEFRAMES = ("5m", "5min", "15m", "15min", "1h", "4h", "1d", "1day")
_BAR_MINUTES = {"5m": 5, "5min": 5, "15m": 15, "15min": 15, "1h": 60, "4h": 240, "1d": 1440, "1day": 1440}
# Native yfinance interval to request per timeframe -- 5m/15m/60m/1d are
# all native Yahoo intervals; 4h has no native interval, so it's
# resampled locally from 60m (see _resample_to_4h).
_FETCH_INTERVAL = {
    "5m": "5m", "5min": "5m", "15m": "15m", "15min": "15m",
    "1h": "60m", "4h": "60m", "1d": "1d", "1day": "1d",
}

# Yahoo Finance no longer carries XAUUSD=X spot -- COMEX gold futures (GC=F)
# track spot within $2-5 and are structurally equivalent for pattern
# detection. Mirrors smc_bot's/harmonic_bot's handling of this same problem.
_YF_SYMBOL_OVERRIDE = {
    "XAU/USD": "GC=F",
}


def _to_yf_symbol(pair: str) -> str:
    """'EUR/USD' -> 'EURUSD=X', 'XAU/USD' -> 'GC=F'"""
    return _YF_SYMBOL_OVERRIDE.get(pair, pair.replace("/", "") + "=X")


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
    df = df[["open", "high", "low", "close"]].astype(float)
    index = pd.DatetimeIndex(df.index)
    df.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    df.index.name = "datetime"
    return df.sort_index()


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    resampler = df.resample("4h", origin="start_day")
    resampled = resampler.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    counts = resampler.size()
    return resampled[(counts == 4) & resampled[["open", "high", "low", "close"]].notna().all(axis=1)]


def _trim_unclosed_candle(df: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    bar_close_time = df.index[-1] + timedelta(minutes=_BAR_MINUTES[timeframe])
    if bar_close_time > now:
        return df.iloc[:-1]
    return df


def _df_to_candles(df: pd.DataFrame, timeframe: str) -> list[Candle]:
    # Daily bars carry only a date in yfinance's index -- format without a
    # time component would still parse fine downstream (midnight), but an
    # explicit "00:00:00" keeps every Candle.timestamp in this codebase's
    # one naive "YYYY-MM-DD HH:MM:SS" convention without a special case.
    return [
        Candle(
            timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
        )
        for ts, row in df.iterrows()
    ]


def fetch_candles(
    pair: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    history_fn=None,
    now: datetime | None = None,
) -> list[Candle]:
    """Fetch OHLC candles from Yahoo Finance via yfinance. Returns an empty
    list on any failure rather than raising -- core.market_data falls back
    to Twelve Data on an empty result."""
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        return []
    if now is None:
        now = datetime.now(timezone.utc)
    if history_fn is None:
        import yfinance as yf

        def history_fn(ticker, start, end, interval):
            return yf.Ticker(ticker).history(start=start, end=end, interval=interval)

    ticker = _to_yf_symbol(pair)
    try:
        raw = history_fn(ticker, start, end, _FETCH_INTERVAL[timeframe])
        df = _normalize(raw)
        if timeframe == "4h":
            df = _resample_to_4h(df)
        if timeframe not in ("1d", "1day"):  # a daily bar isn't "unclosed" until the next day
            df = _trim_unclosed_candle(df, timeframe, now)
        return _df_to_candles(df, timeframe)
    except Exception:
        return []
