"""Telegram notifications for the executor. Never raises."""
import logging

import requests

log = logging.getLogger("executor.notify")


class Notifier:
    def __init__(self, token: str, chat_id: str, http_post=requests.post):
        self.token, self.chat_id, self._post = token, chat_id, http_post

    def __call__(self, text: str) -> None:
        log.info("notify: %s", text)
        if not (self.token and self.chat_id):
            return
        try:
            self._post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                       json={"chat_id": self.chat_id, "text": text}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram send failed: %s", e)
