import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from core.backtest_account import apply_account_simulation
from core.backtest_engine import run_backtest, BacktestTrade, FunnelCounts
from core.backtest_report import compute_report
from core.candle_cache import fetch_candles_cached
from rulebook.loader import load_rulebook

# profile -> (zone TF cache key, confirm TF cache key, confirm bars per zone bar)
PROFILES = {
    "D1_H4": ("d1", "h4", 6),
    "H4_H1": ("h4", "h1", 4),
    "H1_M15": ("h1", "m15", 4),
}

HOLDOUT_LOG = Path("docs/backtest_reports/holdout_log.md")


def _merge_funnels(funnels: list[FunnelCounts]) -> FunnelCounts:
    merged = {}
    for f in funnels:
        for k, v in f.__dict__.items():
            if k == "expired_by_reason":
                merged.setdefault(k, {})
                for reason, count in v.items():
                    merged[k][reason] = merged[k].get(reason, 0) + count
            else:
                merged[k] = merged.get(k, 0) + v
    return FunnelCounts(**merged)


def build_report(
    pairs: list[str], profile: str, start: datetime, end: datetime, rulebook,
    *, fetch_fn=fetch_candles_cached, start_balance: float = 10000.0,
) -> dict:
    zone_tf, confirm_tf, confirm_bars_per_zone_bar = PROFILES[profile]
    all_trades: list[BacktestTrade] = []
    funnels: list[FunnelCounts] = []

    for pair in pairs:
        zone_candles = fetch_fn(pair, zone_tf, start, end, settings.TWELVE_DATA_API_KEY_2)
        confirm_candles = fetch_fn(pair, confirm_tf, start, end, settings.TWELVE_DATA_API_KEY_2)
        if not zone_candles or not confirm_candles:
            continue
        trades, funnel = run_backtest(
            pair=pair, zone_tf_candles=zone_candles, confirm_tf_candles=confirm_candles,
            confirm_bars_per_zone_bar=confirm_bars_per_zone_bar, rulebook=rulebook,
        )
        all_trades.extend(trades)
        funnels.append(funnel)

    accounted = apply_account_simulation(
        all_trades, start_balance, rulebook.risk.risk_per_trade_pct,
        max_concurrent_positions=rulebook.risk.max_concurrent_positions,
        max_daily_loss_pct=rulebook.risk.max_daily_loss_pct,
        max_consecutive_losses=rulebook.risk.max_consecutive_losses,
    )
    return compute_report(accounted, start_balance, _merge_funnels(funnels) if funnels else FunnelCounts())


def _log_holdout_run(args, report: dict) -> None:
    HOLDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"- {datetime.now(timezone.utc).isoformat()} | profile={args.profile} variant={args.variant} "
        f"period={args.start}..{args.end} trades={report['num_trades']} "
        f"expectancy_r={report.get('expectancy_r')} net_profit={report.get('net_profit')}\n"
    )
    with open(HOLDOUT_LOG, "a") as f:
        f.write(line)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="supplydemand_bot backtester")
    parser.add_argument("--profile", required=True, choices=list(PROFILES))
    parser.add_argument("--variant", default=None, help="named variant from rulebook/rules.yaml")
    parser.add_argument("--pairs", default="all")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--start-balance", type=float, default=10000.0)
    parser.add_argument("--period", choices=["calib", "holdout"], default="calib")
    parser.add_argument("--i-mean-it", action="store_true", help="required to run --period holdout")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.period == "holdout" and not args.i_mean_it:
        raise SystemExit("--period holdout requires --i-mean-it (see spec section 7)")

    pairs = list(settings.PAIRS) if args.pairs == "all" else [p.strip() for p in args.pairs.split(",")]
    rulebook = load_rulebook(settings.RULEBOOK_PATH, variant=args.variant, profile=args.profile)
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    print(f"Fetching {len(pairs)} pairs, profile={args.profile}, {args.start}..{args.end} ...")
    report = build_report(pairs, args.profile, start, end, rulebook, start_balance=args.start_balance)
    print(json.dumps(report, indent=2, default=str))

    if args.period == "holdout":
        _log_holdout_run(args, report)


if __name__ == "__main__":
    main()
