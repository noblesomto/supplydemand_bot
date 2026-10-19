"""Backtest engine: drives zone detection, the setup state machine, and
trade simulation across a pair's zone-TF and confirmation-TF candles.
No look-ahead: at zone-TF bar i, only candles[:i+1] (zone TF) and the
confirmation-TF bars that close within that zone-TF bar are visible.

Known minor look-ahead: detect_zones() confirms a zone's "fast mover"
departure using up to leg_out_bars candles AFTER the leg-out bar, and
this engine marks the zone watchable starting at leg_out_index itself
(not leg_out_index + however many bars the departure actually took to
confirm). A retrace into the zone during that short confirmation window
is vanishingly rare in practice (the zone is defined by departure
already being fast), but is a real discrepancy from strict live
sequencing -- flagged here rather than fixed, since it would require
re-deriving zones incrementally per bar instead of once per series.
"""

from bisect import bisect_left
from dataclasses import dataclass, field, replace

from core.atr import compute_atr_series
from core.candles import Candle
from core.levels import (
    TradeLevels, compute_sl, compute_trade_levels, nearest_opposing_target,
    round_step_for, spread_for,
)
from core.setup_machine import (
    Setup, SetupState, Grade, setup_direction, advance_setup, check_entry_confirmation,
)
from core.trade_sim import simulate_outcome, net_of_spread, TradeOutcome
from core.trend import find_pivots, classify_trend
from core.zones import detect_zones, update_zone_lifecycle, Zone, ZoneStatus, ZoneType
from rulebook.models import Rulebook


@dataclass(frozen=True)
class FunnelCounts:
    zones_detected: int = 0
    retraced: int = 0
    graded_a: int = 0
    graded_b: int = 0
    graded_c: int = 0
    zone_tf_confirmed: int = 0
    confirm_tf_confirmed: int = 0
    passed_rr: int = 0
    entered: int = 0
    expired_by_reason: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestTrade:
    pair: str
    direction: str
    zone_type: str
    grade: str
    zone_confirmed_timestamp: str
    entry_timestamp: str
    levels: TradeLevels
    outcome: TradeOutcome


def _confirm_tf_slice_for_zone_bar(
    confirm_candles: list[Candle], zone_tf_bar_start_ts: str, zone_tf_bar_end_ts: str,
    confirm_ts: list[str],
) -> list[Candle]:
    """`confirm_ts` is the (sorted) timestamps of `confirm_candles`."""
    lo = bisect_left(confirm_ts, zone_tf_bar_start_ts)
    hi = bisect_left(confirm_ts, zone_tf_bar_end_ts, lo)
    return confirm_candles[lo:hi]


def run_backtest(
    *,
    pair: str,
    zone_tf_candles: list[Candle],
    confirm_tf_candles: list[Candle],
    confirm_bars_per_zone_bar: int,
    rulebook: Rulebook,
) -> tuple[list[BacktestTrade], FunnelCounts]:
    """`confirm_bars_per_zone_bar`: how many confirmation-TF candles fall
    inside one zone-TF candle (e.g. 6 for D1/H4, 4 for H4/H1, 4 for
    H1/M15) -- used to map a zone-TF bar index to its confirmation-TF
    slice positionally, since both series share the same bar grid by
    construction (confirm_tf_candles is resampled from the same source
    as zone_tf_candles, see backtest_cli.py).
    """
    zones = detect_zones(zone_tf_candles, rulebook.zone)
    atrs = compute_atr_series(zone_tf_candles, rulebook.zone.atr_period)
    confirm_ts = [c.timestamp for c in confirm_tf_candles]
    confirm_atrs = compute_atr_series(confirm_tf_candles, rulebook.zone.atr_period)
    # A pivot at k depends only on bars [k-n, k+n], so the pivots visible
    # at bar i (find_pivots over candles[:i+1]) are exactly the full-series
    # pivots with index <= i - n. Computed once instead of per bar.
    pivot_n = rulebook.trend.pivot_n
    all_pivots = find_pivots(zone_tf_candles, pivot_n)
    pivot_cursor = 0

    trades: list[BacktestTrade] = []
    funnel = {
        "zones_detected": len(zones), "retraced": 0, "graded_a": 0, "graded_b": 0, "graded_c": 0,
        "zone_tf_confirmed": 0, "confirm_tf_confirmed": 0, "passed_rr": 0, "entered": 0,
        "expired_by_reason": {},
    }

    active_setups: list[Setup] = []
    spread = spread_for(pair, rulebook.risk)
    round_step = round_step_for(pair, rulebook.risk) if rulebook.risk.round_targets else None

    for i, candle in enumerate(zone_tf_candles):
        # Activate newly-started zones (by leg_out_index) as watched setups.
        for z in zones:
            if z.leg_out_index == i:
                active_setups.append(Setup(zone=z, direction=setup_direction(z)))

        while pivot_cursor < len(all_pivots) and all_pivots[pivot_cursor].index <= i - pivot_n:
            pivot_cursor += 1
        visible_pivots = all_pivots[:pivot_cursor]
        trend = classify_trend(visible_pivots)
        active_zones_now = [
            s.zone for s in active_setups if s.zone.status == ZoneStatus.ACTIVE and s.zone.leg_out_index <= i
        ]

        still_active = []
        for setup in active_setups:
            # Setup transition runs BEFORE the zone-break lifecycle check:
            # a bar that trades beyond the distal edge is exactly what a
            # grade-A trap looks like, so the bar that confirms a trap
            # must not be retroactively invalidated by the same bar's
            # "closed beyond distal" break rule below.
            if setup.state == SetupState.WATCHING and setup.zone.status == ZoneStatus.ACTIVE:
                window = zone_tf_candles[max(0, i - 3): i + 1]
                before = setup.state
                setup = advance_setup(setup, candle, i, rulebook.setup, trend, window, rulebook.trend)
                if before == SetupState.WATCHING and setup.state == SetupState.IN_ZONE:
                    funnel["retraced"] += 1

            elif setup.state == SetupState.IN_ZONE:
                window = zone_tf_candles[max(0, i - 3): i + 1]
                setup = advance_setup(setup, candle, i, rulebook.setup, trend, window, rulebook.trend)
                if setup.state == SetupState.ZONE_CONFIRMED:
                    funnel["zone_tf_confirmed"] += 1
                    if setup.grade == Grade.A:
                        funnel["graded_a"] += 1
                    elif setup.grade == Grade.B:
                        funnel["graded_b"] += 1
                elif setup.state == SetupState.EXPIRED:
                    if setup.grade == Grade.C:
                        funnel["graded_c"] += 1
                    funnel["expired_by_reason"][setup.expiry_reason] = funnel["expired_by_reason"].get(setup.expiry_reason, 0) + 1

            # Zone-break lifecycle check, skipped for a setup that just
            # confirmed/entered or already expired on this very bar (see
            # comment above) -- avoids double-counting and avoids a
            # zone-break retroactively invalidating a bar's own trap.
            if setup.zone.status == ZoneStatus.ACTIVE and i > setup.zone.leg_out_index and setup.state not in (
                SetupState.ZONE_CONFIRMED, SetupState.ENTRY, SetupState.EXPIRED,
            ):
                updated_zone = update_zone_lifecycle(setup.zone, candle, i, rulebook.zone, atrs[i])
                if updated_zone is not setup.zone:
                    setup = replace(setup, zone=updated_zone)
                    if updated_zone.status != ZoneStatus.ACTIVE:
                        setup = replace(setup, state=SetupState.EXPIRED, expiry_reason="zone_" + updated_zone.status.value)
                        funnel["expired_by_reason"]["zone_" + updated_zone.status.value] = (
                            funnel["expired_by_reason"].get("zone_" + updated_zone.status.value, 0) + 1
                        )

            if setup.state == SetupState.ZONE_CONFIRMED and setup.confirmed_at_index == i:
                zone_bar_end = zone_tf_candles[i + 1].timestamp if i + 1 < len(zone_tf_candles) else None
                confirm_window = (
                    _confirm_tf_slice_for_zone_bar(confirm_tf_candles, candle.timestamp, zone_bar_end, confirm_ts)
                    if zone_bar_end else []
                )
                # Extend the window to cover h4_window zone-bars' worth of confirm-TF candles.
                extra_bars = []
                j = i + 1
                bars_needed = rulebook.setup.h4_window * confirm_bars_per_zone_bar - len(confirm_window)
                while bars_needed > 0 and j < len(zone_tf_candles):
                    next_end = zone_tf_candles[j + 1].timestamp if j + 1 < len(zone_tf_candles) else None
                    if next_end is None:
                        break
                    extra = _confirm_tf_slice_for_zone_bar(confirm_tf_candles, zone_tf_candles[j].timestamp, next_end, confirm_ts)
                    extra_bars.extend(extra)
                    bars_needed -= len(extra)
                    j += 1
                confirm_window = confirm_window + extra_bars

                setup = check_entry_confirmation(setup, confirm_window, rulebook.setup)
                if setup.state == SetupState.ENTRY:
                    funnel["confirm_tf_confirmed"] += 1
                    trade = _materialize_trade(
                        pair, setup, confirm_window, confirm_tf_candles, zone_tf_candles,
                        rulebook, active_zones_now, visible_pivots, spread, round_step, i,
                        confirm_ts, confirm_atrs,
                    )
                    if trade is not None:
                        funnel["passed_rr"] += 1
                        funnel["entered"] += 1
                        trades.append(trade)
                        setup = replace(setup, zone=replace(setup.zone, status=ZoneStatus.RETIRED))
                    else:
                        setup = replace(setup, state=SetupState.EXPIRED, expiry_reason="rr_rejected")
                        funnel["expired_by_reason"]["rr_rejected"] = funnel["expired_by_reason"].get("rr_rejected", 0) + 1
                elif setup.state == SetupState.EXPIRED:
                    funnel["expired_by_reason"][setup.expiry_reason] = funnel["expired_by_reason"].get(setup.expiry_reason, 0) + 1

            if setup.state not in (SetupState.EXPIRED, SetupState.ENTRY):
                still_active.append(setup)
        active_setups = still_active

    return trades, FunnelCounts(**funnel)


def _materialize_trade(
    pair: str, setup: Setup, confirm_window: list[Candle], all_confirm_candles: list[Candle],
    zone_tf_candles: list[Candle], rulebook: Rulebook, active_zones: list[Zone],
    pivots, spread: float, round_step: float | None, zone_bar_index: int,
    confirm_ts: list[str], confirm_atrs: list,
) -> BacktestTrade | None:
    entry_candle = confirm_window[setup.entry_index]
    entry = entry_candle.close
    direction = setup.direction

    swing_candles = zone_tf_candles[setup.deepest_index: zone_bar_index + 1] or [entry_candle]
    pos = bisect_left(confirm_ts, entry_candle.timestamp)
    entry_idx_in_confirm = pos if pos < len(confirm_ts) and confirm_ts[pos] == entry_candle.timestamp else None
    confirm_atr = confirm_atrs[entry_idx_in_confirm] if entry_idx_in_confirm is not None else None
    sl_buffer = rulebook.risk.sl_buffer_atr * confirm_atr if confirm_atr else 0.0

    sl = compute_sl(direction, swing_candles, sl_buffer, spread)

    other_zones = [z for z in active_zones if z.leg_out_index != setup.zone.leg_out_index]
    target = nearest_opposing_target(direction, entry, other_zones, pivots, round_step=round_step)

    levels = compute_trade_levels(direction, entry, sl, target)
    if levels is None or levels.rr < rulebook.risk.min_rr:
        return None

    entry_pos = entry_idx_in_confirm if entry_idx_in_confirm is not None else -1
    post_entry = all_confirm_candles[entry_pos + 1:] if entry_pos >= 0 else []

    opposite_type = ZoneType.SUPPLY if direction == "long" else ZoneType.DEMAND
    opposing_levels = [z.proximal for z in other_zones if z.zone_type == opposite_type] or [levels.target]
    outcome = simulate_outcome(
        levels, direction, post_entry, rulebook.exit,
        opposing_levels=opposing_levels, sl_buffer=sl_buffer,
    )
    if not outcome.resolved:
        if post_entry:
            last = post_entry[-1]
            mtm_r = (last.close - entry) / abs(entry - sl) if direction == "long" else (entry - last.close) / abs(entry - sl)
            outcome = TradeOutcome(True, mtm_r, "open_at_end", last.timestamp, open_at_end=True)
        else:
            return None

    outcome = net_of_spread(outcome, entry, sl, spread)

    return BacktestTrade(
        pair=pair, direction=direction, zone_type=setup.zone.zone_type.value,
        grade=setup.grade.value if setup.grade else "?",
        zone_confirmed_timestamp=zone_tf_candles[setup.confirmed_at_index].timestamp,
        entry_timestamp=entry_candle.timestamp, levels=levels, outcome=outcome,
    )
