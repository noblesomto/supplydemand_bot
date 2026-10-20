from datetime import datetime, timedelta, timezone

import pytest

from executor import risk
from executor.app import Executor
from executor.broker.base import AccountInfo, BrokerPosition, Fill, Quote, SymbolInfo, make_comment, parse_comment
from executor.config import ConfigError, load_settings
from executor.signals import open_readonly
from executor.state import State
from live.store import Signal, make_session_factory

NOW = datetime(2026, 11, 2, 10, 5, tzinfo=timezone.utc)
ENV = {"EXEC_MODE": "demo", "EXEC_CT_ACCOUNT_ID": "123", "CT_ENV": "demo"}


class FakeBroker:
    def __init__(self, bid=1.1000, ask=1.1001, equity=10000.0):
        self.q = Quote(bid, ask, 0)
        self.equity = equity
        self.placed, self.open_positions, self.results = [], {}, {}

    def known_pairs(self):
        return {"EUR/USD", "USD/JPY"}

    def quote(self, pair):
        return self.q

    def account_info(self):
        return AccountInfo(123, False, "USD", self.equity, self.equity, 0, self.equity, 100)

    def symbol_info(self, pair):
        return SymbolInfo(pair, pair.replace("/", ""), 1, 5, 4, 100000, 0.01, 100, 0.01, *pair.split("/"))

    def quote_to_account(self, pair):
        return 1.0

    def place_order(self, pair, direction, lots, sl, tp, *, system, signal_id):
        pid = 1000 + signal_id
        self.placed.append((pair, direction, lots, sl, tp, system, signal_id))
        self.open_positions[pid] = BrokerPosition(pid, pair, direction, lots, self.q.ask, sl, tp, "sdbot",
                                                  make_comment(system, signal_id), 0)
        return Fill(pid, self.q.ask, lots)

    def positions(self):
        return list(self.open_positions.values())

    def position_result(self, position_id, since):
        return self.results.get(position_id, (0.0, 0))


def _db(tmp_path, *rows):
    sf = make_session_factory(f"sqlite:///{tmp_path}/scan.db")
    with sf() as s:
        for i, kw in enumerate(rows, start=1):
            base = dict(system="H4-AB-2R", pair="EUR/USD", direction="long", zone_type="demand", grade="A",
                        entry_timestamp=f"2026-11-02 09:{i:02d}:00", entry=1.1000, sl=1.0950, tp=1.1100, rr=2.0,
                        taken=True, status="OPEN", created_at=NOW - timedelta(minutes=2))
            base.update(kw)
            s.add(Signal(**base))
        s.commit()
    return open_readonly(f"{tmp_path}/scan.db")


def _ex(tmp_path, conn, broker, **env):
    msgs = []
    s = load_settings({**ENV, **env})
    return Executor(s, broker, State(tmp_path / "ex.db"), conn, msgs.append, now=lambda: NOW,
                    kill_file=tmp_path / "KILL"), msgs


def test_comment_roundtrip_uses_sdbot_label():
    assert make_comment("PRIME-H1-CT", 7) == "sdbot:PRIME-H1-CT:7"
    assert parse_comment("sdbot:PRIME-H1-CT:7") == ("PRIME-H1-CT", 7)
    assert parse_comment("hbot:MAIN:7") == (None, None)


def test_places_sized_order_with_signal_sl_tp(tmp_path):
    b = FakeBroker()
    ex, msgs = _ex(tmp_path, _db(tmp_path, {}), b)
    ex.tick()
    pair, d, lots, sl, tp, system, sid = b.placed[0]
    # 0.25% of 10000 = $25 over a 0.0051 stop on 100k units -> 0.04 lots (rounded down)
    assert (pair, d, sl, tp, system, sid, lots) == ("EUR/USD", "long", 1.0950, 1.1100, "H4-AB-2R", 1, 0.04)
    assert "OPENED DEMO [H4-AB-2R]" in msgs[-1] and "━" in msgs[-1]
    ex.tick()
    assert len(b.placed) == 1  # never twice


def test_prime_tag_and_skips(tmp_path):
    b = FakeBroker()
    conn = _db(tmp_path, {"system": "PRIME-H1-CT"}, {"taken": False}, {"status": "CLOSED"},
               {"created_at": NOW - timedelta(minutes=30)}, {"pair": "GBP/CHF"})
    ex, msgs = _ex(tmp_path, conn, b)
    ex.tick()
    assert [p[6] for p in b.placed] == [1] and msgs[-1].startswith("🟢 OPENED ⭐ DEMO [PRIME-H1-CT]")
    assert ex.state.skip_counts() == {"paper_skipped": 1, "status_closed": 1, "stale": 1, "no_symbol_mapping": 1}


def test_fill_checks():
    from executor.signals import SignalRow
    sig = SignalRow(1, "X", "EUR/USD", "long", 1.1000, 1.0950, 1.1100, 2.0, True, "OPEN", NOW, "")
    kw = dict(min_rr_fraction=0.7, max_spread_sl_frac=0.25)
    assert risk.fill_check(sig, 1.1000, 1.1001, **kw)[0] is None
    assert risk.fill_check(sig, 1.0940, 1.0945, **kw)[0] == "price_beyond_sl"
    assert risk.fill_check(sig, 1.1100, 1.1101, **kw)[0] == "price_beyond_tp"
    assert risk.fill_check(sig, 1.1040, 1.1041, **kw)[0] == "price_drift"      # 0.59R left of 2R planned
    assert risk.fill_check(sig, 1.1000, 1.1020, **kw)[0] == "spread_too_wide"
    short = SignalRow(2, "X", "EUR/USD", "short", 1.1000, 1.1050, 1.0900, 2.0, True, "OPEN", NOW, "")
    assert risk.fill_check(short, 1.1000, 1.1001, **kw) == (None, 1.1000)


def test_spread_defers_then_places(tmp_path):
    b = FakeBroker(bid=1.1000, ask=1.1020)
    ex, _ = _ex(tmp_path, _db(tmp_path, {}), b)
    ex.tick()
    assert not b.placed and ex.state.outcome(1) == ("deferred", "spread_too_wide")
    b.q = Quote(1.1000, 1.1001, 0)
    ex.tick()
    assert len(b.placed) == 1


def test_open_risk_cap_and_daily_stop(tmp_path):
    b = FakeBroker()
    ex, _ = _ex(tmp_path, _db(tmp_path, {}, {}, {}), b, EXEC_MAX_OPEN_RISK_PCT="0.5")
    ex.tick()
    assert len(b.placed) == 2 and ex.state.skip_counts() == {"open_risk_cap": 1}
    (tmp_path / "x").mkdir()
    b2 = FakeBroker()
    ex2, msgs = _ex(tmp_path / "x", _db(tmp_path / "x", {}), b2)
    ex2.state.set("day_key", ex2._day_key(NOW))
    ex2.state.set("day_start_equity", "10400")
    ex2.tick()
    assert not b2.placed and ex2.state.skip_counts() == {"daily_loss_limit": 1} and "daily loss" in msgs[-1]


def test_close_tracking_reports_r(tmp_path):
    b = FakeBroker()
    ex, msgs = _ex(tmp_path, _db(tmp_path, {}), b)
    ex.tick()
    t = ex.state.open_trades()[0]
    del b.open_positions[t["position_id"]]
    b.results[t["position_id"]] = (2 * t["risk_amount"], 1)
    ex.tick()
    assert not ex.state.open_trades() and msgs[-1].startswith("✅ CLOSED DEMO [H4-AB-2R] WIN — EUR/USD") and "+2.00R" in msgs[-1]


def test_reconcile_adopts_own_position(tmp_path):
    b = FakeBroker()
    ex, msgs = _ex(tmp_path, _db(tmp_path, {}), b)
    b.open_positions[555] = BrokerPosition(555, "EUR/USD", "long", 0.04, 1.1001, 1.095, 1.11, "sdbot",
                                           make_comment("H4-AB-2R", 1), 0)
    ex.reconcile()
    assert ex.state.open_trades()[0]["position_id"] == 555 and "adopted" in msgs[-1]


def test_dry_run_never_places(tmp_path):
    b = FakeBroker()
    ex, msgs = _ex(tmp_path, _db(tmp_path, {}), b, EXEC_MODE="dry_run")
    ex.tick()
    assert not b.placed and ex.state.outcome(1) == ("placed", "dry_run") and msgs[-1].startswith("🧪 DRY would open")


def test_kill_switch(tmp_path):
    b = FakeBroker()
    ex, _ = _ex(tmp_path, _db(tmp_path, {}, {}), b)
    (tmp_path / "KILL").touch()
    ex.tick()
    assert not b.placed and ex.state.skip_counts() == {"kill_switch": 2}


def test_config_validation():
    with pytest.raises(ConfigError):
        load_settings({"EXEC_MODE": "demo"})                 # no account
    with pytest.raises(ConfigError):
        load_settings({**ENV, "EXEC_SYSTEMS": "H4-AB-2R,NOPE"})
    assert load_settings(ENV).risk_pct == 0.25 and len(load_settings(ENV).systems) == 10
