"""Build M15/H1/H4/D1 candle caches from the cTrader M1 bid parquet cache
(spec section 5 update: cTrader replaces Twelve Data as the primary
historical source -- no daily credit ceiling, full coverage, no gaps).

M1 data lives in `data_cache/ctrader_m1/<PAIR>/<YYYY-MM>.parquet`
(one-time copy from /home/www/python/maker_ot's own cTrader cache, same
file layout as marketdata/store.py there -- see README). This script never
fetches: it only reads that M1 cache and resamples locally, replacing
whatever was in data_cache/<PAIR>_{m15,h1,h4,d1}.csv with one
internally-consistent set derived from a single source.

    python scripts/build_from_ctrader_m1.py                     # all pairs
    python scripts/build_from_ctrader_m1.py --pairs EUR/USD,XAU/USD

Future M1 updates beyond this cache's end date need this project's own
cTrader tokens (core/feeds/ctrader.py + ctrader_auth.py) -- not needed for
this one-time rebuild.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from config import settings
from core.candle_cache import save_candles
from core.candles import Candle
from core.resample import resample_candles, resample_to_d1

M1_CACHE = Path(__file__).resolve().parent.parent / "data_cache" / "ctrader_m1"
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _symbol(pair: str) -> str:
    return pair.replace("/", "")


def load_m1(pair: str) -> list[Candle]:
    d = M1_CACHE / _symbol(pair)
    files = sorted(d.glob("*.parquet"))
    if not files:
        return []
    df = pd.concat(pd.read_parquet(f) for f in files).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return [
        Candle(
            timestamp=ts.tz_convert("UTC").strftime(_TS_FORMAT),
            open=float(row["bid_open"]), high=float(row["bid_high"]),
            low=float(row["bid_low"]), close=float(row["bid_close"]),
        )
        for ts, row in df.iterrows()
    ]


def build_pair(pair: str) -> None:
    m1 = load_m1(pair)
    if not m1:
        print(f"  {pair}: no M1 cache found, skipping")
        return
    print(f"  {pair}: {len(m1)} M1 bars ({m1[0].timestamp} .. {m1[-1].timestamp})")

    m15 = resample_candles(m1, "m15")
    save_candles(pair, "m15", m15)
    print(f"  {pair} m15: {len(m15)} bars")

    h1 = resample_candles(m15, "h1")
    save_candles(pair, "h1", h1)
    print(f"  {pair} h1: {len(h1)} bars")

    h4 = resample_candles(h1, "h4")
    save_candles(pair, "h4", h4)
    print(f"  {pair} h4: {len(h4)} bars")

    d1 = resample_to_d1(h4)
    save_candles(pair, "d1", d1)
    print(f"  {pair} d1: {len(d1)} bars")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Rebuild M15/H1/H4/D1 caches from cTrader M1")
    parser.add_argument("--pairs", default="all")
    args = parser.parse_args(argv)

    pairs = list(settings.PAIRS) if args.pairs == "all" else [p.strip() for p in args.pairs.split(",")]
    for pair in pairs:
        build_pair(pair)


if __name__ == "__main__":
    main()
