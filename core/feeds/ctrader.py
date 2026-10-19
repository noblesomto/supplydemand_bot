"""Read-only cTrader Open API history client: M1 bid trendbars. Adapted from
/home/www/python/maker_ot/marketdata/ctrader.py -- copied, not imported (see
README "Modeled on" note); uses this project's own tokens, never maker_ot's.

Twisted runs in a background thread; public methods are synchronous
(blockingCallFromThread), the same pattern harmonic_bot's executor uses.
Prices are integers in 1/100000 of a unit; a trendbar carries `low` plus
deltas to open/high/close (help.ctrader.com/open-api)."""
import threading
from datetime import datetime, timezone

import pandas as pd

BAR_COLUMNS = ["bid_open", "bid_high", "bid_low", "bid_close", "volume"]
DEMO_HOST, LIVE_HOST, PORT = "demo.ctraderapi.com", "live.ctraderapi.com", 5035
PRICE_SCALE = 100_000


class CTraderError(RuntimeError):
    pass


def decode_trendbars(bars) -> pd.DataFrame:
    """ProtoOATrendbar list -> bid OHLC frame indexed by UTC bar open time."""
    if not bars:
        return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="timestamp"), dtype=float)
    low = pd.Series([b.low for b in bars], dtype="int64")
    df = pd.DataFrame({
        "bid_open": (low + [b.deltaOpen for b in bars]) / PRICE_SCALE,
        "bid_high": (low + [b.deltaHigh for b in bars]) / PRICE_SCALE,
        "bid_low": low / PRICE_SCALE,
        "bid_close": (low + [b.deltaClose for b in bars]) / PRICE_SCALE,
        "volume": [float(b.volume) for b in bars],
    })
    df.index = pd.DatetimeIndex(pd.to_datetime([b.utcTimestampInMinutes * 60 for b in bars], unit="s", utc=True),
                                name="timestamp")
    df = df.sort_index()
    return df[~df.index.duplicated(keep="last")]


_reactor_thread: threading.Thread | None = None


def _ensure_reactor() -> None:
    global _reactor_thread
    from twisted.internet import reactor
    if _reactor_thread is None:
        _reactor_thread = threading.Thread(target=lambda: reactor.run(installSignalHandlers=False),
                                           name="twisted-reactor", daemon=True)
        _reactor_thread.start()


class HistoryClient:
    def __init__(self, client_id: str, client_secret: str, access_token: str, *,
                 env: str = "demo", account_id: int | None = None):
        self.cid, self.secret, self.token = client_id, client_secret, access_token
        self.host = LIVE_HOST if env == "live" else DEMO_HOST
        self.account_id = account_id
        self.symbols: dict[str, int] = {}
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._client = None

    # -------------------------------------------------------------- plumbing
    def _send(self, msg, timeout: float = 30.0):
        from ctrader_open_api import Protobuf
        d = self._client.send(msg, responseTimeoutInSeconds=timeout)

        def decode(proto):
            payload = Protobuf.extract(proto)
            if type(payload).__name__ == "ProtoOAErrorRes":
                raise CTraderError(f"{payload.errorCode}: {payload.description}")
            return payload
        return d.addCallback(decode)

    def _call(self, fn, *a):
        from twisted.internet import reactor, threads
        return threads.blockingCallFromThread(reactor, fn, *a)

    def start(self, timeout: float = 60.0) -> "HistoryClient":
        from ctrader_open_api import Client, TcpProtocol
        from twisted.internet import reactor
        _ensure_reactor()
        self._client = Client(self.host, PORT, TcpProtocol, numberOfMessagesToSendPerSecond=5)
        self._client.setConnectedCallback(lambda c: self._bootstrap().addCallbacks(self._ok, self._fail))
        reactor.callFromThread(self._client.startService)
        if not self._ready.wait(timeout):
            raise CTraderError(f"cTrader not ready within {timeout}s")
        if self._error:
            raise self._error
        return self

    def stop(self) -> None:
        from twisted.internet import reactor
        if self._client:
            reactor.callFromThread(self._client.stopService)

    def _ok(self, _):
        self._ready.set()

    def _fail(self, failure):
        self._error = failure.value if isinstance(failure.value, Exception) else CTraderError(str(failure))
        self._ready.set()

    def _bootstrap(self):
        from ctrader_open_api.messages import OpenApiMessages_pb2 as M
        from twisted.internet import defer

        @defer.inlineCallbacks
        def run():
            yield self._send(M.ProtoOAApplicationAuthReq(clientId=self.cid, clientSecret=self.secret))
            res = yield self._send(M.ProtoOAGetAccountListByAccessTokenReq(accessToken=self.token))
            accts = [a for a in res.ctidTraderAccount
                     if self.account_id is None or a.ctidTraderAccountId == self.account_id]
            want_live = self.host == LIVE_HOST
            accts = [a for a in accts if a.isLive == want_live]
            if not accts:
                raise CTraderError("no matching cTrader account for this token/environment; set CT_ACCOUNT_ID")
            self.account_id = accts[0].ctidTraderAccountId
            yield self._send(M.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.token))
            sl = yield self._send(M.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id))
            self.symbols = {s.symbolName: s.symbolId for s in sl.symbol}
        return run()

    # ------------------------------------------------------------------ API
    def trendbars_m1(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """M1 bid bars with open time in [start, end)."""
        from ctrader_open_api.messages import OpenApiMessages_pb2 as M
        from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
        if symbol not in self.symbols:
            raise CTraderError(f"symbol {symbol} not offered by this broker")
        msg = M.ProtoOAGetTrendbarsReq(
            ctidTraderAccountId=self.account_id, symbolId=self.symbols[symbol], period=MM.M1,
            fromTimestamp=_ms(start), toTimestamp=_ms(end))
        res = self._call(lambda: self._send(msg))
        df = decode_trendbars(list(res.trendbar))
        return df[(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))]


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def from_env() -> HistoryClient:
    from core.feeds.ctrader_auth import access_token, env
    e = env()
    acct = e.get("CT_ACCOUNT_ID")
    return HistoryClient(e.get("CT_CLIENT_ID", ""), e.get("CT_CLIENT_SECRET", ""), access_token(),
                         env=e.get("CT_ENV", "demo") or "demo", account_id=int(acct) if acct else None)
