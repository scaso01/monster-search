"""Sign back in to Perplexity with Google, unattended, in a dedicated Chrome profile.

Perplexity sometimes revokes a session server-side long before its cookie expires, and
renewing cannot recover from that. Google sessions last for months, so a browser profile
that stays signed in to Google can redo "Continue with Google" whenever that happens.

One-time setup (a visible window opens; sign in to Google there):
    python -m monster_search.clients.perplexity_relogin --setup
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

PROFILE_DIR = Path.home() / ".cache" / "monster-search" / "perplexity-browser"
_SITE = "https://www.perplexity.ai"
_SESSION_COOKIE = "__Secure-next-auth.session-token"
# Off-screen rather than headless: Google refuses sign-in from headless browsers.
_HIDDEN = ["--window-position=-32000,-32000", "--window-size=1200,900"]

# In-page POST to next-auth's Google provider, so this does not depend on Perplexity's UI.
_START_GOOGLE = """async () => {
  const {csrfToken} = await (await fetch('/api/auth/csrf')).json();
  const f = document.createElement('form');
  f.method = 'POST'; f.action = '/api/auth/signin/google';
  for (const [k, v] of Object.entries({csrfToken, callbackUrl: location.origin + '/', json: 'true'})) {
    const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i);
  }
  document.body.appendChild(f); f.submit();
}"""


class GoogleSignInNeeded(RuntimeError):
    """The dedicated browser is no longer signed in to Google; run --setup once."""


def _launch(p, hidden: bool):
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="chrome", headless=False, no_viewport=True,
        args=_HIDDEN if hidden else [],
    )


def _session(ctx) -> tuple[str, float] | None:
    for c in ctx.cookies(_SITE):
        if c["name"] == _SESSION_COOKIE and c["value"] and c.get("expires", 0) > time.time():
            return c["value"], float(c["expires"])
    return None


def _google_signed_in(ctx) -> bool:
    return any(c["name"] == "SID" for c in ctx.cookies("https://accounts.google.com"))


def _sign_in_to_perplexity(ctx, timeout_s: int) -> tuple[str, float]:
    """Run Continue-with-Google and return the fresh Perplexity session cookie."""
    stale = _session(ctx)
    ctx.clear_cookies(name=_SESSION_COOKIE)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(_SITE + "/", wait_until="domcontentloaded")
    page.evaluate(_START_GOOGLE)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        fresh = _session(ctx)
        if fresh and (not stale or fresh[0] != stale[0]) and "perplexity.ai" in page.url:
            return fresh
        if "accounts.google.com" in page.url:
            if page.locator('input[type="password"], input[type="email"]:visible').count():
                raise GoogleSignInNeeded(
                    "the Perplexity re-login browser is signed out of Google; run "
                    "`python -m monster_search.clients.perplexity_relogin --setup` once")
            account = page.locator("[data-identifier]").first
            if account.count():
                account.click()
                continue
            for label in ("Continue", "Allow"):
                button = page.get_by_role("button", name=label)
                if button.count():
                    button.first.click()
                    break
    raise TimeoutError(f"Perplexity Google sign-in did not finish within {timeout_s}s (last page {page.url})")


def relogin(timeout_s: int = 120) -> tuple[str, float]:
    """Sign in to Perplexity again without a human; returns (token, expiry epoch)."""
    from patchright.sync_api import sync_playwright

    if not PROFILE_DIR.exists():
        raise GoogleSignInNeeded(
            "no Perplexity re-login browser yet; run "
            "`python -m monster_search.clients.perplexity_relogin --setup` once")
    with sync_playwright() as p:
        ctx = _launch(p, hidden=True)
        try:
            if not _google_signed_in(ctx):
                raise GoogleSignInNeeded(
                    "the Perplexity re-login browser is signed out of Google; run "
                    "`python -m monster_search.clients.perplexity_relogin --setup` once")
            return _sign_in_to_perplexity(ctx, timeout_s)
        finally:
            ctx.close()


def setup(timeout_s: int = 900) -> tuple[str, float]:
    """Open a visible window, wait for a Google sign-in, then sign in to Perplexity with it."""
    from patchright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = _launch(p, hidden=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not _google_signed_in(ctx):
                page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
                print("Sign in to Google in the window that just opened (the account you use for Perplexity).")
                deadline = time.time() + timeout_s
                while not _google_signed_in(ctx):
                    if time.time() > deadline:
                        raise TimeoutError("no Google sign-in within the time allowed")
                    page.wait_for_timeout(2000)
            return _sign_in_to_perplexity(ctx, timeout_s=180)
        finally:
            ctx.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign back in to Perplexity with Google.")
    parser.add_argument("--setup", action="store_true", help="one-time visible Google sign-in")
    args = parser.parse_args()
    from monster_search.clients.perplexity_client import PerplexityClient, save_session

    token, expires = setup() if args.setup else relogin()
    save_session(token, expires)
    renewed = PerplexityClient().renew()
    print(f"Perplexity signed in; session valid until {time.strftime('%Y-%m-%d', time.localtime(renewed))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
