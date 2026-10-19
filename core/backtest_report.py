import math
from collections import defaultdict
from datetime import datetime

from core.backtest_account import AccountedTrade
from core.backtest_engine import FunnelCounts


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


def _ci95(values: list[float]) -> tuple[float, float] | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = math.sqrt(variance / n)
    margin = 1.96 * se
    return (mean - margin, mean + margin)


def compute_report(accounted: list[AccountedTrade], start_balance: float, funnel: FunnelCounts) -> dict:
    n = len(accounted)
    base = {
        "num_trades": n, "funnel": funnel.__dict__,
        "start_balance": start_balance, "final_balance": start_balance,
    }
    if n == 0:
        base.update({
            "net_profit": 0.0, "profit_factor": None, "win_rate_pct": None,
            "expectancy_r": None, "expectancy_r_ci95": None, "max_drawdown_pct": 0.0,
            "sharpe_ratio": None, "open_at_end_count": 0,
            "by_year": {}, "by_pair": {}, "by_direction": {}, "by_zone_type": {}, "by_grade": {}, "by_exit_kind": {},
        })
        return base

    pnls = [a.pnl for a in accounted]
    rs = [a.trade.outcome.realized_r for a in accounted]
    wins = [p for p in pnls if p > 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(p for p in pnls if p <= 0))

    equity = [start_balance]
    peak, max_dd_pct = start_balance, 0.0
    for p in pnls:
        value = equity[-1] + p
        equity.append(value)
        peak = max(peak, value)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, 100 * (peak - value) / peak)

    by_day = defaultdict(float)
    for a in accounted:
        by_day[_parse_ts(a.trade.entry_timestamp).date()] += a.pnl
    running, daily_returns = start_balance, []
    for day in sorted(by_day):
        daily_returns.append(by_day[day] / running if running > 0 else 0.0)
        running += by_day[day]
    sharpe = None
    if len(daily_returns) > 1:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((x - mean) ** 2 for x in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(variance)
        if std > 0:
            sharpe = round(math.sqrt(252) * mean / std, 4)

    def _group_stats(items):
        g_rs = [a.trade.outcome.realized_r for a in items]
        g_pnls = [a.pnl for a in items]
        g_wins = [r for r in g_rs if r > 0]
        return {
            "trades": len(items),
            "win_rate_pct": round(100 * len(g_wins) / len(items), 2) if items else None,
            "net_profit": round(sum(g_pnls), 2),
            "expectancy_r": round(sum(g_rs) / len(items), 4) if items else None,
        }

    by_year, by_pair, by_direction, by_zone_type, by_grade, by_exit_kind = (defaultdict(list) for _ in range(6))
    for a in accounted:
        by_year[_parse_ts(a.trade.entry_timestamp).year].append(a)
        by_pair[a.trade.pair].append(a)
        by_direction[a.trade.direction].append(a)
        by_zone_type[a.trade.zone_type].append(a)
        by_grade[a.trade.grade].append(a)
        by_exit_kind[a.trade.outcome.exit_kind or "unresolved"].append(a)

    ci = _ci95(rs)

    return {
        **base,
        "net_profit": round(sum(pnls), 2),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "expectancy_r": round(sum(rs) / n, 4),
        "expectancy_r_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": sharpe,
        "final_balance": round(equity[-1], 2),
        "open_at_end_count": sum(1 for a in accounted if a.trade.outcome.open_at_end),
        "by_year": {str(y): _group_stats(v) for y, v in sorted(by_year.items())},
        "by_pair": {k: _group_stats(v) for k, v in sorted(by_pair.items())},
        "by_direction": {k: _group_stats(v) for k, v in by_direction.items()},
        "by_zone_type": {k: _group_stats(v) for k, v in by_zone_type.items()},
        "by_grade": {k: _group_stats(v) for k, v in by_grade.items()},
        "by_exit_kind": {k: _group_stats(v) for k, v in by_exit_kind.items()},
    }
