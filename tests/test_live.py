from datetime import datetime, timezone

import pandas as pd
import pytest

from core.backtest_engine import BacktestTrade, run_backtest
from core.candle_cache import load_candles
from core.levels import TradeLevels
from core.trade_sim import TradeOutcome
from live import data, notify, replay
from live.scanner import Scanner
from live.store import Signal, get_meta, make_session_factory
from live.systems import LIVE_SYSTEMS
from rulebook.loader import load_rulebook
from config import settings


def _t(pair="EUR/USD", entry="2026-11-02 10:00:00", exit_=None, r=None, open_at_end=False, d="long"):
    out = TradeOutcome(True, r, "target" if exit_ else "open_at_end", exit_ or "2026-11-30 00:00:00",
                       open_at_end=open_at_end or exit_ is None)
    return BacktestTrade(pair, d, "demand", "A", entry, entry, TradeLevels(1.1, 1.09, 1.12, 2.0, d), out)


def test_labels_unique_and_prime_marked():
    labels = [s.label for s in LIVE_SYSTEMS]
    assert len(labels) == len(set(labels)) == 10
    assert {s.label for s in LIVE_SYSTEMS if s.prime} == {"PRIME-H1-CT", "PRIME-H1-AB-2R", "PRIME-H1-A-2R"}
    assert all(s.label.startswith("PRIME-") == s.prime for s in LIVE_SYSTEMS)


def test_diff_new_closed_vanished_and_pre_golive():
    go = "2026-11-01 00:00:00"
    old = _t(entry="2026-10-30 10:00:00")
    open_now = _t(entry="2026-11-02 10:00:00")
    closed_now = _t(entry="2026-11-03 10:00:00", exit_="2026-11-04 10:00:00", r=2.0)
    stored = {replay.key(closed_now): "OPEN", ("EUR/USD", "2026-11-05 10:00:00", "long"): "OPEN",
              ("GBP/USD", "2026-11-05 10:00:00", "long"): "OPEN"}
    d = replay.diff([old, open_now, closed_now], stored, go, pairs_ok={"EUR/USD"})
    assert [replay.key(t) for t in d.new] == [replay.key(open_now)]
    assert [replay.key(t) for t in d.closed] == [replay.key(closed_now)]
    assert d.vanished == [("EUR/USD", "2026-11-05 10:00:00", "long")]  # GBP/USD not replayed -> not vanished


def test_diff_idempotent_once_stored():
    t = _t()
    d = replay.diff([t], {replay.key(t): "OPEN"}, "2026-11-01 00:00:00", {"EUR/USD"})
    assert not d.new and not d.closed and not d.vanished


def test_account_flags_skip_fourth_concurrent():
    risk = load_rulebook(settings.RULEBOOK_PATH, profile="H4_H1").risk
    trades = [_t(pair=p, entry=f"2026-11-02 1{i}:00:00") for i, p in enumerate(["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD"])]
    flags = replay.account_flags(trades, "2026-11-01 00:00:00", risk)
    assert [flags[replay.key(t)] for t in trades] == [None, None, None, "3 open"]


def test_m1_to_m15_drops_forming_bucket():
    idx = pd.date_range("2026-11-02 10:00", periods=20, freq="1min", tz="UTC", name="timestamp")
    df = pd.DataFrame({"bid_open": 1.0, "bid_high": 2.0, "bid_low": 0.5, "bid_close": 1.5, "volume": 1.0}, index=idx)
    m15 = data.m1_to_m15(df)
    assert [c.timestamp for c in m15] == ["2026-11-02 10:00:00"]


def test_append_m1_across_month_boundary(tmp_path):
    idx = pd.date_range("2026-10-31 23:58", periods=4, freq="1min", tz="UTC", name="timestamp")
    df = pd.DataFrame({"bid_open": 1.0, "bid_high": 1.0, "bid_low": 1.0, "bid_close": 1.0, "volume": 1.0}, index=idx)
    data.append_m1("EUR/USD", df, cache=tmp_path)
    data.append_m1("EUR/USD", df.iloc[1:], cache=tmp_path)  # overlap is merged, not duplicated
    assert sorted(f.name for f in (tmp_path / "EURUSD").iterdir()) == ["2026-10.parquet", "2026-11.parquet"]
    back = data.load_m1("EUR/USD", datetime(2026, 10, 1, tzinfo=timezone.utc), cache=tmp_path)
    assert len(back) == 4


def test_notify_labels():
    s = Signal(system="PRIME-H1-CT", pair="EUR/USD", direction="long", zone_type="demand", grade="A",
               entry_timestamp="2026-11-02 10:00:00", entry=1.1, sl=1.09, tp=1.12, rr=2.0, taken=False,
               skip_reason="3 open", status="CLOSED", exit_kind="target", realized_r=1.94)
    assert notify.format_new(s).startswith("⭐ [PRIME-H1-CT] EUR/USD LONG") and "SKIPPED (3 open)" in notify.format_new(s)
    assert "+1.94R" in notify.format_close(s)
    s.system = "H4-AB-2R"
    assert notify.format_new(s).startswith("[H4-AB-2R] ")


class _TG:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def test_scanner_isolates_failing_system(tmp_path, monkeypatch):
    def fake_run(profile, variant, pair, tfs):
        if variant == "sw_counter_trend":
            raise RuntimeError("boom")
        return [_t(pair=pair, entry="2026-11-02 10:00:00")] if pair == "EUR/USD" else []

    monkeypatch.setattr(replay, "run_system", fake_run)
    monkeypatch.setattr(data, "load_m1", lambda pair, since: pd.DataFrame())
    sf = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    tg = _TG()
    sc = Scanner(sf, tg, feed_factory=None, workers=0, pairs=["EUR/USD", "GBP/USD"])
    sc.cycle(now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    sc.cycle(now=datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc))  # second run adds nothing
    with sf() as s:
        rows = s.query(Signal).all()
        assert get_meta(s, "go_live_at") == "2026-11-01 00:00:00"
    assert sorted(r.system for r in rows) == sorted(x.label for x in LIVE_SYSTEMS if x.label != "PRIME-H1-CT")
    assert len(tg.sent) == 9 and all(m.startswith(("[", "⭐ [")) for m in tg.sent)


def test_live_replay_matches_backtest():
    """Signals the live path emits bar by bar equal one full backtest."""
    z_all, c_all = load_candles("EUR/USD", "h4"), load_candles("EUR/USD", "h1")
    if not z_all or not c_all:
        pytest.skip("no cached candles")
    lo, hi = "2025-01-01 00:00:00", "2025-09-01 00:00:00"
    z = [k for k in z_all if lo <= k.timestamp < hi]
    c = [k for k in c_all if lo <= k.timestamp < hi]
    rb = load_rulebook(settings.RULEBOOK_PATH, variant="grid_close_AB_fixed2r", profile="H4_H1")
    full, _ = run_backtest(pair="EUR/USD", zone_tf_candles=z, confirm_tf_candles=c, confirm_bars_per_zone_bar=4, rulebook=rb)
    assert full
    for cut in ["2025-03-01 00:00:00", "2025-05-01 00:00:00", "2025-07-01 00:00:00"]:
        pz = [k for k in z if k.timestamp < cut]
        pc = [k for k in c if k.timestamp < cut]
        part, _ = run_backtest(pair="EUR/USD", zone_tf_candles=pz, confirm_tf_candles=pc, confirm_bars_per_zone_bar=4, rulebook=rb)
        full_before = {replay.key(t): t for t in full if t.entry_timestamp < cut}
        assert {replay.key(t) for t in part} == set(full_before)
        for t in part:
            if replay.is_closed(t):
                assert t.outcome == full_before[replay.key(t)].outcome


def test_exit_tp_matches_exit_model():
    lv = TradeLevels(1.1000, 1.0900, 1.1470, 4.7, "long")
    assert replay.exit_tp(lv, "fixed_2r") == (pytest.approx(1.12), 2.0)
    assert replay.exit_tp(lv, "fixed_3r") == (pytest.approx(1.13), 3.0)
    assert replay.exit_tp(lv, "next_level") == (1.1470, 4.7)
    short = TradeLevels(150.0, 151.0, 140.0, 10.0, "short")
    assert replay.exit_tp(short, "fixed_2r")[0] == pytest.approx(148.0)


def test_scanner_reconnects_feed_after_fetch_error(monkeypatch):
    made = []

    class Feed:
        def __init__(self, fail):
            self.fail = fail

        def trendbars_m1(self, *a):
            if self.fail:
                raise RuntimeError("token expired")
            return pd.DataFrame()

        def stop(self):
            pass

    def factory():
        made.append(Feed(fail=not made))  # first client fails, the next works
        return made[-1]

    monkeypatch.setattr(data, "load_m1", lambda pair, since: pd.DataFrame())
    sc = Scanner(None, _TG(), feed_factory=factory, workers=0, pairs=["EUR/USD"])
    sc.refresh(datetime(2026, 11, 2, 10, tzinfo=timezone.utc))
    sc.refresh(datetime(2026, 11, 2, 10, 15, tzinfo=timezone.utc))
    assert len(made) == 2 and sc.fetch_failures["EUR/USD"] == 0
