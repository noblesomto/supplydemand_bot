"""Calib-only sweep of the zone/target thresholds that the 2026-10-12
amendment loosened (leg_atr, departure_atr, pivot_n). That retune was
decided on an engine that scanned every historical pivot for targets and
charged no spread, so it is re-examined here on the corrected engine.
Never touches holdout: candles are fetched only up to the calib end.

Per cell: raw per-trade R net of spread (no account simulation, so the
concurrency cap does not reshuffle trades), CI95, the two calib halves
(stability within calib), and the share of traded pairs with net R > 0.

    python scripts/threshold_sweep.py --profile H4_H1 --workers 8
"""
import argparse
import itertools
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_cli import PROFILES
from config import settings
from core.backtest_engine import run_backtest
from core.candle_cache import fetch_candles_cached
from rulebook.loader import _deep_merge, load_rulebook
from rulebook.models import Rulebook

CALIB_START, CALIB_MID, CALIB_END = "2022-01-04", "2023-09-04", "2025-05-04"
LEG_ATR = [0.5, 0.75, 1.0]
DEPARTURE_ATR = [0.3, 1.0, 2.0]
PIVOT_N = [3, 21]
VARIANTS = ["baseline", "grid_close_AB_fixed2r"]


def _utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _run_cell(args):
    profile, variant, leg, dep, piv = args
    zone_tf, confirm_tf, per = PROFILES[profile]
    rb = load_rulebook(settings.RULEBOOK_PATH, variant=None if variant == "baseline" else variant, profile=profile)
    rb = Rulebook.model_validate(_deep_merge(rb.model_dump(), {
        "zone": {"leg_atr": leg, "departure_atr": dep}, "trend": {"pivot_n": piv}}))
    rows = []
    for pair in settings.PAIRS:
        z = fetch_candles_cached(pair, zone_tf, _utc(CALIB_START), _utc(CALIB_END), None)
        c = fetch_candles_cached(pair, confirm_tf, _utc(CALIB_START), _utc(CALIB_END), None)
        if not z or not c:
            continue
        trades, _ = run_backtest(pair=pair, zone_tf_candles=z, confirm_tf_candles=c,
                                 confirm_bars_per_zone_bar=per, rulebook=rb)
        rows += [(pair, t.entry_timestamp, t.outcome.realized_r) for t in trades
                 if t.outcome.realized_r is not None and t.entry_timestamp < CALIB_END]
    return args, rows


def _summ(rows):
    rs = [r for _, _, r in rows]
    n = len(rs)
    if n < 2:
        return {"n": n}
    m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
    a = [r for _, ts, r in rows if ts < CALIB_MID]
    b = [r for _, ts, r in rows if ts >= CALIB_MID]
    by_pair = {}
    for p, _, r in rows:
        by_pair[p] = by_pair.get(p, 0.0) + r
    return {
        "n": n, "exp": m, "lo": m - 1.96 * sd / math.sqrt(n),
        "a": sum(a) / len(a) if a else float("nan"), "b": sum(b) / len(b) if b else float("nan"),
        "pairs": sum(1 for v in by_pair.values() if v > 0) / len(by_pair),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    cells = [(args.profile, v, l, d, p) for v in VARIANTS
             for l, d, p in itertools.product(LEG_ATR, DEPARTURE_ATR, PIVOT_N)]
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(_run_cell, cells))

    print(f"=== {args.profile}: calib-only threshold sweep {CALIB_START}..{CALIB_END} (raw R net of spread)")
    for variant in VARIANTS:
        print(f"\n-- {variant} --")
        print(f"{'leg':>5} {'dep':>4} {'piv':>4} {'n':>5} {'expR':>7} {'ci_lo':>7} {'1st':>7} {'2nd':>7} {'pairs+':>6}")
        for (_, v, l, d, p), rows in results:
            if v != variant:
                continue
            s = _summ(rows)
            if s["n"] < 2:
                print(f"{l:>5} {d:>4} {p:>4} {s['n']:>5}")
                continue
            print(f"{l:>5} {d:>4} {p:>4} {s['n']:>5} {s['exp']:>+7.3f} {s['lo']:>+7.3f} "
                  f"{s['a']:>+7.3f} {s['b']:>+7.3f} {100 * s['pairs']:>5.0f}%")


if __name__ == "__main__":
    main()
