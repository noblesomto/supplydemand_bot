"""Telegram alerts. Every message starts with the system label; PRIME
systems get a star. Unsent alerts are retried next cycle (alerted_at null)."""
import logging

import requests

log = logging.getLogger(__name__)


def _tag(system: str) -> str:
    return f"⭐ [{system}]" if system.startswith("PRIME-") else f"[{system}]"


def _px(v: float | None, pair: str) -> str:
    if v is None:
        return "-"
    digits = 2 if pair.startswith("XAU") else 3 if pair.endswith("JPY") else 5
    return f"{v:.{digits}f}"


def format_new(sig) -> str:
    head = f"{_tag(sig.system)} {sig.pair} {sig.direction.upper()} ({sig.zone_type}, grade {sig.grade})"
    if not sig.taken:
        head += f" SKIPPED ({sig.skip_reason})"
    rr = f"  ({sig.rr:.1f}R)" if sig.rr else ""
    return f"{head}\nEntry {_px(sig.entry, sig.pair)}  SL {_px(sig.sl, sig.pair)}  TP {_px(sig.tp, sig.pair)}{rr}\nBar {sig.entry_timestamp} UTC · paper"


def format_close(sig) -> str:
    return f"{_tag(sig.system)} {sig.pair} {sig.direction.upper()} closed: {sig.exit_kind} {sig.realized_r:+.2f}R (net of spread)"


def format_vanished(sig) -> str:
    return f"{_tag(sig.system)} {sig.pair} {sig.direction.upper()} @ {sig.entry_timestamp} vanished from replay (data revision)"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            log.info("telegram not configured: %s", text.splitlines()[0])
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text}, timeout=15)
            return r.ok
        except requests.RequestException as e:
            log.warning("telegram send failed: %s", e)
            return False
