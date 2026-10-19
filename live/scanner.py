"""One scan cycle: refresh candles, replay every system on every pair in
isolated tasks, sync each system to the DB, send pending alerts."""
import logging
import multiprocessing as mp
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import settings
from live import data, notify, replay
from live.store import Signal, get_meta, set_meta, signals_for
from live.systems import LIVE_SYSTEMS
from rulebook.loader import load_rulebook

log = logging.getLogger(__name__)
REPLAY_ANCHOR = datetime(2026, 1, 1, tzinfo=timezone.utc)
ALERT_MAX_AGE = timedelta(hours=24)
FEED_MAX_AGE = timedelta(hours=12)
_TS = "%Y-%m-%d %H:%M:%S"


def _replay_task(args):
    """All systems of one profile on one pair. Each system isolated."""
    profile, pair, tfs = args
    out = {}
    for sys_ in LIVE_SYSTEMS:
        if sys_.profile != profile:
            continue
        try:
            out[sys_.label] = replay.run_system(profile, sys_.variant, pair, tfs)
        except Exception as e:  # noqa: BLE001 -- one system never stops the others
            out[sys_.label] = e
    return pair, out


class Scanner:
    def __init__(self, session_factory, telegram, feed_factory=None, workers: int = 4, pairs=None):
        """`feed_factory()` returns a started cTrader client; None = cache only."""
        self.sf, self.tg, self.feed_factory = session_factory, telegram, feed_factory
        self.feed, self.feed_started = None, None
        self.pairs = list(pairs or settings.PAIRS)
        self.workers = workers
        self.m1: dict[str, pd.DataFrame] = {}
        self.fetch_failures: dict[str, int] = defaultdict(int)
        self.last_scan_at: datetime | None = None

    # ---- data
    def _ensure_feed(self, now: datetime) -> None:
        """(Re)connect every FEED_MAX_AGE or after an error -- reconnecting
        goes through ctrader_auth.access_token(), which refreshes the token."""
        if self.feed is not None and self.feed_started is not None and now - self.feed_started < FEED_MAX_AGE:
            return
        if self.feed is not None:
            try:
                self.feed.stop()
            except Exception:  # noqa: BLE001
                pass
        self.feed = self.feed_factory()
        self.feed_started = now

    def refresh(self, now: datetime) -> None:
        if self.feed_factory is not None:
            try:
                self._ensure_feed(now)
            except Exception as e:  # noqa: BLE001
                log.error("cTrader connect failed: %s", e)
                self.feed = None
        failed = False
        for pair in self.pairs:
            if pair not in self.m1:
                self.m1[pair] = data.load_m1(pair, REPLAY_ANCHOR)
            if self.feed is None:
                continue
            df = self.m1[pair]
            last = df.index[-1] if not df.empty else pd.Timestamp(REPLAY_ANCHOR)
            try:
                new = data.fetch_new_m1(self.feed, pair, last, now)
                self.fetch_failures[pair] = 0
            except Exception as e:  # noqa: BLE001
                self.fetch_failures[pair] += 1
                failed = True
                log.warning("fetch %s failed (%d in a row): %s", pair, self.fetch_failures[pair], e)
                if self.fetch_failures[pair] == 3:
                    self.tg.send(f"[scanner] {pair}: M1 fetch failed 3 cycles in a row")
                continue
            if not new.empty:
                data.append_m1(pair, new)
                merged = pd.concat([df, new])
                self.m1[pair] = merged[~merged.index.duplicated(keep="last")].sort_index()
        if failed and self.feed is not None:
            self.feed_started = now - FEED_MAX_AGE  # force a reconnect next cycle

    # ---- cycle
    def cycle(self, now: datetime | None = None) -> None:
        now = now or data.utcnow()
        self.refresh(now)
        with self.sf() as s:
            go_live = get_meta(s, "go_live_at")
            if go_live is None:
                go_live = now.strftime(_TS)
                set_meta(s, "go_live_at", go_live)
                s.commit()
                log.info("go-live set to %s", go_live)

        profiles = sorted({x.profile for x in LIVE_SYSTEMS})
        tasks = [(p, pair, data.build_timeframes(self.m1[pair])) for p in profiles for pair in self.pairs]
        by_system: dict[str, dict] = defaultdict(lambda: {"trades": [], "ok": set()})
        for pair, results in self._map(tasks):
            for label, res in results.items():
                if isinstance(res, Exception):
                    log.error("replay %s %s failed: %r", label, pair, res)
                    continue
                by_system[label]["trades"] += res
                by_system[label]["ok"].add(pair)

        for sys_ in LIVE_SYSTEMS:
            try:
                self._sync(sys_, by_system[sys_.label], go_live, now)
            except Exception:  # noqa: BLE001
                log.exception("sync %s failed", sys_.label)
        self._send_pending(now)
        self.last_scan_at = now
        with self.sf() as s:
            set_meta(s, "last_scan_at", now.strftime(_TS))
            s.commit()

    def _map(self, tasks):
        if self.workers <= 0:  # inline, for tests
            return [_replay_task(t) for t in tasks]
        with ProcessPoolExecutor(self.workers, mp_context=mp.get_context("spawn")) as pool:
            return list(pool.map(_replay_task, tasks))

    def _sync(self, sys_, res: dict, go_live: str, now: datetime) -> None:
        with self.sf() as s:
            rows = {(r.pair, r.entry_timestamp, r.direction): r for r in signals_for(s, sys_.label)}
            d = replay.diff(res["trades"], {k: r.status for k, r in rows.items()}, go_live, res["ok"])
            if d.new:
                rb = load_rulebook(settings.RULEBOOK_PATH, variant=sys_.variant, profile=sys_.profile)
                flags = replay.account_flags(res["trades"], go_live, rb.risk)
            for t in d.new:
                tp, rr = replay.exit_tp(t.levels, rb.exit.model)
                reason = flags.get(replay.key(t))
                closed = replay.is_closed(t)
                s.add(Signal(
                    system=sys_.label, pair=t.pair, direction=t.direction, zone_type=t.zone_type, grade=t.grade,
                    entry_timestamp=t.entry_timestamp, entry=t.levels.entry, sl=t.levels.sl,
                    tp=tp, rr=rr, taken=reason is None, skip_reason=reason,
                    status="CLOSED" if closed else "OPEN",
                    exit_kind=t.outcome.exit_kind if closed else None,
                    exit_timestamp=t.outcome.exit_timestamp if closed else None,
                    realized_r=t.outcome.realized_r if closed else None, created_at=now))
            for t in d.closed:
                r = rows[replay.key(t)]
                r.status, r.exit_kind = "CLOSED", t.outcome.exit_kind
                r.exit_timestamp, r.realized_r = t.outcome.exit_timestamp, t.outcome.realized_r
            for k in d.vanished:
                rows[k].status = "VANISHED"
                log.warning("%s %s vanished from replay", sys_.label, k)
            s.commit()

    def _send_pending(self, now: datetime) -> None:
        cutoff = now - ALERT_MAX_AGE
        with self.sf() as s:
            for sig in s.query(Signal).filter(Signal.alerted_at.is_(None)).order_by(Signal.id):
                if sig.created_at.replace(tzinfo=timezone.utc) < cutoff:
                    continue
                if self.tg.send(notify.format_new(sig)):
                    sig.alerted_at = now
            for sig in s.query(Signal).filter(Signal.status.in_(["CLOSED", "VANISHED"]),
                                              Signal.alerted_at.is_not(None),
                                              Signal.closed_alerted_at.is_(None)):
                text = notify.format_close(sig) if sig.status == "CLOSED" else notify.format_vanished(sig)
                if self.tg.send(text):
                    sig.closed_alerted_at = now
            s.commit()
