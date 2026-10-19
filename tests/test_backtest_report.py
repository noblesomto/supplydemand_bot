from core.backtest_account import AccountedTrade
from core.backtest_engine import BacktestTrade, FunnelCounts
from core.backtest_report import compute_report
from core.levels import TradeLevels
from core.trade_sim import TradeOutcome


def _trade(entry_ts, realized_r, pair="EUR/USD", direction="long", zone_type="demand", grade="A", exit_kind="target"):
    return BacktestTrade(
        pair=pair, direction=direction, zone_type=zone_type, grade=grade,
        zone_confirmed_timestamp=entry_ts, entry_timestamp=entry_ts,
        levels=TradeLevels(entry=1.0, sl=0.99, target=1.03, rr=3.0, direction=direction),
        outcome=TradeOutcome(True, realized_r, exit_kind, entry_ts),
    )


def test_empty_report():
    report = compute_report([], 10000.0, FunnelCounts())
    assert report["num_trades"] == 0
    assert report["net_profit"] == 0.0


def test_report_computes_headline_stats():
    trades = [_trade("2024-01-01 00:00:00", 2.0), _trade("2024-01-02 00:00:00", -1.0)]
    accounted = [
        AccountedTrade(trade=trades[0], risk_amount=100.0, pnl=200.0, balance_after=10200.0),
        AccountedTrade(trade=trades[1], risk_amount=102.0, pnl=-102.0, balance_after=10098.0),
    ]
    report = compute_report(accounted, 10000.0, FunnelCounts(zones_detected=5, entered=2))
    assert report["num_trades"] == 2
    assert report["win_rate_pct"] == 50.0
    assert report["net_profit"] == 98.0
    assert report["by_pair"]["EUR/USD"]["trades"] == 2
    assert report["funnel"]["zones_detected"] == 5
