"""Telegram alerts. Every message starts with the system label; PRIME
systems get a star. Unsent alerts are retried next cycle (alerted_at null)."""
import logging

import requests

log = logging.getLogger(__name__)


LINE = "━━━━━━━━━━━━━━━━━━━━"
_EXIT = {"target": ("🏆", "TARGET HIT", "Target"), "sl": ("❌", "STOPPED OUT", "Stop Loss"),
         "sl_trailed": ("🟡", "TRAILED STOP", "Trailed Stop"), "pa_exit": ("🟡", "PA EXIT", "Price-action exit")}


def _tag(system: str) -> str:
    return f"⭐ [{system}]" if system.startswith("PRIME-") else f"[{system}]"


def _px(v: float | None, pair: str) -> str:
    if v is None:
        return "-"
    digits = 2 if pair.startswith("XAU") else 3 if pair.endswith("JPY") else 5
    return f"{v:.{digits}f}"


def _ts(ts) -> str:
    return str(ts)[:16]


def format_new(sig) -> str:
    long = sig.direction.lower() == "long"
    emoji, action = ("🟢", "BUY") if long else ("🔴", "SELL")
    rr = f"{sig.rr:.1f}" if sig.rr else "-"
    skip = "" if sig.taken else f"⏭ Paper account: SKIPPED ({sig.skip_reason})\n"
    return (f"{emoji} {_tag(sig.system)} {action} — {sig.pair}\n"
            f"{LINE}\n"
            f"📦 Zone: {sig.zone_type.capitalize()} · Grade {sig.grade}\n"
            f"📍 Entry: {_px(sig.entry, sig.pair)}\n"
            f"🛑 Stop Loss: {_px(sig.sl, sig.pair)}\n"
            f"🎯 Target: {_px(sig.tp, sig.pair)}\n"
            f"📊 R:R: {rr}\n"
            f"{skip}"
            f"⏰ {_ts(sig.entry_timestamp)} UTC · paper\n"
            f"{LINE}")


def format_close(sig) -> str:
    emoji, title, exit_label = _EXIT.get(sig.exit_kind, ("⚪", "CLOSED", sig.exit_kind))
    if sig.exit_kind != "target" and sig.realized_r is not None and sig.realized_r > 0:
        emoji = "✅"
    return (f"{emoji} {_tag(sig.system)} {title} — {sig.pair}\n"
            f"{LINE}\n"
            f"↕️ {sig.direction.upper()} · entry {_px(sig.entry, sig.pair)}\n"
            f"🎯 Exit: {exit_label}\n"
            f"📊 Realized: {sig.realized_r:+.2f}R (net of spread)\n"
            f"⏰ {_ts(sig.exit_timestamp)} UTC · paper\n"
            f"{LINE}")


def format_vanished(sig) -> str:
    return (f"⚠️ {_tag(sig.system)} VANISHED — {sig.pair}\n"
            f"{sig.direction.upper()} @ {_ts(sig.entry_timestamp)} UTC dropped out of the replay (data revision)")


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
