"""Account simulation: compounding position sizing, spread cost in R,
and circuit breakers. Adapted from harmonic_bot's core/backtest_account.py
-- same mechanics, generalized BacktestTrade shape (see backtest_engine.py)."""

from dataclasses import dataclass
from datetime import date, datetime

from core.backtest_engine import BacktestTrade


@dataclass(frozen=True)
class AccountedTrade:
    trade: BacktestTrade
    risk_amount: float
    pnl: float
    balance_after: float


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


def apply_account_simulation(
    trades: list[BacktestTrade],
    start_balance: float,
    risk_pct: float,
    *,
    max_concurrent_positions: int | None = None,
    max_daily_loss_pct: float | None = None,
    max_consecutive_losses: int | None = None,
) -> list[AccountedTrade]:
    """Sizes each trade's risk as risk_pct% of the balance AT ENTRY TIME,
    compounding across the run. Each trade's own `cost_r` (spread
    converted to a fraction of that trade's own R, computed per-trade in
    backtest_engine.py from the pair's spread and that trade's SL
    distance) is already baked into `trade.outcome.realized_r` by the
    time it reaches here.

    `max_concurrent_positions`: a trade whose entry falls while that many
    trades are already open is skipped. `max_daily_loss_pct` /
    `max_consecutive_losses`: per-calendar-day circuit breakers -- once
    either trips, every remaining trade that day is skipped; both reset
    at the next day's first trade.
    """
    ordered = sorted(trades, key=lambda t: t.entry_timestamp)
    balance = start_balance
    open_intervals: list[tuple[datetime, datetime]] = []
    out: list[AccountedTrade] = []

    current_day: date | None = None
    day_start_balance = start_balance
    day_realized_pnl = 0.0
    consecutive_losses = 0
    day_halted = False

    for t in ordered:
        entry_dt = _parse_ts(t.entry_timestamp)
        day = entry_dt.date()
        if day != current_day:
            current_day = day
            day_start_balance = balance
            day_realized_pnl = 0.0
            consecutive_losses = 0
            day_halted = False

        if day_halted:
            continue

        if max_concurrent_positions is not None:
            open_intervals = [iv for iv in open_intervals if iv[1] > entry_dt]
            if len(open_intervals) >= max_concurrent_positions:
                continue
            if t.outcome.exit_timestamp:
                open_intervals.append((entry_dt, _parse_ts(t.outcome.exit_timestamp)))

        risk_amount = balance * risk_pct / 100
        pnl = t.outcome.realized_r * risk_amount
        balance += pnl
        day_realized_pnl += pnl
        out.append(AccountedTrade(trade=t, risk_amount=risk_amount, pnl=pnl, balance_after=balance))

        consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1

        if (
            max_daily_loss_pct is not None
            and day_start_balance > 0
            and (-day_realized_pnl / day_start_balance * 100) >= max_daily_loss_pct
        ):
            day_halted = True
        if max_consecutive_losses is not None and consecutive_losses >= max_consecutive_losses:
            day_halted = True

    return out
