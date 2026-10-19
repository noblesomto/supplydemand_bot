"""Diagnose calib-vs-holdout disagreement for one profile/variant. Runs the
engine ONCE over the whole history (no cold start at the holdout boundary),
then slices raw per-trade R (no account simulation, no compounding) by
period and by trade attributes. R is net of spread (the engine charges
cost_r = spread / risk per trade); the "gross" line adds it back.

    python scripts/diagnose_split.py --profile H1_M15 --variant grid_wick_AB_nextlevel
"""
import argparse
import math
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_cli import PROFILES
from config import settings
from core.atr import compute_atr_series
from core.backtest_engine import run_backtest
from core.candle_cache import fetch_candles_cached
from core.levels import spread_for
from core.trend import find_pivots, classify_trend
from rulebook.loader import load_rulebook

START, SPLIT, END = "2022-01-04", "2025-05-04", "2026-10-08"


def _utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def collect(profile: str, variant: str | None, pairs: list[str]) -> list[dict]:
    zone_tf, confirm_tf, per = PROFILES[profile]
    rb = load_rulebook(settings.RULEBOOK_PATH, variant=variant, profile=profile)
    rows = []
    for pair in pairs:
        z = fetch_candles_cached(pair, zone_tf, _utc(START), _utc(END), None)
        c = fetch_candles_cached(pair, confirm_tf, _utc(START), _utc(END), None)
        if not z or not c:
            continue
        trades, _ = run_backtest(pair=pair, zone_tf_candles=z, confirm_tf_candles=c,
                                 confirm_bars_per_zone_bar=per, rulebook=rb)
        z_ts = [k.timestamp for k in z]
        atrs = compute_atr_series(z, rb.zone.atr_period)
        pivots = find_pivots(z, rb.trend.pivot_n)
        spread = spread_for(pair, rb.risk)
        # Long-horizon regime: zone-TF ATR relative to its own 1-year-ish median.
        for t in trades:
            zi = max(0, sum(1 for ts in z_ts if ts <= t.entry_timestamp) - 1)
            risk = abs(t.levels.entry - t.levels.sl)
            vis = [p for p in pivots if p.index <= zi - rb.trend.pivot_n]
            window = [a for a in atrs[max(0, zi - 250): zi + 1] if a]
            med = sorted(window)[len(window) // 2] if window else None
            rows.append({
                "pair": pair, "direction": t.direction, "grade": t.grade, "zone_type": t.zone_type,
                "entry": t.entry_timestamp, "exit": t.outcome.exit_timestamp, "exit_kind": t.outcome.exit_kind,
                "r": t.outcome.realized_r, "rr": t.levels.rr,
                "cost_r": spread / risk if risk else 0.0,
                "risk_atr": risk / atrs[zi] if atrs[zi] else None,
                "vol_regime": atrs[zi] / med if med and atrs[zi] else None,
                "trend": classify_trend(vis).value,
                "period": "calib" if t.entry_timestamp < SPLIT else "holdout",
                "half": f"{t.entry_timestamp[:4]}H{1 if int(t.entry_timestamp[5:7]) <= 6 else 2}",
            })
    return rows


def stats(rs: list[float]) -> str:
    n = len(rs)
    if n == 0:
        return "n=0"
    m = sum(rs) / n
    if n > 1:
        sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
        lo, hi = m - 1.96 * sd / math.sqrt(n), m + 1.96 * sd / math.sqrt(n)
        ci = f"[{lo:+.2f},{hi:+.2f}]"
    else:
        ci = ""
    win = 100 * sum(1 for x in rs if x > 0) / n
    return f"n={n:4d} expR={m:+.3f} {ci:16} win={win:4.1f}%"


def table(rows, key, title, net=False):  # net=True -> add spread back (gross)
    print(f"\n-- {title} --  (calib | holdout)")
    groups = defaultdict(lambda: {"calib": [], "holdout": []})
    for r in rows:
        k = key(r)
        if k is None:
            continue
        groups[k][r["period"]].append(r["r"] + (r["cost_r"] if net else 0.0))
    for k in sorted(groups, key=str):
        g = groups[k]
        print(f"  {str(k):14} {stats(g['calib']):48} | {stats(g['holdout'])}")


def bucket(v, edges):
    if v is None:
        return None
    for e in edges:
        if v < e:
            return f"<{e}"
    return f">={edges[-1]}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--pairs", default="all")
    ap.add_argument("--dump", help="pickle the raw rows here")
    args = ap.parse_args(argv)
    pairs = list(settings.PAIRS) if args.pairs == "all" else args.pairs.split(",")
    rows = collect(args.profile, None if args.variant == "baseline" else args.variant, pairs)
    if args.dump:
        pickle.dump(rows, open(args.dump, "wb"))

    print(f"=== {args.profile} / {args.variant}: continuous run {START}..{END}, split at {SPLIT}")
    table(rows, lambda r: "all", "overall, net of spread")
    table(rows, lambda r: "all", "overall, gross (spread added back)", net=True)
    table(rows, lambda r: r["half"], "by half-year")
    table(rows, lambda r: r["direction"], "direction")
    table(rows, lambda r: r["zone_type"] + "/" + r["trend"], "zone type / trend at entry")
    table(rows, lambda r: r["grade"], "grade")
    table(rows, lambda r: r["exit_kind"], "exit kind")
    table(rows, lambda r: bucket(r["rr"], [2.5, 3.5, 5, 8]), "planned RR")
    table(rows, lambda r: bucket(r["risk_atr"], [0.5, 1.0, 1.5, 2.5]), "SL distance in zone-TF ATR")
    table(rows, lambda r: bucket(r["vol_regime"], [0.8, 1.0, 1.25, 1.6]), "volatility regime (ATR / 250-bar median)")
    table(rows, lambda r: bucket(r["cost_r"], [0.02, 0.05, 0.1, 0.2]), "spread cost in R")
    table(rows, lambda r: r["pair"], "pair")


if __name__ == "__main__":
    main()
