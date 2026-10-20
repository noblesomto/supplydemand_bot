"""Trading-scope token for the executor, in its own file
(.ctrader_tokens_trading.json, written by
`python -m core.feeds.ctrader_auth --scope trading --token-file ...`), so the
executor and the scanner never refresh the same token."""
import threading

from core.feeds.ctrader_auth import REFRESH_MARGIN_S, TokenError, _save, _token_call, access_token
import json
import time


class TokenManager:
    def __init__(self, path, client_id: str, client_secret: str):
        self.path, self.cid, self.secret = path, client_id, client_secret
        self._lock = threading.RLock()
        if not path.exists():
            raise TokenError(f"{path.name} missing: run ctrader_auth --scope trading --token-file {path.name}")

    @property
    def access_token(self) -> str:
        with self._lock:
            return access_token(path=self.path)  # refreshes + persists when within a day of expiry

    def refresh(self) -> str:
        with self._lock:
            tok = json.loads(self.path.read_text())
            tok = _token_call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                               "client_id": self.cid, "client_secret": self.secret})
            _save(tok, self.path)
            return tok["access_token"]
