"""Executor settings from the project .env (EXEC_* keys). The scanner's
CT_ACCOUNT_ID is its data account; the executor trades EXEC_CT_ACCOUNT_ID."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
TOKEN_FILE = ROOT / ".ctrader_tokens_trading.json"
MODES = ("dry_run", "demo", "live")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    ct_client_id: str
    ct_client_secret: str
    ct_env: str
    ct_account_id: int | None
    mode: str
    allow_live: bool
    scanner_db: str
    systems: tuple[str, ...]
    risk_pct: float
    max_open_risk_pct: float
    max_daily_loss_pct: float
    max_signal_age_min: float
    min_rr_fraction: float      # skip if price drift leaves < this fraction of the planned RR
    max_spread_sl_frac: float   # spread must be <= this fraction of the SL distance
    telegram_token: str
    telegram_chat_id: str


def load_settings(env: dict | None = None) -> Settings:
    from live.systems import LIVE_SYSTEMS
    e = env if env is not None else {**dotenv_values(ENV_FILE), **{k: v for k, v in os.environ.items()
                                                                 if k.startswith(("CT_", "EXEC_", "TELEGRAM_"))}}
    g = lambda k, d="": (e.get(k) or d).strip()
    mode = g("EXEC_MODE", "dry_run")
    if mode not in MODES:
        raise ConfigError(f"EXEC_MODE={mode!r} not in {MODES}")
    acct = g("EXEC_CT_ACCOUNT_ID")
    if mode != "dry_run" and not acct:
        raise ConfigError("EXEC_CT_ACCOUNT_ID is required (the demo account to trade)")
    labels = [s.label for s in LIVE_SYSTEMS]
    systems = tuple(x.strip() for x in g("EXEC_SYSTEMS", ",".join(labels)).split(",") if x.strip())
    unknown = set(systems) - set(labels)
    if unknown:
        raise ConfigError(f"EXEC_SYSTEMS has unknown labels: {sorted(unknown)}")
    db = g("EXEC_SCANNER_DB", g("DB_URL", "sqlite:///supplydemand_bot.db").removeprefix("sqlite:///"))
    return Settings(
        ct_client_id=g("CT_CLIENT_ID"), ct_client_secret=g("CT_CLIENT_SECRET"), ct_env=g("CT_ENV", "demo"),
        ct_account_id=int(acct) if acct else None, mode=mode, allow_live=g("EXEC_ALLOW_LIVE") == "true",
        scanner_db=str(db if Path(db).is_absolute() else ROOT / db), systems=systems,
        risk_pct=float(g("EXEC_RISK_PCT", "0.25")), max_open_risk_pct=float(g("EXEC_MAX_OPEN_RISK_PCT", "8")),
        max_daily_loss_pct=float(g("EXEC_MAX_DAILY_LOSS_PCT", "3")),
        max_signal_age_min=float(g("EXEC_MAX_SIGNAL_AGE_MIN", "20")),
        min_rr_fraction=float(g("EXEC_MIN_RR_FRACTION", "0.7")),
        max_spread_sl_frac=float(g("EXEC_MAX_SPREAD_SL_FRAC", "0.25")),
        telegram_token=g("TELEGRAM_BOT_TOKEN"), telegram_chat_id=g("TELEGRAM_CHAT_ID"))
