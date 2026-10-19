"""Run the spec section 7 core variant set on the calib period for one
profile, and write a comparison summary (JSON + markdown) to
docs/backtest_reports/. Selection happens on calib only; the chosen
variant(s) then run once on holdout via backtest_cli.py --period holdout
--i-mean-it.

    python scripts/run_calib_grid.py --profile D1_H4 --start 2022-01-04 --end 2025-05-04
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from backtest_cli import build_report
from config import settings
from rulebook.loader import load_rulebook

OUT_DIR = Path("docs/backtest_reports")
HEADLINE_FIELDS = [
    "num_trades", "win_rate_pct", "expectancy_r", "expectancy_r_ci95",
    "profit_factor", "net_profit", "max_drawdown_pct", "sharpe_ratio", "open_at_end_count",
]


def _run_variant(name: str, pairs: list[str], profile: str, start: datetime, end: datetime) -> dict:
    variant = None if name == "baseline" else name
    rulebook = load_rulebook(settings.RULEBOOK_PATH, variant=variant, profile=profile)
    return build_report(pairs, profile, start, end, rulebook)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the core calib variant grid")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--pairs", default="all")
    parser.add_argument("--workers", type=int, default=1, help="variants run in parallel processes")
    args = parser.parse_args(argv)

    pairs = list(settings.PAIRS) if args.pairs == "all" else [p.strip() for p in args.pairs.split(",")]
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    with open(settings.RULEBOOK_PATH) as f:
        variant_names = ["baseline"] + sorted(yaml.safe_load(f).get("variants", {}))

    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {name: pool.submit(_run_variant, name, pairs, args.profile, start, end) for name in variant_names}
        for name in variant_names:
            report = futures[name].result()
            results[name] = report
            print(f"  {name}: trades={report['num_trades']} expectancy_r={report.get('expectancy_r')} "
                  f"net_profit={report.get('net_profit')} funnel.entered={report['funnel']['entered']}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"calib_grid_{args.profile}_{stamp}.json"
    md_path = OUT_DIR / f"calib_grid_{args.profile}_{stamp}.md"
    json_path.write_text(json.dumps(results, indent=2, default=str))

    lines = [
        f"# Calib grid: {args.profile}, {args.start} .. {args.end}", "",
        "| variant | " + " | ".join(HEADLINE_FIELDS) + " | funnel.entered |",
        "|---|" + "---|" * (len(HEADLINE_FIELDS) + 1),
    ]
    for name, r in results.items():
        row = [str(r.get(f)) for f in HEADLINE_FIELDS] + [str(r["funnel"]["entered"])]
        lines.append(f"| {name} | " + " | ".join(row) + " |")
    md_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {json_path} and {md_path}")


if __name__ == "__main__":
    main()
