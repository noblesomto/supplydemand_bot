"""Per-zone setup state machine (spec 6.4): WATCHING -> IN_ZONE ->
ZONE_CONFIRMED -> ENTRY, or EXPIRED at any point. Advanced one zone-TF
bar at a time; the confirmation-TF scan is a separate step invoked once
a setup reaches ZONE_CONFIRMED."""

from dataclasses import dataclass, replace
from enum import Enum

from core.candles import Candle
from core.patterns import bullish_confirmation, bearish_confirmation
from core.trend import Trend
from core.zones import Zone, ZoneType
from rulebook.models import SetupRules, TrendRules


class SetupState(str, Enum):
    WATCHING = "watching"
    IN_ZONE = "in_zone"
    ZONE_CONFIRMED = "zone_confirmed"
    ENTRY = "entry"
    EXPIRED = "expired"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass
class Setup:
    zone: Zone
    direction: str  # "long" (demand) | "short" (supply)
    state: SetupState = SetupState.WATCHING
    deepest_index: int | None = None
    deepest_price: float | None = None
    deepest_close: float | None = None  # most extreme close since entering the zone (trap_mode=close)
    grade: Grade | None = None
    confirmed_at_index: int | None = None  # zone-TF confirmation candle index
    expiry_reason: str | None = None
    entry_index: int | None = None  # confirmation-TF index, once ENTRY


def setup_direction(zone: Zone) -> str:
    return "long" if zone.zone_type == ZoneType.DEMAND else "short"


def _depth_grade(zone: Zone, deepest_price: float, trap_mode: str, deepest_close: float | None) -> Grade:
    midpoint = (zone.proximal + zone.distal) / 2
    if zone.zone_type == ZoneType.DEMAND:
        beyond_distal = (deepest_close < zone.close_line) if trap_mode == "close" else (deepest_price < zone.distal)
        if beyond_distal:
            return Grade.A
        if deepest_price <= midpoint:
            return Grade.B
        return Grade.C
    else:
        beyond_distal = (deepest_close > zone.close_line) if trap_mode == "close" else (deepest_price > zone.distal)
        if beyond_distal:
            return Grade.A
        if deepest_price >= midpoint:
            return Grade.B
        return Grade.C


def advance_setup(
    setup: Setup, candle: Candle, index: int, rules: SetupRules,
    trend: Trend, recent_zone_tf: list[Candle], trend_rules: TrendRules | None = None,
) -> Setup:
    """Advance by one closed zone-TF bar. `recent_zone_tf` ends with
    `candle` and holds enough history for pattern checks (engulfing
    lookback etc)."""
    trend_rules = trend_rules or TrendRules()
    if setup.zone.status.value != "active":
        if setup.state not in (SetupState.ENTRY, SetupState.EXPIRED):
            return replace(setup, state=SetupState.EXPIRED, expiry_reason="zone_" + setup.zone.status.value)
        return setup

    if setup.state == SetupState.WATCHING:
        if not _trend_allows(setup.direction, trend, trend_rules):
            return setup
        if setup.zone.contains(candle.low, candle.high):
            price = candle.low if setup.direction == "long" else candle.high
            return replace(
                setup, state=SetupState.IN_ZONE,
                deepest_index=index, deepest_price=price, deepest_close=candle.close,
            )
        return setup

    if setup.state == SetupState.IN_ZONE:
        price = candle.low if setup.direction == "long" else candle.high
        deeper = (
            setup.deepest_price is None
            or (setup.direction == "long" and price < setup.deepest_price)
            or (setup.direction == "short" and price > setup.deepest_price)
        )
        if deeper:
            setup = replace(setup, deepest_index=index, deepest_price=price)
        if (
            setup.deepest_close is None
            or (setup.direction == "long" and candle.close < setup.deepest_close)
            or (setup.direction == "short" and candle.close > setup.deepest_close)
        ):
            setup = replace(setup, deepest_close=candle.close)

        if index - setup.deepest_index > rules.confirm_window:
            return replace(setup, state=SetupState.EXPIRED, expiry_reason="confirm_window_elapsed")

        confirmed = (
            bullish_confirmation(recent_zone_tf, engulfing_lookbacks=rules.engulfing_lookbacks)
            if setup.direction == "long"
            else bearish_confirmation(recent_zone_tf, engulfing_lookbacks=rules.engulfing_lookbacks)
        )
        if not confirmed:
            return setup

        grade = _depth_grade(setup.zone, setup.deepest_price, rules.trap_mode, setup.deepest_close)
        if grade.value not in rules.accepted_grades:
            return replace(setup, state=SetupState.EXPIRED, expiry_reason=f"grade_{grade.value}_rejected", grade=grade)

        return replace(setup, state=SetupState.ZONE_CONFIRMED, grade=grade, confirmed_at_index=index)

    return setup


def _trend_allows(direction: str, trend: Trend, trend_rules: TrendRules) -> bool:
    if trend == Trend.RANGE:
        return True
    with_trend = (direction == "long" and trend == Trend.UP) or (direction == "short" and trend == Trend.DOWN)
    if with_trend:
        return True
    return trend_rules.counter_trend_extreme


def check_entry_confirmation(
    setup: Setup, confirm_tf_window: list[Candle], rules: SetupRules,
) -> Setup:
    """Scan confirmation-TF candles that close after the zone-TF
    confirmation bar, up to h4_window bars (expressed as a count of
    confirmation-TF bars supplied by the caller -- see
    backtest_engine.py for how many that is per profile). Entry price is
    the close of whichever bar confirms (or, for ib_entry='break', the
    close of the bar that later breaks that inside bar's range)."""
    if setup.state != SetupState.ZONE_CONFIRMED:
        return setup

    confirm_fn = bullish_confirmation if setup.direction == "long" else bearish_confirmation
    for i in range(1, len(confirm_tf_window) + 1):
        window = confirm_tf_window[:i]
        if not confirm_fn(window, engulfing_lookbacks=rules.engulfing_lookbacks):
            continue
        is_inside_bar_signal = len(window) >= 2 and _is_inside_bar_only(window[-1], window[-2], setup.direction)
        if is_inside_bar_signal and rules.ib_entry == "break":
            broken = _find_break(confirm_tf_window[i:], window[-1], setup.direction)
            if broken is None:
                continue
            return replace(setup, state=SetupState.ENTRY, entry_index=i - 1 + 1 + broken)
        return replace(setup, state=SetupState.ENTRY, entry_index=i - 1)

    if len(confirm_tf_window) >= rules.h4_window:
        return replace(setup, state=SetupState.EXPIRED, expiry_reason="no_confirmation_tf_confirmation")
    return setup


def _is_inside_bar_only(current: Candle, previous: Candle, direction: str) -> bool:
    from core.patterns import is_inside_bar
    return is_inside_bar(current, previous)


def _find_break(following: list[Candle], inside_bar: Candle, direction: str) -> int | None:
    for j, c in enumerate(following):
        if direction == "long" and c.close > inside_bar.high:
            return j
        if direction == "short" and c.close < inside_bar.low:
            return j
    return None
