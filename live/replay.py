"""Replay: run the tested engine unchanged and diff its trades against
what is already stored. Pure functions -- no I/O."""
from dataclasses import dataclass, replace

from backtest_cli import PROFILES
from config import settings
from core.backtest_account import apply_account_simulation
from core.backtest_engine import BacktestTrade, run_backtest
from core.candles import Candle
from rulebook.loader import load_rulebook

FAR_FUTURE = "2999-01-01 00:00:00"


def run_system(profile: str, variant: str | None, pair: str, tfs: dict[str, list[Candle]]) -> list[BacktestTrade]:
    zone_tf, confirm_tf, per = PROFILES[profile]
    rb = load_rulebook(settings.RULEBOOK_PATH, variant=variant, profile=profile)
    if not tfs.get(zone_tf) or not tfs.get(confirm_tf):
        return []
    trades, _ = run_backtest(pair=pair, zone_tf_candles=tfs[zone_tf], confirm_tf_candles=tfs[confirm_tf],
                             confirm_bars_per_zone_bar=per, rulebook=rb)
    return trades


def exit_tp(levels, exit_model: str) -> tuple[float | None, float | None]:
    """(tp, rr) the system actually exits at: fixed_Nr -> N x risk from entry;
    next_level -> the level target; ladder models have no fixed TP."""
    risk = abs(levels.entry - levels.sl)
    sign = 1 if levels.direction == "long" else -1
    if exit_model in ("fixed_2r", "fixed_3r"):
        n = 2.0 if exit_model == "fixed_2r" else 3.0
        return levels.entry + sign * n * risk, n
    if exit_model == "next_level":
        return levels.target, levels.rr
    return None, None


def key(t: BacktestTrade) -> tuple[str, str, str]:
    return (t.pair, t.entry_timestamp, t.direction)


def is_closed(t: BacktestTrade) -> bool:
    return t.outcome.resolved and not t.outcome.open_at_end


@dataclass
class Diff:
    new: list[BacktestTrade]
    closed: list[BacktestTrade]  # stored-open signals whose trade has now closed
    vanished: list[tuple[str, str, str]]  # stored-open keys no longer replayed


def diff(trades: list[BacktestTrade], stored: dict[tuple, str], go_live: str, pairs_ok: set[str]) -> Diff:
    """`stored`: key -> status for this system. Only pairs in `pairs_ok`
    (replayed successfully this cycle) can produce vanished signals."""
    live = [t for t in trades if t.entry_timestamp >= go_live]
    by_key = {key(t): t for t in live}
    new = [t for k, t in by_key.items() if k not in stored]
    closed = [t for k, t in by_key.items() if stored.get(k) == "OPEN" and is_closed(t)]
    vanished = [k for k, s in stored.items() if s == "OPEN" and k[0] in pairs_ok and k not in by_key]
    return Diff(new, closed, vanished)


def account_flags(trades: list[BacktestTrade], go_live: str, risk) -> dict[tuple, str | None]:
    """key -> None if the system's own paper account takes the trade, else a
    skip reason. Open trades count as open indefinitely and carry 0R so a
    mark-to-market value never trips the daily stop."""
    live = sorted((t for t in trades if t.entry_timestamp >= go_live), key=lambda t: t.entry_timestamp)
    sim = [t if is_closed(t) else replace(t, outcome=replace(t.outcome, realized_r=0.0, exit_timestamp=FAR_FUTURE))
           for t in live]
    taken = apply_account_simulation(
        sim, 1000.0, risk.risk_per_trade_pct, max_concurrent_positions=risk.max_concurrent_positions,
        max_daily_loss_pct=risk.max_daily_loss_pct, max_consecutive_losses=risk.max_consecutive_losses)
    taken_keys = {key(a.trade) for a in taken}
    taken_trades = [a.trade for a in taken]
    out = {}
    for t in sim:
        k = key(t)
        if k in taken_keys:
            out[k] = None
            continue
        n_open = sum(1 for o in taken_trades if o.entry_timestamp <= t.entry_timestamp < o.outcome.exit_timestamp)
        out[k] = f"{n_open} open" if n_open >= risk.max_concurrent_positions else "daily stop"
    return out
