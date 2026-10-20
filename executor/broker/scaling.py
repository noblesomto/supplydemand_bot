"""Pure cTrader scaling/conversion helpers (units verified against
help.ctrader.com/open-api/messages: prices 1/100000, volume 0.01 unit,
money = value / 10**moneyDigits)."""
PRICE_SCALE = 100_000
VOLUME_SCALE = 100  # protocol volume is in 0.01 of a unit


def price_from_int(v: int) -> float:
    return v / PRICE_SCALE


def price_to_int(p: float) -> int:
    return int(round(p * PRICE_SCALE))


def relative_to_int(dist: float, digits: int) -> int:
    """Relative SL/TP distance in 1/100000, rounded to the symbol's price precision (JPY pairs: multiples of 100).
    The broker rejects finer values with 'Relative stop loss has invalid precision'. Never below one price step."""
    step = 10 ** max(5 - digits, 0)
    return max(int(round(abs(dist) * PRICE_SCALE / step)), 1) * step


def round_price(p: float, digits: int) -> float:
    return round(p, digits)


def money(v: int, money_digits: int) -> float:
    return v / (10 ** money_digits)


def lots_to_volume(lots: float, lot_size: int) -> int:
    """lot_size is the symbol's lotSize field (protocol units, i.e. units*100)."""
    return int(round(lots * lot_size))


def volume_to_lots(volume: int, lot_size: int) -> float:
    return volume / lot_size


def contract_size(lot_size: int) -> float:
    return lot_size / VOLUME_SCALE


def split_pair(pair: str) -> tuple[str, str]:
    base, quote = pair.split("/")
    return base, quote


def broker_name(pair: str) -> str:
    return pair.replace("/", "")


def quote_to_account_rate(quote_ccy: str, account_ccy: str, mids: dict[str, float]) -> float:
    """Value of 1 unit of `quote_ccy` in `account_ccy`, from mid prices keyed by
    broker symbol name (e.g. {"USDJPY": 150.0}). Raises KeyError if no route."""
    if quote_ccy == account_ccy:
        return 1.0
    direct = f"{quote_ccy}{account_ccy}"     # price = account per quote
    inverse = f"{account_ccy}{quote_ccy}"    # price = quote per account
    if direct in mids and mids[direct] > 0:
        return mids[direct]
    if inverse in mids and mids[inverse] > 0:
        return 1.0 / mids[inverse]
    raise KeyError(f"no conversion route {quote_ccy}->{account_ccy}")
