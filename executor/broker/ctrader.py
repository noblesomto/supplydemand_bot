"""cTrader Open API broker. Twisted runs in a background thread; the public
methods are synchronous and thread-safe (blockingCallFromThread).

Field names/units checked against help.ctrader.com/open-api and the
installed ctrader_open_api 0.9.2 protobufs. Demo-first: every order path
goes through _guard_live()."""
import logging
import statistics
import threading
import time
from collections import deque
from typing import Callable

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
from twisted.internet import defer, reactor, task, threads

from executor.broker import scaling as sc
from executor.broker.base import (AccountInfo, AmbiguousResult, BadStops, BadVolume, BrokerError,
                                  BrokerOrder, BrokerPosition, Fill, LiveAccountRefused, MarketClosed,
                                  NoQuotes, NotEnoughMoney, Quote, SymbolInfo, TokenExpired,
                                  make_comment)
from executor.config import Settings
from executor.state import State
from executor.tokens import TokenManager

log = logging.getLogger("executor.broker")
LABEL = "sdbot"
DEMO_HOST, LIVE_HOST, PORT = "demo.ctraderapi.com", "live.ctraderapi.com", 5035

_ERR_MAP = {
    "OA_AUTH_TOKEN_EXPIRED": TokenExpired, "CH_ACCESS_TOKEN_INVALID": TokenExpired,
    "NOT_ENOUGH_MONEY": NotEnoughMoney, "MARKET_CLOSED": MarketClosed, "TRADING_DISABLED": MarketClosed,
    "SYMBOL_HAS_HOLIDAY": MarketClosed, "TRADING_BAD_VOLUME": BadVolume,
    "TRADING_BAD_STOPS": BadStops, "PROTECTION_IS_TOO_CLOSE_TO_MARKET": BadStops,
    "TRADING_BAD_PRICES": BadStops, "NO_QUOTES": NoQuotes,
}

_reactor_thread: threading.Thread | None = None


def _ensure_reactor() -> None:
    global _reactor_thread
    if _reactor_thread is None:
        _reactor_thread = threading.Thread(
            target=lambda: reactor.run(installSignalHandlers=False), name="twisted-reactor", daemon=True)
        _reactor_thread.start()


def _error_for(code_name: str, desc: str = "") -> BrokerError:
    return _ERR_MAP.get(code_name, BrokerError)(f"{code_name}: {desc}".strip(": "))


class CTraderBroker:
    def __init__(self, settings: Settings, tokens: TokenManager, state: State,
                 pairs: list[str], on_reconnect: Callable[[], None] | None = None):
        self.s, self.tokens, self.state, self.pairs = settings, tokens, state, list(pairs)
        self.on_reconnect = on_reconnect
        self._symbols: dict[str, SymbolInfo] = {}          # pair -> info
        self._lot_size: dict[str, int] = {}                # pair -> protocol lotSize
        self._by_id: dict[int, str] = {}                   # symbolId -> pair/broker name
        self._name_to_id: dict[str, int] = {}              # broker name -> symbolId (all symbols)
        self._quotes: dict[int, Quote] = {}
        self._spreads: dict[int, deque] = {}   # symbolId -> recent (5s bucket, spread) samples, ~1h
        self._buckets: dict[int, dict[int, list[float]]] = {}   # symbolId -> {5s bucket: [max_bid, min_ask]}
        self._quote_cb: Callable[[str, Quote], None] | None = None
        self._asset_names: dict[int, str] = {}
        self._ctid = settings.ct_account_id
        self._is_live = False
        self._acct_ccy = "USD"
        self._money_digits = 2
        self._leverage = 0.0
        self._exec_events: dict[str, list] = {}
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._client: Client | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 90.0) -> None:
        _ensure_reactor()
        host = LIVE_HOST if self.s.ct_env == "live" else DEMO_HOST
        self._client = Client(host, PORT, TcpProtocol, numberOfMessagesToSendPerSecond=30)
        self._client.setConnectedCallback(lambda c: self._bootstrap_bg())
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)
        reactor.callFromThread(self._client.startService)
        if not self._ready.wait(timeout):
            raise BrokerError(f"cTrader did not become ready within {timeout}s: {self._start_error}")
        if self._start_error:
            raise self._start_error

    def stop(self) -> None:
        if self._client:
            reactor.callFromThread(self._client.stopService)

    def _on_disconnected(self, client, reason) -> None:
        log.warning("cTrader disconnected: %s (auto-reconnect via ClientService)", reason)
        self._ready.clear()

    def _bootstrap_bg(self) -> None:
        d = self._bootstrap()
        d.addCallbacks(self._bootstrap_ok, self._bootstrap_fail)

    def _bootstrap_ok(self, _):
        first = not self._ready.is_set()
        self._ready.set()
        if self.on_reconnect:
            threading.Thread(target=self.on_reconnect, name="reconcile", daemon=True).start()
        log.info("cTrader ready (account %s, live=%s, ccy=%s)%s", self._ctid, self._is_live,
                 self._acct_ccy, "" if first else " [reconnected]")

    def _bootstrap_fail(self, failure):
        log.error("cTrader bootstrap failed: %s", failure.getErrorMessage())
        self._start_error = failure.value if isinstance(failure.value, Exception) else BrokerError(str(failure))
        self._ready.set()

    # ------------------------------------------------------------- requests
    def _send(self, msg, timeout: float = 10.0, client_msg_id: str | None = None):
        d = self._client.send(msg, clientMsgId=client_msg_id, responseTimeoutInSeconds=timeout)
        d.addCallback(self._decode)
        return d

    @staticmethod
    def _decode(proto_msg):
        payload = Protobuf.extract(proto_msg)
        name = type(payload).__name__
        if name == "ProtoOAErrorRes":
            raise _error_for(payload.errorCode, payload.description)
        if name == "ProtoOAOrderErrorEvent":
            raise _error_for(payload.errorCode, payload.description)
        return payload

    @defer.inlineCallbacks
    def _bootstrap(self):
        d = yield self._send(M.ProtoOAApplicationAuthReq(clientId=self.s.ct_client_id,
                                                         clientSecret=self.s.ct_client_secret))
        for attempt in (1, 2):
            try:
                token = self.tokens.access_token
                accts = yield self._send(M.ProtoOAGetAccountListByAccessTokenReq(accessToken=token))
                acct = self._pick_account(accts)
                self._ctid, self._is_live = acct.ctidTraderAccountId, acct.isLive
                self._guard_environment()
                yield self._send(M.ProtoOAAccountAuthReq(ctidTraderAccountId=self._ctid, accessToken=token))
                break
            except TokenExpired:
                if attempt == 2:
                    raise
                log.warning("token expired, refreshing")
                yield threads.deferToThread(self.tokens.refresh)
        yield self._load_assets()
        yield self._load_symbols()
        yield self._load_trader()
        yield self._subscribe_spots()

    def _pick_account(self, res):
        accts = list(res.ctidTraderAccount)
        want = self.s.ct_account_id
        if want:
            accts = [a for a in accts if a.ctidTraderAccountId == want]
        if len(accts) != 1:
            raise BrokerError(f"expected exactly one matching cTrader account, got {len(accts)}; set CT_ACCOUNT_ID")
        return accts[0]

    def _guard_environment(self) -> None:
        if self._is_live and not (self.s.mode == "live" and self.s.allow_live):
            raise LiveAccountRefused("authenticated account isLive=True but EXEC_MODE=live and ALLOW_LIVE=true are not both set")
        if self.s.ct_env == "demo" and self._is_live:
            raise LiveAccountRefused("CT_ENV=demo but the account is live")

    def _guard_live(self) -> None:
        self._guard_environment()

    @defer.inlineCallbacks
    def _load_assets(self):
        res = yield self._send(M.ProtoOAAssetListReq(ctidTraderAccountId=self._ctid))
        self._asset_names = {a.assetId: a.name for a in res.asset}

    @defer.inlineCallbacks
    def _load_symbols(self):
        res = yield self._send(M.ProtoOASymbolsListReq(ctidTraderAccountId=self._ctid), timeout=30)
        light = {s.symbolId: s for s in res.symbol}
        self._name_to_id = {s.symbolName: s.symbolId for s in light.values()}
        wanted = {}
        for pair in self.pairs:
            bn = sc.broker_name(pair)
            sid = self._name_to_id.get(bn)
            if sid is None:
                log.error("no broker symbol for %s (%s): this pair will be refused", pair, bn)
                continue
            wanted[pair] = sid
        if wanted:
            det = yield self._send(M.ProtoOASymbolByIdReq(ctidTraderAccountId=self._ctid,
                                                         symbolId=list(wanted.values())), timeout=30)
            full = {s.symbolId: s for s in det.symbol}
            for pair, sid in wanted.items():
                s, l = full[sid], light[sid]
                base, quote = sc.split_pair(pair)
                self._lot_size[pair] = s.lotSize
                self._by_id[sid] = pair
                self._symbols[pair] = SymbolInfo(
                    pair=pair, broker_name=l.symbolName, symbol_id=sid, digits=s.digits, pip_position=s.pipPosition,
                    contract_size=sc.contract_size(s.lotSize), min_lots=sc.volume_to_lots(s.minVolume, s.lotSize),
                    max_lots=sc.volume_to_lots(s.maxVolume, s.lotSize), step_lots=sc.volume_to_lots(s.stepVolume, s.lotSize),
                    base_ccy=base, quote_ccy=quote)

    @defer.inlineCallbacks
    def _load_trader(self):
        res = yield self._send(M.ProtoOATraderReq(ctidTraderAccountId=self._ctid))
        t = res.trader
        self._money_digits = t.moneyDigits or 2
        self._acct_ccy = self._asset_names.get(t.depositAssetId, "USD")
        self._leverage = t.leverageInCents / 100.0 if t.leverageInCents else float(t.maxLeverage or 0)

    @defer.inlineCallbacks
    def _subscribe_spots(self):
        ids = {s.symbol_id for s in self._symbols.values()}
        # extra symbols needed to convert each quote currency into the account currency
        for s in list(self._symbols.values()):
            for name in (f"{s.quote_ccy}{self._acct_ccy}", f"{self._acct_ccy}{s.quote_ccy}"):
                if name in self._name_to_id:
                    ids.add(self._name_to_id[name])
                    self._by_id.setdefault(self._name_to_id[name], name)
        yield self._send(M.ProtoOASubscribeSpotsReq(ctidTraderAccountId=self._ctid, symbolId=sorted(ids)), timeout=30)

    # --------------------------------------------------------- push events
    def _on_message(self, client, msg) -> None:
        try:
            payload = Protobuf.extract(msg)
        except Exception:
            return
        name = type(payload).__name__
        if name == "ProtoOASpotEvent":
            prev = self._quotes.get(payload.symbolId)
            bid = sc.price_from_int(payload.bid) if payload.bid else (prev.bid if prev else 0.0)
            ask = sc.price_from_int(payload.ask) if payload.ask else (prev.ask if prev else 0.0)
            if bid and ask:
                q = Quote(bid, ask, time.time())
                self._quotes[payload.symbolId] = q
                self._record_extreme(payload.symbolId, q)
                pair = self._by_id.get(payload.symbolId)
                if self._quote_cb and pair in self._symbols:
                    self._quote_cb(pair, q)
        elif name == "ProtoOAExecutionEvent":
            coid = payload.order.clientOrderId if payload.HasField("order") else ""
            if coid:
                self._exec_events.setdefault(coid, []).append(payload)
        elif name == "ProtoOAAccountsTokenInvalidatedEvent":
            log.warning("token invalidated by server: %s", payload.reason)
        elif name == "ProtoOAClientDisconnectEvent":
            log.warning("server asked us to disconnect: %s", payload.reason)

    BUCKET_S = 5
    KEEP_S = 3 * 24 * 3600

    def _record_extreme(self, sid: int, q: Quote) -> None:
        b = self._buckets.setdefault(sid, {})
        k = int(q.ts // self.BUCKET_S)
        cur = b.get(k)
        if cur is None:
            if q.ask >= q.bid > 0:
                self._spreads.setdefault(sid, deque(maxlen=720)).append(q.ask - q.bid)
            b[k] = [q.bid, q.ask]
            cutoff = k - self.KEEP_S // self.BUCKET_S
            for old in [x for x in b if x < cutoff][:50]:
                del b[old]
        else:
            cur[0] = max(cur[0], q.bid)
            cur[1] = min(cur[1], q.ask)

    def typical_spread(self, pair: str) -> float | None:
        """Median of the last ~hour of sampled spreads (one per 5 s), so the filter adapts to session and pair.
        None until a minute of samples exists (right after a restart the plain percentage rule applies)."""
        d = self._spreads.get(self.symbol_info(pair).symbol_id)
        if not d or len(d) < 12:
            return None
        return statistics.median(d)

    def extremes_since(self, pair: str, since_ts: float) -> tuple[float, float] | None:
        """(highest bid, lowest ask) seen on the spot stream since `since_ts` (5s resolution), so a TP1 touch
        between management polls is not missed. None if nothing recorded (e.g. stream down)."""
        b = self._buckets.get(self.symbol_info(pair).symbol_id)
        if not b:
            return None
        lo = int(since_ts // self.BUCKET_S)
        vals = [v for k, v in list(b.items()) if k >= lo]
        if not vals:
            return None
        return max(v[0] for v in vals), min(v[1] for v in vals)

    # ----------------------------------------------------------- sync API
    def _call(self, fn, *a, **kw):
        return threads.blockingCallFromThread(reactor, fn, *a, **kw)

    def symbol_info(self, pair: str) -> SymbolInfo:
        try:
            return self._symbols[pair]
        except KeyError:
            raise BrokerError(f"no symbol mapping for {pair}")

    def known_pairs(self) -> set[str]:
        return set(self._symbols)

    def subscribe_spots(self, pairs=None, callback=None) -> None:
        self._quote_cb = callback  # subscription itself happens during bootstrap for all pairs

    def quote(self, pair: str) -> Quote:
        q = self._quotes.get(self.symbol_info(pair).symbol_id)
        if q is None or time.time() - q.ts > 120:
            raise NoQuotes(f"no fresh quote for {pair}")
        return q

    def _mids(self) -> dict[str, float]:
        out = {}
        for sid, q in self._quotes.items():
            n = self._by_id.get(sid)
            if n:
                out[sc.broker_name(n)] = (q.bid + q.ask) / 2
        return out

    def quote_to_account(self, pair: str) -> float:
        try:
            return sc.quote_to_account_rate(self.symbol_info(pair).quote_ccy, self._acct_ccy, self._mids())
        except KeyError as e:
            raise NoQuotes(str(e))

    @defer.inlineCallbacks
    def _account_info(self):
        t = yield self._send(M.ProtoOATraderReq(ctidTraderAccountId=self._ctid))
        rec = yield self._send(M.ProtoOAReconcileReq(ctidTraderAccountId=self._ctid))
        pnl = yield self._send(M.ProtoOAGetPositionUnrealizedPnLReq(ctidTraderAccountId=self._ctid))
        md = t.trader.moneyDigits or self._money_digits
        balance = sc.money(t.trader.balance, md)
        unreal = sum(sc.money(p.netUnrealizedPnL, pnl.moneyDigits) for p in pnl.positionUnrealizedPnL)
        used = sum(sc.money(p.usedMargin, p.moneyDigits or md) for p in rec.position)
        eq = balance + unreal
        return AccountInfo(self._ctid, self._is_live, self._acct_ccy, balance, eq, used, eq - used, self._leverage)

    def account_info(self) -> AccountInfo:
        return self._call(self._account_info)

    @defer.inlineCallbacks
    def _margin_per_lot(self, pair: str):
        info = self.symbol_info(pair)
        vol = sc.lots_to_volume(1.0, self._lot_size[pair])
        res = yield self._send(M.ProtoOAExpectedMarginReq(ctidTraderAccountId=self._ctid,
                                                         symbolId=info.symbol_id, volume=[vol]))
        m = res.margin[0]
        return sc.money(max(m.buyMargin, m.sellMargin), res.moneyDigits or self._money_digits)

    def expected_margin_per_lot(self, pair: str) -> float:
        return self._call(self._margin_per_lot, pair)

    def _pos_from_proto(self, p) -> BrokerPosition | None:
        pair = self._pair_for_symbol(p.tradeData.symbolId)
        if pair is None:
            return None
        td = p.tradeData
        return BrokerPosition(
            position_id=p.positionId, pair=pair, direction="long" if td.tradeSide == MM.BUY else "short",
            lots=sc.volume_to_lots(td.volume, self._lot_size[pair]), entry=p.price,
            sl=p.stopLoss or None, tp=p.takeProfit or None, label=td.label, comment=td.comment,
            open_ts=td.openTimestamp / 1000.0,
            swap=sc.money(p.swap, p.moneyDigits or self._money_digits),
            commission=sc.money(p.commission, p.moneyDigits or self._money_digits))

    def _pair_for_symbol(self, sid: int) -> str | None:
        pair = self._by_id.get(sid)
        return pair if pair in self._symbols else None

    @defer.inlineCallbacks
    def _reconcile(self):
        res = yield self._send(M.ProtoOAReconcileReq(ctidTraderAccountId=self._ctid))
        return res

    def positions(self) -> list[BrokerPosition]:
        """Only OUR positions (label == sdbot)."""
        res = self._call(self._reconcile)
        out = [self._pos_from_proto(p) for p in res.position if p.tradeData.label == LABEL]
        return [p for p in out if p]

    def pending_orders(self) -> list[BrokerOrder]:
        res = self._call(self._reconcile)
        out = []
        for o in res.order:
            td = o.tradeData
            pair = self._pair_for_symbol(td.symbolId)
            if td.label != LABEL or pair is None:
                continue
            out.append(BrokerOrder(
                o.orderId, pair, "long" if td.tradeSide == MM.BUY else "short",
                sc.volume_to_lots(td.volume, self._lot_size[pair]), MM.ProtoOAOrderType.Name(o.orderType),
                o.limitPrice or None, o.stopPrice or None, o.stopLoss or None, o.takeProfit or None,
                td.label, td.comment, o.positionId or None, o.clientOrderId))
        return out

    @defer.inlineCallbacks
    def _position_result(self, position_id: int, since_ts: float):
        res = yield self._send(M.ProtoOADealListByPositionIdReq(
            ctidTraderAccountId=self._ctid, positionId=position_id,
            fromTimestamp=int((since_ts - 3600) * 1000), toTimestamp=int(time.time() * 1000)), timeout=20)  # a future 'to' is rejected: INCORRECT_BOUNDARIES
        net, closing = 0.0, 0
        for d in res.deal:
            md = d.moneyDigits or self._money_digits
            net += sc.money(d.commission, md)  # signed (negative = cost)
            if d.HasField("closePositionDetail"):
                c = d.closePositionDetail
                net += sc.money(c.grossProfit, c.moneyDigits or md) + sc.money(c.swap, c.moneyDigits or md)
                closing += 1
        return net, closing

    def position_result(self, position_id: int, since_ts: float) -> tuple[float, int]:
        """(net P&L in account currency incl. commission/swap, number of closing deals) for a position."""
        return self._call(self._position_result, position_id, since_ts)

    # ------------------------------------------------------------- trading
    def place_order(self, pair, direction, lots, sl, tp, *, system, signal_id, action="open", entry=None) -> Fill:
        if entry is not None:
            raise NotImplementedError("limit/stop entries are not used: scanner entries are market orders")
        self._guard_live()
        info = self.symbol_info(pair)
        if not (info.min_lots <= lots <= info.max_lots):
            raise BadVolume(f"{lots} lots outside [{info.min_lots}, {info.max_lots}]")
        coid = f"sdbot-{signal_id}-{action}"
        if not self.state.claim_msg_id(coid):
            existing = [p for p in self.positions() if p.signal_id == signal_id]
            if existing:
                p = existing[0]
                return Fill(p.position_id, p.entry, p.lots)
            raise AmbiguousResult(f"{coid} already sent but no matching position found; reconcile first")
        return self._call(self._place_market, info, direction, lots, sl, tp, system, signal_id, coid)

    @defer.inlineCallbacks
    def _place_market(self, info, direction, lots, sl, tp, system, signal_id, coid):
        q = self._quotes.get(info.symbol_id)
        if q is None:
            raise NoQuotes(f"no quote for {info.pair}")
        est = q.ask if direction == "long" else q.bid
        # Market orders can't carry absolute SL/TP, so attach protection at fill via
        # relative distances from the quote; absolute levels are amended right after the fill.
        rel_sl = sc.relative_to_int(est - sl, info.digits)
        rel_tp = sc.relative_to_int(tp - est, info.digits)
        msg = M.ProtoOANewOrderReq(
            ctidTraderAccountId=self._ctid, symbolId=info.symbol_id, orderType=MM.MARKET,
            tradeSide=MM.BUY if direction == "long" else MM.SELL,
            volume=sc.lots_to_volume(lots, self._lot_size[info.pair]),
            relativeStopLoss=rel_sl, relativeTakeProfit=rel_tp,
            label=LABEL, comment=make_comment(system, signal_id), clientOrderId=coid)
        try:
            yield self._send(msg, timeout=15, client_msg_id=coid)
        except (defer.TimeoutError, defer.CancelledError):
            raise AmbiguousResult(f"no response for {coid}; reconcile before retrying")
        fill = yield self._wait_fill(coid, timeout=20)
        # verify/set the exact absolute protection; a position must never stay unprotected
        yield self._ensure_protection(fill.position_id, info, sl, tp)
        return fill

    @defer.inlineCallbacks
    def _wait_fill(self, coid: str, timeout: float):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in self._exec_events.get(coid, []):
                et = ev.executionType
                if et == MM.ORDER_REJECTED:
                    raise _error_for(ev.errorCode or "REJECTED")
                if et in (MM.ORDER_FILLED, MM.ORDER_PARTIAL_FILL) and ev.HasField("deal"):
                    d = ev.deal
                    pair = self._by_id[d.symbolId]
                    return Fill(ev.position.positionId, d.executionPrice,
                                sc.volume_to_lots(d.filledVolume, self._lot_size[pair]),
                                abs(sc.money(d.commission, d.moneyDigits or self._money_digits)))
            yield task.deferLater(reactor, 0.1, lambda: None)
        raise AmbiguousResult(f"fill for {coid} not seen within {timeout}s; reconcile before retrying")

    @defer.inlineCallbacks
    def _ensure_protection(self, position_id, info, sl, tp):
        last = None
        for attempt in range(4):
            try:
                yield self._send(M.ProtoOAAmendPositionSLTPReq(
                    ctidTraderAccountId=self._ctid, positionId=position_id,
                    stopLoss=sc.round_price(sl, info.digits), takeProfit=sc.round_price(tp, info.digits)))
                return
            except TokenExpired:
                raise
            except BrokerError as e:
                last = e
                log.error("SL/TP amend failed for position %s (attempt %d): %s", position_id, attempt + 1, e)
                yield task.deferLater(reactor, 0.5, lambda: None)
        log.critical("POSITION %s HAS NO CONFIRMED STOP: closing it. last error: %s", position_id, last)
        yield self._close(position_id, None, info.pair)
        raise BadStops(f"could not attach SL/TP to position {position_id}; position closed ({last})")

    def modify_position(self, position_id: int, sl: float | None, tp: float | None) -> None:
        self._guard_live()
        pos = next((p for p in self.positions() if p.position_id == position_id), None)
        if pos is None:
            raise BrokerError(f"position {position_id} not found among sdbot positions")
        digits = self.symbol_info(pos.pair).digits
        kw = {}
        if sl is not None:
            kw["stopLoss"] = sc.round_price(sl, digits)
        if tp is not None:
            kw["takeProfit"] = sc.round_price(tp, digits)
        self._call(lambda: self._send(M.ProtoOAAmendPositionSLTPReq(
            ctidTraderAccountId=self._ctid, positionId=position_id, **kw)))

    @defer.inlineCallbacks
    def _close(self, position_id, lots, pair):
        res = yield self._reconcile()
        pos = next((p for p in res.position if p.positionId == position_id), None)
        if pos is None:
            return
        vol = pos.tradeData.volume if lots is None else sc.lots_to_volume(lots, self._lot_size[pair])
        yield self._send(M.ProtoOAClosePositionReq(ctidTraderAccountId=self._ctid, positionId=position_id,
                                                  volume=vol), timeout=15)

    def close_position(self, position_id: int, lots: float | None = None) -> None:
        self._guard_live()
        pos = next((p for p in self.positions() if p.position_id == position_id), None)
        if pos is None:
            raise BrokerError(f"position {position_id} not found among sdbot positions")
        self._call(self._close, position_id, lots, pos.pair)

    def cancel_order(self, order_id: int) -> None:
        self._guard_live()
        self._call(lambda: self._send(M.ProtoOACancelOrderReq(ctidTraderAccountId=self._ctid, orderId=order_id)))
