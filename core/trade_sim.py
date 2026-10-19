"""Bar-by-bar exit simulation on confirmation-TF candles (spec 6.6).
SL-first semantics on same-bar SL/TP ambiguity, matching harmonic_bot's
convention -- intrabar order isn't knowable from OHLC alone."""

from dataclasses import dataclass, replace

from core.candles import Candle
from core.levels import TradeLevels
from core.patterns import bullish_confirmation, bearish_confirmation
from rulebook.models import ExitRules


@dataclass(frozen=True)
class TradeOutcome:
    resolved: bool
    realized_r: float | None
    exit_kind: str | None
    exit_timestamp: str | None
    open_at_end: bool = False


def r_multiple(price: float, entry: float, sl: float, direction: str) -> float:
    risk = abs(entry - sl)
    move = (price - entry) if direction == "long" else (entry - price)
    return move / risk if risk > 0 else 0.0


def _hit_sl(c: Candle, sl: float, direction: str) -> bool:
    return c.low <= sl if direction == "long" else c.high >= sl


def _hit_target(c: Candle, target: float, direction: str) -> bool:
    return c.high >= target if direction == "long" else c.low <= target


def simulate_fixed(levels: TradeLevels, direction: str, candles_after_entry: list[Candle], target: float) -> TradeOutcome:
    """Exit at `target` (used for fixed_2r/fixed_3r and next_level -- the
    only difference between them is what `target` the caller passes in)
    or at SL, whichever is touched first (SL-first on a same-bar tie)."""
    for c in candles_after_entry:
        if _hit_sl(c, levels.sl, direction):
            return TradeOutcome(True, -1.0, "sl", c.timestamp)
        if _hit_target(c, target, direction):
            r = r_multiple(target, levels.entry, levels.sl, direction)
            return TradeOutcome(True, r, "target", c.timestamp)
    return TradeOutcome(False, None, None, None)


def simulate_ladder(
    levels: TradeLevels, direction: str, candles_after_entry: list[Candle],
    opposing_levels: list[float], sl_buffer: float, *, pa_exit: bool = False,
) -> TradeOutcome:
    """No early breakeven. Each time price closes beyond the next
    opposing level and the following bar holds beyond it, the stop moves
    to that level (+/- sl_buffer). Exit only by stop, UNLESS pa_exit is
    set: then an opposite-direction confirmation candle at any reached
    level closes the trade at that candle's close."""
    # Only levels beyond entry in the trade direction can trail the stop;
    # anything behind entry would loosen it past the original -1R.
    ahead = [lv for lv in opposing_levels if (lv > levels.entry if direction == "long" else lv < levels.entry)]
    ordered = sorted(ahead) if direction == "long" else sorted(ahead, reverse=True)
    next_level_idx = 0
    current_sl = levels.sl
    confirm_fn = bearish_confirmation if direction == "long" else bullish_confirmation

    for i, c in enumerate(candles_after_entry):
        if _hit_sl(c, current_sl, direction):
            r = r_multiple(current_sl, levels.entry, levels.sl, direction)
            return TradeOutcome(True, r, "sl_trailed" if current_sl != levels.sl else "sl", c.timestamp)

        if pa_exit and next_level_idx > 0:
            window = candles_after_entry[max(0, i - 2): i + 1]
            if confirm_fn(window):
                r = r_multiple(c.close, levels.entry, levels.sl, direction)
                return TradeOutcome(True, r, "pa_exit", c.timestamp)

        # Advance the ladder: has price closed beyond the next level, and
        # did the following bar (this one, one step later) hold beyond it?
        while next_level_idx < len(ordered):
            level = ordered[next_level_idx]
            closed_beyond = c.close > level if direction == "long" else c.close < level
            if not closed_beyond:
                break
            held = c.low > level if direction == "long" else c.high < level
            if held:
                trailed = level - sl_buffer if direction == "long" else level + sl_buffer
                current_sl = max(current_sl, trailed) if direction == "long" else min(current_sl, trailed)
                next_level_idx += 1
                continue
            break

    return TradeOutcome(False, None, None, None)


def net_of_spread(outcome: TradeOutcome, entry: float, sl: float, spread: float) -> TradeOutcome:
    """Spec 6.5 cost: round-trip spread / this trade's SL distance, in R.
    Candles are bid-only, so the spread is never paid by the fill prices."""
    risk = abs(entry - sl)
    if outcome.realized_r is None or risk <= 0:
        return outcome
    return replace(outcome, realized_r=outcome.realized_r - spread / risk)


def simulate_outcome(
    levels: TradeLevels, direction: str, candles_after_entry: list[Candle],
    exit_rules: ExitRules, *, opposing_levels: list[float] | None = None, sl_buffer: float = 0.0,
) -> TradeOutcome:
    if exit_rules.model == "fixed_2r":
        target = levels.entry + 2 * abs(levels.entry - levels.sl) if direction == "long" else levels.entry - 2 * abs(levels.entry - levels.sl)
        return simulate_fixed(levels, direction, candles_after_entry, target)
    if exit_rules.model == "fixed_3r":
        target = levels.entry + 3 * abs(levels.entry - levels.sl) if direction == "long" else levels.entry - 3 * abs(levels.entry - levels.sl)
        return simulate_fixed(levels, direction, candles_after_entry, target)
    if exit_rules.model == "next_level":
        return simulate_fixed(levels, direction, candles_after_entry, levels.target)
    if exit_rules.model in ("ladder", "ladder_pa_exit"):
        return simulate_ladder(
            levels, direction, candles_after_entry, opposing_levels or [levels.target],
            sl_buffer, pa_exit=(exit_rules.model == "ladder_pa_exit"),
        )
    raise ValueError(f"unknown exit model: {exit_rules.model!r}")
