"""Orchestrator: scanner signals -> eligibility -> fill checks -> sizing/caps
-> demo market order with broker-side SL/TP -> close tracking."""
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from executor import risk
from executor.broker.base import AmbiguousResult, BrokerError, MarketClosed, NoQuotes
from executor.signals import by_id, new_signals

log = logging.getLogger("executor.app")
HEARTBEAT = Path(__file__).resolve().parent / ".heartbeat"
RETRYABLE = frozenset({"spread_too_wide", "no_quote", "market_closed"})


def _tag(system: str) -> str:
    return f"⭐ DEMO [{system}]" if system.startswith("PRIME-") else f"DEMO [{system}]"


class Executor:
    def __init__(self, s, broker, state, scanner_conn, notify: Callable[[str], None],
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), kill_file: Path | None = None):
        self.s, self.b, self.state, self.conn, self.notify, self.now = s, broker, state, scanner_conn, notify, now
        self.kill_file = kill_file or Path(__file__).resolve().parent / "KILL"
        self._last_summary_day: str | None = None

    @staticmethod
    def _day_key(now: datetime) -> str:
        return (now - timedelta(hours=22)).strftime("%Y-%m-%d")   # trading day rolls at 22:00 UTC

    def _day_start_equity(self, equity: float) -> float:
        key = self._day_key(self.now())
        if self.state.get("day_key") != key:
            self.state.set("day_key", key)
            self.state.set("day_start_equity", repr(equity))
        return float(self.state.get("day_start_equity"))

    # ------------------------------------------------------------ signals
    def process_signals(self) -> None:
        for sig in new_signals(self.conn, self.state, self.s.max_signal_age_min * 3, self.now()):
            try:
                self._handle(sig)
            except Exception as e:  # noqa: BLE001 -- one bad signal must not block the rest
                log.exception("signal %s failed", sig.id)
                self.state.record(sig.id, sig.system, sig.pair, "rejected", f"error:{type(e).__name__}")
                self.notify(f"⚠️ {_tag(sig.system)} signal #{sig.id} {sig.pair} error: {e}")

    def _skip(self, sig, reason: str) -> None:
        prior = self.state.outcome(sig.id)
        if prior and prior[0] == "deferred" and reason == "stale":
            reason = f"expired:{prior[1]}"
        self.state.record(sig.id, sig.system, sig.pair, "skipped", reason)
        log.info("skip #%s %s [%s]: %s", sig.id, sig.pair, sig.system, reason)

    def _handle(self, sig) -> None:
        s = self.s
        reason = risk.eligibility(sig, self.now(), systems=s.systems, known_pairs=self.b.known_pairs(),
                                  max_age_min=s.max_signal_age_min)
        if reason:
            return self._skip(sig, reason)
        if self.kill_file.exists():
            return self._skip(sig, "kill_switch")
        try:
            q = self.b.quote(sig.pair)
        except NoQuotes:
            return self.state.record(sig.id, sig.system, sig.pair, "deferred", "no_quote")
        reason, est = risk.fill_check(sig, q.bid, q.ask, min_rr_fraction=s.min_rr_fraction,
                                      max_spread_sl_frac=s.max_spread_sl_frac)
        if reason in RETRYABLE:
            return self.state.record(sig.id, sig.system, sig.pair, "deferred", reason)
        if reason:
            return self._skip(sig, reason)

        acct = self.b.account_info()
        info = self.b.symbol_info(sig.pair)
        spec = risk.SymbolSpec(info.contract_size, info.min_lots, info.max_lots, info.step_lots,
                               self.b.quote_to_account(sig.pair))
        lots, money_at_risk, why = risk.size_lots(acct.equity, s.risk_pct, est, sig.sl, spec)
        if why != "ok":
            return self._skip(sig, why)
        block = risk.account_block(equity=acct.equity, day_start_equity=self._day_start_equity(acct.equity),
                                   open_risk=sum(t["risk_amount"] for t in self.state.open_trades()),
                                   new_risk=money_at_risk, max_open_risk_pct=s.max_open_risk_pct,
                                   max_daily_loss_pct=s.max_daily_loss_pct)
        if block:
            self._skip(sig, block)
            if block == "daily_loss_limit":
                self.notify(f"⏸ DEMO new trades blocked (daily loss limit); #{sig.id} {sig.pair} [{sig.system}] skipped")
            return

        label = (f"{_tag(sig.system)} {sig.pair} {sig.direction.upper()} #{sig.id} {lots} lots @~{est} "
                 f"SL {sig.sl} TP {sig.tp} risk {s.risk_pct}%")
        self.state.record(sig.id, sig.system, sig.pair, "accepted")   # claimed: never processed twice
        if s.mode == "dry_run":
            self.state.record(sig.id, sig.system, sig.pair, "placed", "dry_run")
            return self.notify(f"DRY would open: {label}")
        try:
            fill = self.b.place_order(sig.pair, sig.direction, lots, sig.sl, sig.tp, system=sig.system, signal_id=sig.id)
        except AmbiguousResult as e:
            self.state.record(sig.id, sig.system, sig.pair, "rejected", "ambiguous")
            self.notify(f"❓ ambiguous order result #{sig.id} {sig.pair} [{sig.system}]: {e}. Reconciling.")
            return self.reconcile()
        except MarketClosed as e:
            self.state.record(sig.id, sig.system, sig.pair, "deferred", "market_closed")
            return log.info("defer #%s: %s", sig.id, e)
        except BrokerError as e:
            self.state.record(sig.id, sig.system, sig.pair, "rejected", type(e).__name__)
            return self.notify(f"❌ order rejected #{sig.id} {sig.pair} [{sig.system}]: {e}")
        real_risk = abs(fill.price - sig.sl) * info.contract_size * fill.lots * spec.quote_to_account
        self.state.add_trade(signal_id=sig.id, system=sig.system, pair=sig.pair, direction=sig.direction,
                             position_id=fill.position_id, lots=fill.lots, signal_entry=sig.entry,
                             fill_price=fill.price, sl=sig.sl, tp=sig.tp, risk_pct=real_risk / acct.equity * 100,
                             risk_amount=real_risk, commission=fill.commission)
        self.state.record(sig.id, sig.system, sig.pair, "placed")
        self.notify(f"🟢 OPENED {label} | fill {fill.price} (vs signal {sig.entry}) risk ${real_risk:.2f}")

    # ------------------------------------------------------------ closes
    def check_closed(self) -> None:
        trades = self.state.open_trades()
        if not trades:
            return
        live_ids = {p.position_id for p in self.b.positions()}
        for t in trades:
            if t["position_id"] in live_ids:
                continue
            net, closing = self.b.position_result(t["position_id"], t["opened_ts"])
            if closing == 0:
                continue   # gone but no closing deal visible yet: next pass
            r = net / t["risk_amount"] if t["risk_amount"] else 0.0
            self.state.close_trade(t["signal_id"], net, r)
            icon = "✅" if r > 0.05 else ("➖" if r > -0.05 else "❌")
            self.notify(f"{icon} CLOSED {_tag(t['system'])} {t['pair']} #{t['signal_id']}: {r:+.2f}R (${net:+.2f})")

    # ---------------------------------------------------------- reconcile
    def reconcile(self) -> None:
        """Adopt our labelled positions with no local record; never leave one unmanaged."""
        try:
            positions = self.b.positions()
        except BrokerError as e:
            return log.error("reconcile failed: %s", e)
        known = {t["position_id"] for t in self.state.open_trades()}
        for p in positions:
            if p.position_id in known:
                continue
            row = by_id(self.conn, p.signal_id) if p.signal_id is not None else None
            if row is None:
                self.notify(f"⚠️ DEMO orphan position {p.position_id} {p.pair} (comment {p.comment!r}): not tracked")
                continue
            info = self.b.symbol_info(p.pair)
            risk_amt = abs(p.entry - row.sl) * info.contract_size * p.lots * self.b.quote_to_account(p.pair)
            eq = self.b.account_info().equity
            self.state.add_trade(signal_id=row.id, system=row.system, pair=p.pair, direction=p.direction,
                                 position_id=p.position_id, lots=p.lots, signal_entry=row.entry, fill_price=p.entry,
                                 sl=row.sl, tp=row.tp or 0.0, risk_pct=risk_amt / eq * 100, risk_amount=risk_amt,
                                 opened_ts=p.open_ts)
            self.state.record(row.id, row.system, p.pair, "placed", "adopted")
            self.notify(f"🔁 {_tag(row.system)} adopted position {p.position_id} {p.pair} #{row.id}")

    # -------------------------------------------------------------- daily
    def maybe_daily_summary(self) -> None:
        key = self._day_key(self.now())
        if self._last_summary_day in (None, key):
            self._last_summary_day = key
            return
        self._last_summary_day = key
        closed = self.state.closed_since(time.time() - 24 * 3600)
        r = sum(t["realized_r"] or 0 for t in closed)
        money = sum(t["realized_money"] or 0 for t in closed)
        acct = self.b.account_info()
        self.notify(f"📊 DEMO daily: {len(closed)} closed, {r:+.2f}R (${money:+.2f}), "
                    f"{len(self.state.open_trades())} open, equity ${acct.equity:,.2f}")

    def tick(self) -> None:
        self.process_signals()
        self.check_closed()
        self.maybe_daily_summary()
        HEARTBEAT.write_text(str(time.time()))
