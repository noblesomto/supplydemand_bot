from core.edge_bar import score_edge_bar


def _report(n=40, exp=0.5, ci=(0.1, 0.9), dd=10.0, by_year=None, by_pair=None):
    return {
        "num_trades": n, "expectancy_r": exp, "expectancy_r_ci95": list(ci) if ci else None,
        "max_drawdown_pct": dd,
        "by_year": by_year if by_year is not None else {"2025": {"net_profit": 100.0}, "2026": {"net_profit": 50.0}},
        "by_pair": by_pair if by_pair is not None else {
            "EUR/USD": {"net_profit": 10.0}, "GBP/USD": {"net_profit": 20.0}, "USD/JPY": {"net_profit": -5.0},
        },
    }


def test_all_criteria_pass():
    result = score_edge_bar(_report(), _report(), holdout_years=[2025, 2026])
    assert result["verdict"] == "PASS"
    assert all(c["passed"] for c in result["criteria"].values())


def test_too_few_trades_is_inconclusive_not_fail():
    result = score_edge_bar(_report(n=19), _report(), holdout_years=[2025, 2026])
    assert result["criteria"]["min_trades"]["passed"] is False
    assert result["verdict"] == "INCONCLUSIVE"


def test_other_failure_beats_inconclusive():
    result = score_edge_bar(_report(n=19, dd=20.0), _report(), holdout_years=[2025, 2026])
    assert result["verdict"] == "FAIL"


def test_ci_lower_bound_must_be_positive():
    result = score_edge_bar(_report(ci=(-0.2, 1.0)), _report(), holdout_years=[2025, 2026])
    assert result["criteria"]["expectancy"]["passed"] is False


def test_year_without_trades_counts_as_not_profitable():
    result = score_edge_bar(_report(by_year={"2025": {"net_profit": 100.0}}), _report(), holdout_years=[2025, 2026])
    assert result["criteria"]["every_year"]["passed"] is False


def test_pair_share_and_exclusions():
    holdout = _report(by_pair={"A": {"net_profit": 1.0}, "B": {"net_profit": -1.0}, "C": {"net_profit": -2.0}})
    calib = _report(by_pair={"A": {"net_profit": 1.0}, "B": {"net_profit": -3.0}, "C": {"net_profit": 4.0}})
    result = score_edge_bar(holdout, calib, holdout_years=[2025, 2026])
    assert result["criteria"]["pairs"]["passed"] is False
    assert result["excluded_pairs"] == ["B"]
