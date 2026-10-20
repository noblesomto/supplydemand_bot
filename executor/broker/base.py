"""Broker interface and plain dataclasses. No cTrader imports here."""
from dataclasses import dataclass
from typing import Callable, Protocol


class BrokerError(Exception):
    pass


class NotEnoughMoney(BrokerError): ...
class MarketClosed(BrokerError): ...
class BadVolume(BrokerError): ...
class BadStops(BrokerError): ...
class NoQuotes(BrokerError): ...
class TokenExpired(BrokerError): ...
class AmbiguousResult(BrokerError):
    """Outcome unknown (timeout/disconnect): reconcile with the broker before retrying."""
class LiveAccountRefused(BrokerError): ...


@dataclass(frozen=True)
class AccountInfo:
    ctid: int
    is_live: bool
    currency: str
    balance: float
    equity: float
    used_margin: float
    free_margin: float
    leverage: float


@dataclass(frozen=True)
class SymbolInfo:
    pair: str            # scanner format, e.g. EUR/USD
    broker_name: str     # e.g. EURUSD
    symbol_id: int
    digits: int
    pip_position: int
    contract_size: float # units per 1.0 lot
    min_lots: float
    max_lots: float
    step_lots: float
    base_ccy: str
    quote_ccy: str
    min_sl_distance: float = 0.0  # price units, 0 = unknown


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    ts: float


@dataclass(frozen=True)
class BrokerPosition:
    position_id: int
    pair: str
    direction: str       # long | short
    lots: float
    entry: float
    sl: float | None
    tp: float | None
    label: str
    comment: str
    open_ts: float
    swap: float = 0.0
    commission: float = 0.0

    @property
    def signal_id(self) -> int | None:
        return parse_comment(self.comment)[1]

    @property
    def system(self) -> str | None:
        return parse_comment(self.comment)[0]


@dataclass(frozen=True)
class BrokerOrder:
    order_id: int
    pair: str
    direction: str
    lots: float
    order_type: str
    limit_price: float | None
    stop_price: float | None
    sl: float | None
    tp: float | None
    label: str
    comment: str
    position_id: int | None = None
    client_order_id: str = ""


@dataclass(frozen=True)
class Fill:
    position_id: int
    price: float
    lots: float
    commission: float = 0.0


def make_comment(system: str, signal_id: int) -> str:
    return f"sdbot:{system}:{signal_id}"


def parse_comment(comment: str) -> tuple[str | None, int | None]:
    parts = (comment or "").split(":")
    if len(parts) == 3 and parts[0] == "sdbot" and parts[2].isdigit():
        return parts[1], int(parts[2])
    return None, None


class Broker(Protocol):
    def account_info(self) -> AccountInfo: ...
    def symbol_info(self, pair: str) -> SymbolInfo: ...
    def quote(self, pair: str) -> Quote: ...
    def subscribe_spots(self, pairs: list[str], callback: Callable[[str, Quote], None] | None = None) -> None: ...
    def extremes_since(self, pair: str, since_ts: float) -> tuple[float, float] | None: ...
    def typical_spread(self, pair: str) -> float | None: ...   # median recent spread (price units); None until enough samples
    def quote_to_account(self, pair: str) -> float: ...
    def expected_margin_per_lot(self, pair: str) -> float: ...
    def positions(self) -> list[BrokerPosition]: ...
    def pending_orders(self) -> list[BrokerOrder]: ...
    def place_order(self, pair: str, direction: str, lots: float, sl: float, tp: float, *, system: str,
                    signal_id: int, action: str = "open", entry: float | None = None) -> Fill: ...
    def modify_position(self, position_id: int, sl: float | None, tp: float | None) -> None: ...
    def close_position(self, position_id: int, lots: float | None = None) -> None: ...
    def cancel_order(self, order_id: int) -> None: ...
    def position_result(self, position_id: int, since_ts: float) -> tuple[float, int]: ...
