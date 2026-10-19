"""Score variants against the spec section 7 edge-found bar. Runs each
variant on calib (for the pair-exclusion rule) and on holdout, logs every
holdout run to holdout_log.md, and writes a markdown summary to
docs/backtest_reports/.

    python scripts/score_edge_bar.py --profile D1_H4 --variants grid_wick_A_ladder,grid_close_AB_ladder --i-mean-it
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_cli import build_report, HOLDOUT_LOG
from config import settings
from core.edge_bar import score_edge_bar
from rulebook.loader import load_rulebook

OUT_DIR = Path("docs/backtest_reports")
CALIB = ("2022-01-04", "2025-05-04")
HOLDOUT = ("2025-05-04", "2026-10-08")


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score variants against the edge-found bar")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--variants", required=True, help="comma-separated; 'baseline' for no variant")
    parser.add_argument("--pairs", default="all")
    parser.add_argument("--i-mean-it", action="store_true", help="required: this runs holdout")
    args = parser.parse_args(argv)
    if not args.i_mean_it:
        raise SystemExit("this runs holdout; pass --i-mean-it (see spec section 7)")

    pairs = list(settings.PAIRS) if args.pairs == "all" else [p.strip() for p in args.pairs.split(",")]
    holdout_years = list(range(_utc(HOLDOUT[0]).year, _utc(HOLDOUT[1]).year + 1))
    lines = [f"# Edge-found bar: {args.profile}, holdout {HOLDOUT[0]} .. {HOLDOUT[1]}", ""]
    results = {}

    for name in [v.strip() for v in args.variants.split(",")]:
        rulebook = load_rulebook(settings.RULEBOOK_PATH, variant=None if name == "baseline" else name,
                                 profile=args.profile)
        print(f"  {name} ...", flush=True)
        calib = build_report(pairs, args.profile, _utc(CALIB[0]), _utc(CALIB[1]), rulebook)
        holdout = build_report(pairs, args.profile, _utc(HOLDOUT[0]), _utc(HOLDOUT[1]), rulebook)
        with open(HOLDOUT_LOG, "a") as f:
            f.write(f"- {datetime.now(timezone.utc).isoformat()} | profile={args.profile} variant={name} "
                    f"period={HOLDOUT[0]}..{HOLDOUT[1]} trades={holdout['num_trades']} "
                    f"expectancy_r={holdout.get('expectancy_r')} net_profit={holdout.get('net_profit')} "
                    f"(score_edge_bar.py)\n")
        score = score_edge_bar(holdout, calib, holdout_years)
        results[name] = {"score": score, "holdout": holdout, "calib_by_pair": calib["by_pair"]}
        print(f"    {score['verdict']}")

        lines += [f"## {name}: **{score['verdict']}**", "", "| criterion | pass | detail |", "|---|---|---|"]
        for crit, c in score["criteria"].items():
            lines.append(f"| {crit} | {'yes' if c['passed'] else 'no'} | {c['detail']} |")
        lines += ["", f"Excluded pairs (lost in calib and holdout): {', '.join(score['excluded_pairs']) or 'none'}", "",
                  "| pair | holdout trades | holdout net | calib net |", "|---|---|---|---|"]
        for pair, s in holdout["by_pair"].items():
            lines.append(f"| {pair} | {s['trades']} | {s['net_profit']} | "
                         f"{calib['by_pair'].get(pair, {}).get('net_profit')} |")
        lines.append("")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = OUT_DIR / f"edge_bar_{args.profile}_{stamp}.md"
    md_path.write_text("\n".join(lines))
    md_path.with_suffix(".json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {md_path}")


if __name__ == "__main__":
    main()
