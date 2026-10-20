"""python -m executor [--check-config]: demo-account executor for the S/D systems.
Trades only positions labelled 'sdbot'; refuses live accounts unless
EXEC_MODE=live and EXEC_ALLOW_LIVE=true. Open positions keep their
broker-side SL/TP while this process is down."""
import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from executor.config import TOKEN_FILE, ConfigError, load_settings
from executor.version import __version__

HERE = Path(__file__).resolve().parent
TICK_SECONDS = 10
MAX_CONSECUTIVE_TICK_FAILURES = 5


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s UTC %(levelname)s %(name)s: %(message)s")
    logging.Formatter.converter = time.gmtime
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), RotatingFileHandler(HERE / "executor.log", maxBytes=5_000_000, backupCount=5)):
        h.setFormatter(fmt)
        root.addHandler(h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="executor")
    ap.add_argument("--check-config", action="store_true")
    args = ap.parse_args(argv)
    try:
        s = load_settings()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    print(f"executor {__version__} mode={s.mode} env={s.ct_env} account={s.ct_account_id} risk={s.risk_pct}% "
          f"max_open_risk={s.max_open_risk_pct}% daily_stop={s.max_daily_loss_pct}% systems={','.join(s.systems)}")
    if args.check_config:
        return 0

    setup_logging()
    log = logging.getLogger("executor")
    from config import settings as scanner_settings
    from executor.app import Executor
    from executor.broker.ctrader import CTraderBroker
    from executor.notifier import Notifier
    from executor.signals import open_readonly
    from executor.state import State
    from executor.tokens import TokenManager

    notify = Notifier(s.telegram_token, s.telegram_chat_id)
    state = State()
    holder: dict = {}
    broker = CTraderBroker(s, TokenManager(TOKEN_FILE, s.ct_client_id, s.ct_client_secret), state,
                           list(scanner_settings.PAIRS),
                           on_reconnect=lambda: holder["app"].reconcile() if "app" in holder else None)
    broker.start()
    app = Executor(s, broker, state, open_readonly(s.scanner_db), notify)
    holder["app"] = app
    acct = broker.account_info()
    missing = [p for p in scanner_settings.PAIRS if p not in broker.known_pairs()]
    notify(f"🤖 S/D executor v{__version__} started ({s.mode}) on account {acct.ctid} "
           f"(live={acct.is_live}), equity {acct.currency} {acct.equity:,.2f}, {len(s.systems)} systems, "
           f"risk {s.risk_pct}%/trade" + (f", MISSING pairs: {','.join(missing)}" if missing else ""))
    app.reconcile()
    failures = 0
    while True:
        try:
            app.tick()
            failures = 0
        except Exception:  # noqa: BLE001
            failures += 1
            log.exception("tick failed (%d/%d)", failures, MAX_CONSECUTIVE_TICK_FAILURES)
            if failures >= MAX_CONSECUTIVE_TICK_FAILURES:
                notify("💥 S/D executor exiting after repeated failures; open positions keep their broker-side SL/TP")
                return 1
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
