#!/usr/bin/env python3
"""Health watchdog for the S/D paper scanner, run by a systemd timer.
Catches a hung scheduler (process up, no scans). Standalone: imports none
of the app's modules. Alerts once per outage, then once on recovery."""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent
load_dotenv(APP_DIR / ".env")
BOT_TOKEN, CHAT_ID = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
HEALTH_URL = f"http://localhost:{os.getenv('PORT', '8003')}/health"
STATE_FILE = APP_DIR / ".watchdog_state"
STALE_THRESHOLD_MIN = 60  # 4 missed 15-min cycles


def send(text: str) -> None:
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"watchdog: telegram failed: {e}", file=sys.stderr)


def main() -> None:
    prev = STATE_FILE.read_text().strip() if STATE_FILE.exists() else "OK"
    host = os.uname().nodename
    try:
        try:
            r = requests.get(HEALTH_URL, timeout=10)
        except requests.ConnectionError:
            time.sleep(30)  # service may be (re)starting: uvicorn binds a few seconds after launch
            r = requests.get(HEALTH_URL, timeout=10)
        r.raise_for_status()
        last = r.json().get("last_scan_at")
        if not last:  # fresh process, first cycle not finished yet
            return
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        stale = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        if stale > STALE_THRESHOLD_MIN:
            if prev != "DOWN":
                send(f"[S/D scanner] ALERT\nNo scan in {stale:.0f} min (last {last}) on {host}.\n"
                     f"Check: systemctl status supplydemand_bot / bot.log")
                STATE_FILE.write_text("DOWN")
        else:
            if prev == "DOWN":
                send(f"[S/D scanner] RECOVERED on {host} (last scan {last}).")
            STATE_FILE.write_text("OK")
    except Exception as e:  # noqa: BLE001
        if prev != "DOWN":
            send(f"[S/D scanner] ALERT\n/health unreachable on {host}: {type(e).__name__}: {e}")
            STATE_FILE.write_text("DOWN")


if __name__ == "__main__":
    main()
