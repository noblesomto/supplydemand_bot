"""Spec section 7 edge-found bar: score a holdout report (plus the matching
calib report, for the pair-exclusion rule) against the five criteria."""

MIN_TRADES = 30
MAX_DRAWDOWN_PCT = 15.0
MIN_PAIR_SHARE = 0.6


def score_edge_bar(holdout: dict, calib: dict, holdout_years: list[int]) -> dict:
    ci = holdout.get("expectancy_r_ci95")
    exp = holdout.get("expectancy_r")
    by_year = holdout.get("by_year", {})
    year_profit = {y: by_year.get(str(y), {}).get("net_profit") for y in holdout_years}
    pair_profit = {p: s["net_profit"] for p, s in holdout.get("by_pair", {}).items()}
    winning_pairs = [p for p, v in pair_profit.items() if v > 0]
    pair_share = len(winning_pairs) / len(pair_profit) if pair_profit else 0.0
    calib_pair_profit = {p: s["net_profit"] for p, s in calib.get("by_pair", {}).items()}

    criteria = {
        "expectancy": {
            "passed": exp is not None and exp > 0 and ci is not None and ci[0] > 0,
            "detail": f"expectancy_r={exp} ci95={ci}",
        },
        "every_year": {
            "passed": all(v is not None and v > 0 for v in year_profit.values()),
            "detail": ", ".join(f"{y}: {v}" for y, v in year_profit.items()),
        },
        "pairs": {
            "passed": pair_share >= MIN_PAIR_SHARE,
            "detail": f"{len(winning_pairs)}/{len(pair_profit)} traded pairs profitable ({100 * pair_share:.0f}%)",
        },
        "max_drawdown": {
            "passed": holdout.get("max_drawdown_pct", 0.0) < MAX_DRAWDOWN_PCT,
            "detail": f"max_drawdown_pct={holdout.get('max_drawdown_pct')}",
        },
        "min_trades": {
            "passed": holdout.get("num_trades", 0) >= MIN_TRADES,
            "detail": f"trades={holdout.get('num_trades', 0)} (need {MIN_TRADES})",
        },
    }

    if all(c["passed"] for c in criteria.values()):
        verdict = "PASS"
    elif all(c["passed"] for k, c in criteria.items() if k != "min_trades"):
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAIL"

    excluded = sorted(p for p, v in pair_profit.items() if v < 0 and calib_pair_profit.get(p, 0.0) < 0)
    return {"verdict": verdict, "criteria": criteria, "excluded_pairs": excluded}
