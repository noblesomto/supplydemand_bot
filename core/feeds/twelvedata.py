from datetime import datetime, timedelta, timezone

import requests

from core.candles import Candle

TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"

# Twelve Data silently clamps very wide date-range requests down to a recent
# slice rather than erroring, instead of returning the full requested range
# (observed in production -- see /home/www/scripts/trend_bot/app/data/
# fetcher.py). Pagination therefore always walks fixed calendar chunks
# rather than trusting one wide query or a response-length signal.
_CHUNK_DAYS = 14
_PAGE_SIZE_SANITY_BOUND = 5000
# Proactive pacing between chunk requests, kept below the free-tier
# requests-per-minute quota -- relying on 429 backoff alone burns the whole
# retry budget on quota walls a steady cadence never hits.
_INTER_REQUEST_DELAY = 7.5
# Twelve Data's native interval naming -> bar width in minutes, for
# dropping a still-forming last candle (see _trim_unclosed_candle).
# Mirrors core/yfinance_feed.py's _BAR_MINUTES/_trim_unclosed_candle --
# this module had no equivalent, so a live fallback fetch (core.market_
# data.fetch_candles calls this when yfinance returns no data) could
# return an in-progress candle as if it were closed, unlike the
# yfinance-primary path. An unrecognized timeframe (e.g. a custom --ltf/
# --htf passed to backtest_cli.py) is a no-op here rather than an error,
# since backtest end dates are always in the past anyway and this only
# ever matters for a fetch ending at or near "now".
_BAR_MINUTES = {
    "1min": 1, "5min": 5, "15min": 15, "30min": 30, "45min": 45,
    "1h": 60, "2h": 120, "4h": 240, "1day": 1440,
}


def _trim_unclosed_candle(candles: list[Candle], timeframe: str, now: datetime) -> list[Candle]:
    if not candles or timeframe not in _BAR_MINUTES:
        return candles
    last_ts = datetime.strptime(candles[-1].timestamp, "%Y-%m-%d %H:%M:%S")
    bar_close_time = last_ts + timedelta(minutes=_BAR_MINUTES[timeframe])
    return candles[:-1] if bar_close_time > now else candles


def fetch_candles(
    pair: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    api_key: str,
    *,
    http_get=None,
    sleep_fn=None,
    max_retries: int = 6,
    inter_request_delay: float = _INTER_REQUEST_DELAY,
    chunk_days: int = _CHUNK_DAYS,
    now: datetime | None = None,
) -> list[Candle]:
    """Fetch [start, end] historical OHLC from Twelve Data's time_series
    endpoint, paginating in fixed calendar chunks. `http_get`/`sleep_fn` are
    injectable so tests never hit the network or sleep for real. `now` is
    injectable for the same reason `_trim_unclosed_candle` needs it;
    defaults to the real current time.
    """
    if http_get is None:
        http_get = requests.get
    if sleep_fn is None:
        import time
        sleep_fn = time.sleep
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.replace(tzinfo=None) if now.tzinfo is not None else now

    all_rows: list[dict] = []
    chunk = timedelta(days=chunk_days)
    cursor = start
    while cursor < end:
        if cursor != start:
            sleep_fn(inter_request_delay)
        cursor_end = min(cursor + chunk, end)
        payload = _fetch_page(http_get, sleep_fn, pair, timeframe, cursor, cursor_end, api_key, max_retries)
        if payload.get("status") == "error":
            raise RuntimeError(f"Twelve Data error response: {payload.get('message', payload)}")
        rows = payload.get("values", [])
        if len(rows) > _PAGE_SIZE_SANITY_BOUND:
            raise RuntimeError(
                f"Twelve Data returned {len(rows)} rows for a {chunk_days}-day chunk; "
                f"page-size assumption broken -- narrow chunk_days"
            )
        all_rows.extend(rows)
        cursor = cursor_end

    candles = [
        Candle(
            timestamp=v["datetime"],
            open=float(v["open"]), high=float(v["high"]), low=float(v["low"]), close=float(v["close"]),
        )
        for v in all_rows
    ]
    # Twelve Data returns each page newest-first; sorting once on the
    # combined result (rather than reversing per page) is correct
    # regardless of how many chunks were fetched.
    candles.sort(key=lambda c: c.timestamp)
    return _trim_unclosed_candle(candles, timeframe, now)


def _fetch_page(http_get, sleep_fn, pair, timeframe, cursor_start, cursor_end, api_key, max_retries) -> dict:
    params = {
        "symbol": pair,
        "interval": timeframe,
        "start_date": cursor_start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": cursor_end.strftime("%Y-%m-%d %H:%M:%S"),
        "apikey": api_key,
        "timezone": "UTC",
    }
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = http_get(TWELVEDATA_BASE_URL, params=params, timeout=10)
        except requests.exceptions.RequestException as exc:
            # Transient network failures (DNS blips, connection resets, an
            # unreachable network for a moment) get the same backoff-and-
            # retry treatment as rate limiting -- a multi-hour, multi-pair
            # backtest run is exactly when a brief outage is likely to hit,
            # and losing the whole run to one blip wastes the pacing budget
            # already spent on the earlier chunks.
            last_exc = exc
            sleep_fn(min(2 ** attempt, 30))
            continue
        if response.status_code == 429:
            sleep_fn(min(2 ** attempt, 30))
            continue
        response.raise_for_status()
        return response.json()
    if last_exc is not None:
        raise RuntimeError(f"Twelve Data request failed after {max_retries} retries: {last_exc}") from last_exc
    raise RuntimeError(f"Twelve Data rate limit exceeded after {max_retries} retries")
