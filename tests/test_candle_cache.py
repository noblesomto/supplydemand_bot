from datetime import datetime, timezone

from core.candles import Candle
from core.candle_cache import fetch_candles_cached, save_candles, load_candles, merge_candles


def mk(ts, v=1.0):
    return Candle(timestamp=ts, open=v, high=v + 0.01, low=v - 0.01, close=v)


def test_cold_cache_fetches_whole_range(tmp_path):
    calls = []

    def fetch_fn(pair, tf, start, end, api_key):
        calls.append((start, end))
        return [mk("2024-01-01 00:00:00"), mk("2024-01-02 00:00:00")]

    result = fetch_candles_cached(
        "EUR/USD", "d1",
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc),
        "key", fetch_fn=fetch_fn, cache_dir=tmp_path,
    )
    assert len(result) == 2
    assert len(calls) == 1


def test_warm_cache_skips_fetch_when_fully_covered(tmp_path):
    save_candles("EUR/USD", "d1", [mk("2024-01-01 00:00:00"), mk("2024-01-05 00:00:00")], cache_dir=tmp_path)

    def fetch_fn(*a, **k):
        raise AssertionError("should not fetch -- range already cached")

    result = fetch_candles_cached(
        "EUR/USD", "d1",
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 5, tzinfo=timezone.utc),
        "key", fetch_fn=fetch_fn, cache_dir=tmp_path,
    )
    assert len(result) == 2


def test_extends_cache_forward_only(tmp_path):
    save_candles("EUR/USD", "d1", [mk("2024-01-01 00:00:00"), mk("2024-01-02 00:00:00")], cache_dir=tmp_path)
    calls = []

    def fetch_fn(pair, tf, start, end, api_key):
        calls.append((start, end))
        return [mk("2024-01-03 00:00:00")]

    result = fetch_candles_cached(
        "EUR/USD", "d1",
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 3, tzinfo=timezone.utc),
        "key", fetch_fn=fetch_fn, cache_dir=tmp_path,
    )
    assert len(calls) == 1
    assert len(result) == 3
    reloaded = load_candles("EUR/USD", "d1", cache_dir=tmp_path)
    assert len(reloaded) == 3


def test_merge_candles_new_wins_on_conflict():
    existing = [mk("2024-01-01 00:00:00", v=1.0)]
    new = [mk("2024-01-01 00:00:00", v=2.0)]
    merged = merge_candles(existing, new)
    assert len(merged) == 1
    assert merged[0].open == 2.0
