"""Sign back in to Perplexity by email, unattended.

Perplexity sometimes revokes a session server-side long before its cookie expires, and
renewing cannot recover from that. This requests Perplexity's sign-in email from a fresh
browser, reads the sign-in link from Gmail, and opens it in that same browser.

Gmail is read with a read-only token minted from notebooklm-py's stored Google master
token (`notebooklm login --master-token`), so no extra credential is stored. Google sign-in
is not used: Google rejects OAuth sign-ins from automated browsers.

    python -m monster_search.clients.perplexity_relogin
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import subprocess
import time
import traceback
from pathlib import Path

import httpx

_SITE = "https://www.perplexity.ai"
_SESSION_COOKIE = "__Secure-next-auth.session-token"
_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_SIGNIN_QUERY = 'from:team@mail.perplexity.ai subject:"Sign in to Perplexity" newer_than:1d'
# Off-screen rather than headless: Perplexity's bot check blocks headless Chrome.
_HIDDEN = ["--window-position=-32000,-32000", "--window-size=1200,900"]
STATE_DIR = Path.home() / ".cache" / "monster-search"
FAILURE_SHOT = STATE_DIR / "perplexity-relogin-failure.png"
LOG = STATE_DIR / "perplexity-relogin.log"
# Python of a notebooklm-py install that holds a Google master token.
MINT_PYTHON = Path(os.environ.get(
    "MONSTER_GOOGLE_MINT_PYTHON",
    Path.home() / ".claude" / "skills" / "notebooklm" / ".venv" / "Scripts" / "python.exe"))

# Gmail's own Android client id; the token is read-only (gmail.readonly).
_TOKEN_SCRIPT = """
import gpsoauth, json
from notebooklm.paths import get_master_token_path
from notebooklm.auth import read_master_token
rec = read_master_token(get_master_token_path())
if rec is None:
    raise SystemExit("no notebooklm master token")
r = gpsoauth.perform_oauth(rec["email"], rec["master_token"], rec["android_id"],
    service="oauth2:https://www.googleapis.com/auth/gmail.readonly", app="com.google.android.gm",
    client_sig="38918a453d07199354f8b19af05ec6562ced5788")
if "Auth" not in r:
    raise SystemExit(f"Google refused a Gmail token: {r.get('Error', 'unknown error')}")
print(json.dumps({"email": rec["email"], "token": r["Auth"]}))
"""


def _gmail_access() -> tuple[str, str]:
    """(email, read-only Gmail bearer token) minted from the stored master token."""
    if not MINT_PYTHON.exists():
        raise RuntimeError(f"no notebooklm-py install at {MINT_PYTHON} (set MONSTER_GOOGLE_MINT_PYTHON)")
    done = subprocess.run([str(MINT_PYTHON), "-I", "-c", _TOKEN_SCRIPT],
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"could not get Gmail access from the master token: {done.stderr.strip()[-300:]}")
    data = json.loads(done.stdout)
    return data["email"], data["token"]


def _message_body(payload: dict) -> str:
    parts = [payload] + payload.get("parts", [])
    return "".join(base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace")
                   for p in parts if p.get("body", {}).get("data"))


def _signin_link(token: str, sent_after: float) -> str | None:
    """The sign-in link from the newest Perplexity sign-in email sent after *sent_after*."""
    headers = {"Authorization": f"Bearer {token}"}
    listing = httpx.get(_GMAIL, params={"q": _SIGNIN_QUERY, "maxResults": 3}, headers=headers, timeout=30)
    listing.raise_for_status()
    for item in listing.json().get("messages", []):
        msg = httpx.get(f"{_GMAIL}/{item['id']}", params={"format": "full"}, headers=headers, timeout=30)
        msg.raise_for_status()
        data = msg.json()
        if int(data["internalDate"]) / 1000 < sent_after:
            continue
        for url in re.findall(r'href="([^"]+)"', _message_body(data["payload"])):
            if "/api/auth/callback/email" in url:
                return html.unescape(url)
    return None


def relogin(timeout_s: int = 150) -> tuple[str, float]:
    """Sign in to Perplexity again without a human; returns (token, expiry epoch)."""
    from patchright.sync_api import sync_playwright

    email, gmail_token = _gmail_access()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False, args=_HIDDEN)
        try:
            page = browser.new_context().new_page()
            page.goto(_SITE + "/auth/signin", wait_until="domcontentloaded")
            try:
                page.locator('input[type="email"]').first.fill(email, timeout=30000)
                sent_after = time.time() - 5
                page.get_by_role("button", name="Continue with email").click(timeout=30000)
            except Exception as exc:
                page.screenshot(path=str(FAILURE_SHOT))
                raise RuntimeError(f"Perplexity sign-in page changed at {page.url}; screenshot {FAILURE_SHOT}") from exc
            deadline = time.time() + timeout_s
            link = None
            while link is None and time.time() < deadline:
                time.sleep(3)
                link = _signin_link(gmail_token, sent_after)
            if link is None:
                raise TimeoutError(f"no Perplexity sign-in email arrived within {timeout_s}s")
            page.goto(link, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            for c in page.context.cookies(_SITE):
                if c.get("name") == _SESSION_COOKIE and c.get("value"):
                    return c["value"], float(c.get("expires", 0))
            page.screenshot(path=str(FAILURE_SHOT))
            raise RuntimeError(f"the sign-in link gave no session (ended at {page.url}); screenshot {FAILURE_SHOT}")
        finally:
            browser.close()


def main() -> int:
    from monster_search.clients.perplexity_client import PerplexityClient, save_session

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        save_session(*relogin())
        renewed = PerplexityClient()._renew_once()
    except Exception:
        with LOG.open("a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} FAILED\n{traceback.format_exc()}\n")
        raise
    message = f"Perplexity signed in; session valid until {time.strftime('%Y-%m-%d', time.localtime(renewed))}"
    with LOG.open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
