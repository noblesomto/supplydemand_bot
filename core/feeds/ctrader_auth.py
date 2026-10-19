"""This project's own cTrader Open API tokens (read-only 'accounts' scope by
default). Adapted from /home/www/python/maker_ot/marketdata/ctrader_auth.py.

  python -m core.feeds.ctrader_auth            # print the authorization URL, catch/paste the redirect
  python -m core.feeds.ctrader_auth --code X   # exchange a code or full redirect URL

Needs CT_CLIENT_ID, CT_CLIENT_SECRET, CT_REDIRECT_URI in .env (CT_CLIENT_ID/
CT_CLIENT_SECRET may be the same Open API application as harmonic_bot's
executor -- that's just an app registration, not a trading credential).
Tokens are kept in .ctrader_tokens.json (chmod 600), separate from any other
bot, so refreshing them here can never invalidate another bot's refresh
token."""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / ".env"
TOKEN_FILE = ROOT / ".ctrader_tokens.json"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
AUTH_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
REFRESH_MARGIN_S = 24 * 3600


class TokenError(RuntimeError):
    pass


def env() -> dict[str, str]:
    e = dict(dotenv_values(ENV_FILE)) if ENV_FILE.exists() else {}
    e.update({k: v for k, v in os.environ.items() if k.startswith("CT_")})
    return {k: (v or "").strip() for k, v in e.items()}


def _save(tokens: dict, path: Path = TOKEN_FILE) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tokens, f)
    os.replace(tmp, path)


def _token_call(params: dict, http_get=requests.get, clock=time.time) -> dict:
    resp = http_get(TOKEN_URL, params=params, timeout=15)
    try:
        body = resp.json()
    except ValueError:
        raise TokenError(f"token endpoint returned non-JSON (HTTP {resp.status_code})")
    if body.get("errorCode") or "accessToken" not in body:
        raise TokenError(f"token request failed: {body.get('errorCode')} {body.get('description')}")
    return {"access_token": body["accessToken"], "refresh_token": body["refreshToken"],
            "expires_at": clock() + int(body["expiresIn"])}


def access_token(*, path: Path = TOKEN_FILE, http_get=requests.get, clock=time.time) -> str:
    """Current access token, refreshed (and persisted) when within a day of expiry."""
    if not path.exists():
        # Fallback: an access token pasted into .env. Used as-is and NEVER refreshed, because its
        # refresh token may belong to another bot (refreshing would invalidate that bot's copy).
        pasted = env().get("CT_ACCESS_TOKEN")
        if pasted:
            return pasted
        raise TokenError("no cTrader tokens yet: run `python -m core.feeds.ctrader_auth`")
    tok = json.loads(path.read_text())
    if clock() >= tok["expires_at"] - REFRESH_MARGIN_S:
        e = env()
        tok = _token_call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                           "client_id": e.get("CT_CLIENT_ID", ""), "client_secret": e.get("CT_CLIENT_SECRET", "")},
                          http_get, clock)
        _save(tok, path)
    return tok["access_token"]


def parse_code(text: str) -> str:
    text = text.strip()
    if "?" in text or text.startswith("http"):
        qs = parse_qs(urlparse(text).query)
        if "error" in qs:
            raise TokenError(f"authorization refused: {qs['error'][0]}")
        if "code" not in qs:
            raise TokenError("no 'code' parameter in the URL")
        return qs["code"][0]
    if not text:
        raise TokenError("empty authorization code")
    return text


def _wait_for_redirect(redirect_uri: str, timeout: float = 300) -> str:
    u = urlparse(redirect_uri)
    got, done = {}, threading.Event()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            got["url"] = f"http://{u.hostname}:{u.port}{self.path}"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this tab.")
            done.set()

        def log_message(self, *a):
            pass

    srv = HTTPServer((u.hostname, u.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        if not done.wait(timeout):
            raise TokenError(f"no redirect received within {timeout:.0f}s")
        return got["url"]
    finally:
        srv.shutdown()
        srv.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="core.feeds.ctrader_auth")
    ap.add_argument("--code", help="authorization code or full redirect URL")
    ap.add_argument("--scope", default="accounts", choices=["accounts", "trading"])
    a = ap.parse_args(argv)
    e = env()
    cid, secret, redirect = e.get("CT_CLIENT_ID"), e.get("CT_CLIENT_SECRET"), e.get("CT_REDIRECT_URI")
    if not (cid and secret and redirect):
        print("Set CT_CLIENT_ID, CT_CLIENT_SECRET and CT_REDIRECT_URI in .env first.", file=sys.stderr)
        return 2
    try:
        if a.code:
            code = parse_code(a.code)
        else:
            print("Open this URL, log in, pick the account, click Allow access:\n")
            print(f"{AUTH_URL}?{urlencode({'client_id': cid, 'redirect_uri': redirect, 'scope': a.scope})}\n")
            if urlparse(redirect).hostname in ("localhost", "127.0.0.1"):
                print(f"Waiting for the redirect on {redirect} ...")
                code = parse_code(_wait_for_redirect(redirect))
            else:
                code = parse_code(input("Paste the full redirected URL within one minute: "))
        tok = _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                           "client_id": cid, "client_secret": secret})
    except TokenError as err:
        print(f"FAILED: {err}", file=sys.stderr)
        return 1
    _save(tok)
    print(f"OK: tokens saved to {TOKEN_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
