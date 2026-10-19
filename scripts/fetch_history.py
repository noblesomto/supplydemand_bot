"""One-off / resumable long-history backfill (spec section 5). Fetches in
priority order H4 -> H1 -> M15, so D1_H4 can be backtested while lower
timeframes are still downloading. Run from the project root:

    python scripts/fetch_history.py [--pairs EUR/USD,GBP/USD] [--skip h1,m15]

Safe to re-run: core.candle_cache.fetch_candles_cached only fetches the
range not already on disk.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.candle_cache import fetch_candles_cached
from core.market_data import fetch_candles
from core.resample import resample_to_d1
from core.candle_cache import save_candles, load_candles

# Twelve Data's free tier does NOT have intraday FX history back to 2016
# as spec section 5 originally assumed -- probed live (see commit notes):
# 4h/EUR-USD 400s ("no data") before ~2020-03-01, 200s from 2020-03-01 on.
# Starting a few weeks earlier than the probed boundary as a buffer; a
# per-chunk 400 is non-fatal (fetch_tf logs and continues to the next
# pair) if a specific pair's own history starts later still.
HISTORY_START = {
    "h4": datetime(2020, 3, 1, tzinfo=timezone.utc),
    "h1": datetime(2020, 3, 1, tzinfo=timezone.utc),
    "m15": datetime(2022, 1, 1, tzinfo=timezone.utc),
}
TWELVEDATA_NATIVE = {"h4": "4h", "h1": "1h", "m15": "15min"}


def fetch_tf(pair: str, tf: str, end: datetime) -> None:
    native = TWELVEDATA_NATIVE[tf]

    def _fetch(pair, _tf_ignored, start, end, api_key):
        return fetch_candles(pair, native, start, end, api_key)

    start = HISTORY_START[tf]
    print(f"  {pair} {tf}: fetching {start.date()}..{end.date()} ...")
    candles = fetch_candles_cached(pair, tf, start, end, settings.TWELVE_DATA_API_KEY_2, fetch_fn=_fetch)
    print(f"  {pair} {tf}: {len(candles)} candles cached")


def build_d1(pair: str) -> None:
    """D1 = H4 resampled on the 22:00-UTC day boundary. yfinance daily
    only fills history from before H4 coverage starts (spec section 5) --
    not fetched here since H4 already reaches back to 2016, well past
    this project's intended backtest start."""
    h4 = load_candles(pair, "h4")
    if not h4:
        print(f"  {pair} d1: no H4 data cached yet, skipping")
        return
    d1 = resample_to_d1(h4)
    save_candles(pair, "d1", d1)
    print(f"  {pair} d1: {len(d1)} candles built from H4")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backfill H4/H1/M15 history and build D1")
    parser.add_argument("--pairs", default="all")
    parser.add_argument("--skip", default="", help="comma-separated timeframes to skip, e.g. h1,m15")
    parser.add_argument("--pace-seconds", type=float, default=1.0, help="pause between pairs")
    args = parser.parse_args(argv)

    pairs = list(settings.PAIRS) if args.pairs == "all" else [p.strip() for p in args.pairs.split(",")]
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    end = datetime.now(timezone.utc)

    for tf in ("h4", "h1", "m15"):
        if tf in skip:
            continue
        print(f"=== {tf.upper()} ===")
        for pair in pairs:
            try:
                fetch_tf(pair, tf, end)
            except Exception as e:
                print(f"  {pair} {tf}: FAILED -- {e}")
            time.sleep(args.pace_seconds)

    print("=== D1 (built from H4) ===")
    for pair in pairs:
        build_d1(pair)


if __name__ == "__main__":
    main()
