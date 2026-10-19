"""Candlestick price-action patterns used as confirmation signals
throughout the course (pin bar, engulfing, inside bar). Pure functions
on raw Candle sequences -- no zone/trend knowledge here."""

from core.candles import Candle


def is_bullish_pin_bar(c: Candle, *, wick_body_mult: float = 2.0, wick_range_pct: float = 0.6) -> bool:
    """Long lower wick, small body near the top of the range -- rejection
    of lower prices."""
    if c.range <= 0:
        return False
    lower_wick = c.body_low - c.low
    body = c.body if c.body > 0 else c.range * 0.01  # avoid div-by-zero on doji
    close_pos = (c.close - c.low) / c.range
    return lower_wick >= wick_body_mult * body and lower_wick >= wick_range_pct * c.range and close_pos >= 2 / 3


def is_bearish_pin_bar(c: Candle, *, wick_body_mult: float = 2.0, wick_range_pct: float = 0.6) -> bool:
    """Long upper wick, small body near the bottom of the range."""
    if c.range <= 0:
        return False
    upper_wick = c.high - c.body_high
    body = c.body if c.body > 0 else c.range * 0.01
    close_pos = (c.close - c.low) / c.range
    return upper_wick >= wick_body_mult * body and upper_wick >= wick_range_pct * c.range and close_pos <= 1 / 3


def is_bullish_engulfing(candles: list[Candle], *, lookback: int = 1) -> bool:
    """Current candle's body engulfs the combined body span of the
    previous `lookback` (1-3) candles, which must be net bearish, and
    current candle is bullish. `candles` ends at the candle being tested."""
    if len(candles) < lookback + 1:
        return False
    current = candles[-1]
    prior = candles[-1 - lookback:-1]
    if not current.bullish:
        return False
    prior_body_low = min(p.body_low for p in prior)
    prior_body_high = max(p.body_high for p in prior)
    # net bearish: the group opened above where it closed
    if prior[0].open <= prior[-1].close:
        return False
    return current.body_low <= prior_body_low and current.body_high >= prior_body_high


def is_bearish_engulfing(candles: list[Candle], *, lookback: int = 1) -> bool:
    """Mirror of is_bullish_engulfing."""
    if len(candles) < lookback + 1:
        return False
    current = candles[-1]
    prior = candles[-1 - lookback:-1]
    if not current.bearish:
        return False
    prior_body_low = min(p.body_low for p in prior)
    prior_body_high = max(p.body_high for p in prior)
    # net bullish: the group opened below where it closed
    if prior[0].open >= prior[-1].close:
        return False
    return current.body_low <= prior_body_low and current.body_high >= prior_body_high


def is_inside_bar(current: Candle, previous: Candle) -> bool:
    return current.high < previous.high and current.low > previous.low


def bullish_confirmation(candles: list[Candle], *, engulfing_lookbacks: tuple[int, ...] = (1, 2, 3)) -> bool:
    """True if the last candle in `candles` is a bullish pin bar, a
    bullish engulfing (1/2/3-bar), or an inside bar following the
    previous candle."""
    if not candles:
        return False
    if is_bullish_pin_bar(candles[-1]):
        return True
    for lb in engulfing_lookbacks:
        if is_bullish_engulfing(candles, lookback=lb):
            return True
    if len(candles) >= 2 and is_inside_bar(candles[-1], candles[-2]):
        return True
    return False


def bearish_confirmation(candles: list[Candle], *, engulfing_lookbacks: tuple[int, ...] = (1, 2, 3)) -> bool:
    if not candles:
        return False
    if is_bearish_pin_bar(candles[-1]):
        return True
    for lb in engulfing_lookbacks:
        if is_bearish_engulfing(candles, lookback=lb):
            return True
    if len(candles) >= 2 and is_inside_bar(candles[-1], candles[-2]):
        return True
    return False
