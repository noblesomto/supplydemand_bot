"""Swing-structure trend from fractal pivots -- no indicators, per the
course (P02: "the chart itself... indicators have proven almost
useless")."""

from dataclasses import dataclass
from enum import Enum

from core.candles import Candle


class PivotKind(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class Pivot:
    index: int
    timestamp: str
    price: float
    kind: PivotKind


class Trend(str, Enum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


def find_pivots(candles: list[Candle], pivot_n: int = 3) -> list[Pivot]:
    """A fractal high at index i: candles[i].high is the strict max over
    [i-pivot_n, i+pivot_n]. A fractal low mirrors this on .low. Only
    confirmable once pivot_n bars after i have closed -- callers that
    care about look-ahead should slice `candles` to "as of now" first."""
    pivots = []
    n = len(candles)
    for i in range(pivot_n, n - pivot_n):
        window = candles[i - pivot_n: i + pivot_n + 1]
        c = candles[i]
        if c.high == max(w.high for w in window) and sum(1 for w in window if w.high == c.high) == 1:
            pivots.append(Pivot(i, c.timestamp, c.high, PivotKind.HIGH))
        if c.low == min(w.low for w in window) and sum(1 for w in window if w.low == c.low) == 1:
            pivots.append(Pivot(i, c.timestamp, c.low, PivotKind.LOW))
    return pivots


def classify_trend(pivots: list[Pivot]) -> Trend:
    """Up: last two confirmed highs rising AND last two confirmed lows
    rising. Down: both falling. Anything else (including <2 of either
    kind) is RANGE."""
    highs = [p for p in pivots if p.kind == PivotKind.HIGH]
    lows = [p for p in pivots if p.kind == PivotKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return Trend.RANGE

    highs_rising = highs[-1].price > highs[-2].price
    highs_falling = highs[-1].price < highs[-2].price
    lows_rising = lows[-1].price > lows[-2].price
    lows_falling = lows[-1].price < lows[-2].price

    if highs_rising and lows_rising:
        return Trend.UP
    if highs_falling and lows_falling:
        return Trend.DOWN
    return Trend.RANGE


def trend_as_of(candles: list[Candle], index: int, pivot_n: int = 3) -> Trend:
    """Trend using only pivots confirmable using candles[:index+1] --
    i.e. no look-ahead past `index`."""
    visible = candles[: index + 1]
    pivots = find_pivots(visible, pivot_n)
    return classify_trend(pivots)
