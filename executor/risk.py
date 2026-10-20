"""Pure decisions: eligibility, fill-time checks, sizing, account caps."""
import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SymbolSpec:
    contract_size: float
    min_lots: float
    max_lots: float
    step_lots: float
    quote_to_account: float


def round_down_to_step(lots: float, step: float) -> float:
    return round(math.floor(lots / step + 1e-9) * step, 8)


def size_lots(equity: float, risk_pct: float, entry: float, sl: float, spec: SymbolSpec) -> tuple[float, float, str]:
    """(lots, money at risk, reason). Never rounds up past the risk budget."""
    loss_per_lot = abs(entry - sl) * spec.contract_size * spec.quote_to_account
    if equity <= 0 or risk_pct <= 0 or loss_per_lot <= 0:
        return 0.0, 0.0, "bad_sizing_inputs"
    lots = round_down_to_step(min(equity * risk_pct / 100 / loss_per_lot, spec.max_lots), spec.step_lots)
    if lots < spec.min_lots:
        return 0.0, 0.0, "below_min_volume"
    return lots, lots * loss_per_lot, "ok"


def eligibility(sig, now: datetime, *, systems, known_pairs, max_age_min: float) -> str | None:
    if sig.system not in systems:
        return "system_disabled"
    if not sig.taken:
        return "paper_skipped"           # mirror the paper account: it did not take this one
    if sig.status != "OPEN":
        return f"status_{sig.status.lower()}"
    if sig.pair not in known_pairs:
        return "no_symbol_mapping"
    if (now - sig.created_at).total_seconds() > max_age_min * 60:
        return "stale"
    if sig.tp is None:
        return "no_tp"
    ok = sig.sl < sig.entry < sig.tp if sig.direction == "long" else sig.sl > sig.entry > sig.tp
    return None if ok else "bad_geometry"


def fill_check(sig, bid: float, ask: float, *, min_rr_fraction: float, max_spread_sl_frac: float) -> tuple[str | None, float]:
    """(reason or None, expected fill). Longs fill at the ask, shorts at the bid."""
    est = ask if sig.direction == "long" else bid
    risk = (est - sig.sl) if sig.direction == "long" else (sig.sl - est)
    reward = (sig.tp - est) if sig.direction == "long" else (est - sig.tp)
    if risk <= 0:
        return "price_beyond_sl", est
    if reward <= 0:
        return "price_beyond_tp", est
    if (ask - bid) > max_spread_sl_frac * abs(sig.entry - sig.sl):
        return "spread_too_wide", est
    planned = abs(sig.tp - sig.entry) / abs(sig.entry - sig.sl)
    if reward / risk < min_rr_fraction * planned:
        return "price_drift", est
    return None, est


def account_block(*, equity: float, day_start_equity: float, open_risk: float, new_risk: float,
                  max_open_risk_pct: float, max_daily_loss_pct: float) -> str | None:
    if day_start_equity > 0 and (day_start_equity - equity) / day_start_equity * 100 >= max_daily_loss_pct:
        return "daily_loss_limit"
    if equity <= 0 or (open_risk + new_risk) / equity * 100 > max_open_risk_pct + 1e-9:
        return "open_risk_cap"
    return None
