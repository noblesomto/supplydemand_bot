"""A trade must be visible as soon as its entry bar has closed: running the
engine on data cut at any point T yields every full-series trade whose
entry bar closed by T, with identical levels (no look-ahead, no lag)."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest_cli import PROFILES
from config import settings
from live import data
from live.replay import run_system

M1 = data.M1_CACHE / "GBPJPY"


@pytest.mark.skipif(not (M1 / "2024-03.parquet").exists(), reason="needs cached cTrader M1 data")
@pytest.mark.parametrize("profile,variant,start", [("H1_M15", "grid_close_AB_fixed2r", 2024),
                                                   ("H4_H1", "grid_close_AB_fixed2r", 2022)])
def test_trades_visible_once_entry_bar_closes(profile, variant, start):
    m1 = data.load_m1("GBP/JPY", datetime(start, 1, 1, tzinfo=timezone.utc))
    m1 = m1[m1.index < pd.Timestamp("2024-06-01", tz="UTC")]
    full = run_system(profile, variant, "GBP/JPY", data.build_timeframes(m1))
    confirm_len = pd.Timedelta(minutes=15) if PROFILES[profile][1] == "m15" else pd.Timedelta(hours=1)
    checked = 0
    for t in full[5:25]:
        cut = pd.Timestamp(t.entry_timestamp, tz="UTC") + confirm_len   # entry bar just closed
        part = run_system(profile, variant, "GBP/JPY", data.build_timeframes(m1[m1.index < cut]))
        got = {(p.entry_timestamp, p.direction): p.levels for p in part}
        assert (t.entry_timestamp, t.direction) in got, f"{t.entry_timestamp} not visible at {cut}"
        lv = got[(t.entry_timestamp, t.direction)]
        assert (lv.entry, lv.sl, lv.target) == (t.levels.entry, t.levels.sl, t.levels.target)
        checked += 1
    assert checked >= 10
