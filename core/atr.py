from core.candles import Candle


def compute_atr(candles: list[Candle], at_index: int, period: int = 14) -> float | None:
    """Simple average true range over `period` candles ending at_index
    inclusive -- causal, never reads past at_index. None if not enough
    history (needs at_index >= period)."""
    if at_index < period:
        return None
    true_ranges = []
    for i in range(at_index - period + 1, at_index + 1):
        c = candles[i]
        prev_close = candles[i - 1].close
        true_ranges.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    return sum(true_ranges) / period


def compute_atr_series(candles: list[Candle], period: int = 14) -> list[float | None]:
    """ATR at every index, for convenience in backtests/tests."""
    return [compute_atr(candles, i, period) for i in range(len(candles))]
