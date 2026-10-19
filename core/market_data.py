from datetime import datetime

import core.feeds.twelvedata as twelvedata_feed
import core.feeds.yfinance_feed as yfinance_feed
from core.candles import Candle


def fetch_candles(
    pair: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    api_key: str,
    *,
    yfinance_fetch=None,
    twelvedata_fetch=None,
    **kwargs,
) -> list[Candle]:
    """Tries yfinance first (free, no daily credit cap) -- falls back to
    Twelve Data only when yfinance has no data for this symbol/timeframe."""
    yfinance_fetch = yfinance_fetch or yfinance_feed.fetch_candles
    twelvedata_fetch = twelvedata_fetch or twelvedata_feed.fetch_candles

    candles = yfinance_fetch(pair, timeframe, start, end)
    if candles:
        return candles
    return twelvedata_fetch(pair, timeframe, start, end, api_key, **kwargs)
