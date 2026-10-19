"""Does calib performance predict holdout performance across variants?
One continuous run per variant (no cold start), raw per-trade R net of
spread (no account simulation), split at the calib/holdout boundary.
Reports each variant's calib vs holdout expectancy and the Spearman rank
correlation between them -- near zero means calib ranking is noise and
picking the calib winner carries no information.

    python scripts/rank_stability.py --profile H1_M15 --workers 4
"""
import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from config import settings
from scripts.diagnose_split import collect

CALIB_MID = "2023-09-04"  # splits calib roughly in half, for a holdout-free stability check


def _summary(args):
    profile, name = args
    rows = collect(profile, None if name == "baseline" else name, list(settings.PAIRS))
    out = {}
    buckets = {
        "calib": lambda r: r["period"] == "calib",
        "holdout": lambda r: r["period"] == "holdout",
        "calib_a": lambda r: r["entry"] < CALIB_MID,
        "calib_b": lambda r: CALIB_MID <= r["entry"] and r["period"] == "calib",
    }
    for period, keep in buckets.items():
        rs = [r["r"] for r in rows if keep(r)]
        out[period] = (len(rs), sum(rs) / len(rs) if rs else float("nan"))
    return name, out


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for rank, i in enumerate(order):
        ranks[i] = float(rank)
    return ranks


def spearman(x, y):
    rx, ry = _ranks(x), _ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else float("nan")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    with open(settings.RULEBOOK_PATH) as f:
        names = ["baseline"] + sorted(yaml.safe_load(f).get("variants", {}))
    with ProcessPoolExecutor(args.workers) as pool:
        results = dict(pool.map(_summary, [(args.profile, n) for n in names]))

    print(f"=== {args.profile}: calib vs holdout expectancy (raw R net of spread, continuous run)")
    print(f"{'variant':26} {'calib_n':>7} {'calib_expR':>10} {'hold_n':>7} {'hold_expR':>10}")
    for name, r in sorted(results.items(), key=lambda kv: -kv[1]["calib"][1]):
        print(f"{name:26} {r['calib'][0]:>7} {r['calib'][1]:>+10.3f} {r['holdout'][0]:>7} {r['holdout'][1]:>+10.3f}")
    valid = [r for r in results.values() if r["calib"][0] and r["holdout"][0]]
    rho = spearman([r["calib"][1] for r in valid], [r["holdout"][1] for r in valid])
    print(f"\nSpearman rho(calib, holdout) over {len(valid)} variants: {rho:+.2f}")
    rho_in = spearman([r["calib_a"][1] for r in valid], [r["calib_b"][1] for r in valid])
    print(f"Spearman rho(calib first half, calib second half, split {CALIB_MID}): {rho_in:+.2f}")
    for name in sorted(results, key=lambda n: -results[n]["calib_a"][1])[:5]:
        r = results[name]
        print(f"  top first-half {name:26} first={r['calib_a'][1]:+.3f} (n={r['calib_a'][0]}) second={r['calib_b'][1]:+.3f}")


if __name__ == "__main__":
    main()
