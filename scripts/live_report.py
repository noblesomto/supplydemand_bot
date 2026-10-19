"""Per-system forward results from the live paper DB (spec section 8).
Closed signals only. Raw = every signal; account = signals the system's own
paper account took ($1000, 1% compounding risk).

    python scripts/live_report.py
"""
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from live.store import Signal, get_meta, make_session_factory
from live.systems import LIVE_SYSTEMS

# calib expectancy (R, net of spread) from the 2026-10-19 grids, for decay comparison
CALIB_EXP = {"H4-AB-2R": 0.335, "H4-AB-3R": 0.344, "H4-A-3R": 0.380, "H4-A-2R": 0.289, "PRIME-H1-CT": 0.375,
             "PRIME-H1-AB-2R": 0.249, "H1-AB-NL": 0.267, "H1-AB-3R": 0.197, "PRIME-H1-A-2R": 0.154, "H1-BASE": 0.212}


def _stats(rs):
    n = len(rs)
    if n == 0:
        return "n=0"
    m = sum(rs) / n
    ci = ""
    if n > 1:
        sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
        ci = f" ci95=[{m - 1.96 * sd / math.sqrt(n):+.2f},{m + 1.96 * sd / math.sqrt(n):+.2f}]"
    return f"n={n} win={100 * sum(r > 0 for r in rs) / n:.0f}% expR={m:+.3f}{ci}"


def _account(rows):
    bal = peak = 1000.0
    dd = 0.0
    for r in sorted(rows, key=lambda r: r.exit_timestamp):
        bal += bal * 0.01 * r.realized_r
        peak = max(peak, bal)
        dd = max(dd, 100 * (peak - bal) / peak)
    return bal, dd


def main():
    sf = make_session_factory(settings.DB_URL)
    with sf() as s:
        print(f"go-live {get_meta(s, 'go_live_at')}  last scan {get_meta(s, 'last_scan_at')}\n")
        rows = s.query(Signal).filter(Signal.status == "CLOSED").all()
        open_n = defaultdict(int)
        for r in s.query(Signal).filter(Signal.status == "OPEN"):
            open_n[r.system] += 1
    by = defaultdict(list)
    for r in rows:
        by[r.system].append(r)
    for sys_ in LIVE_SYSTEMS:
        rs = by[sys_.label]
        taken = [r for r in rs if r.taken]
        bal, dd = _account(taken)
        print(f"{sys_.label:15} calib={CALIB_EXP[sys_.label]:+.3f}  open={open_n[sys_.label]}")
        print(f"   raw     {_stats([r.realized_r for r in rs])}")
        print(f"   account {_stats([r.realized_r for r in taken])}  balance=${bal:,.0f} maxDD={dd:.1f}%")
        pairs = defaultdict(float)
        for r in rs:
            pairs[r.pair] += r.realized_r
        if pairs:
            print("   pairs   " + "  ".join(f"{p} {v:+.1f}" for p, v in sorted(pairs.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
