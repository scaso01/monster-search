"""Sign back in to Perplexity with Google, unattended, in a dedicated Chrome profile.

Perplexity sometimes revokes a session server-side long before its cookie expires, and
renewing cannot recover from that. Google sessions last for months, so a browser profile
that stays signed in to Google can redo "Continue with Google" whenever that happens.

One-time setup (a visible window opens; sign in to Google there):
    python -m monster_search.clients.perplexity_relogin --setup
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
import traceback
from pathlib import Path

PROFILE_DIR = Path.home() / ".cache" / "monster-search" / "perplexity-browser"
FAILURE_SHOT = PROFILE_DIR.parent / "perplexity-relogin-failure.png"
LOG = PROFILE_DIR.parent / "perplexity-relogin.log"
RELOGIN_TASK = os.environ.get("MONSTER_PERPLEXITY_RELOGIN_TASK", "Perplexity Relogin")
_SITE = "https://www.perplexity.ai"
_SESSION_COOKIE = "__Secure-next-auth.session-token"
# Off-screen rather than headless: Google refuses sign-in from headless browsers.
_HIDDEN = ["--window-position=-32000,-32000", "--window-size=1200,900"]


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
    # Perplexity keeps more than one session cookie; any survivor hides the sign-in button.
    ctx.clear_cookies(domain=re.compile(r"perplexity\.ai$"))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(_SITE + "/auth/signin", wait_until="domcontentloaded")
    try:
        page.get_by_role("button", name="Continue with Google").click(timeout=30000)
    except Exception as exc:
        page.screenshot(path=str(FAILURE_SHOT))
        raise RuntimeError(f"no 'Continue with Google' button at {page.url} (title {page.title()!r}); "
                           f"screenshot {FAILURE_SHOT}") from exc
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        fresh = _session(ctx)
        if fresh and (not stale or fresh[0] != stale[0]):
            return fresh
        # Google may answer in the same tab or in a pop-up.
        for google in [pg for pg in ctx.pages if "accounts.google.com" in pg.url]:
            if google.locator('input[type="password"]:visible, input[type="email"]:visible').count():
                raise GoogleSignInNeeded(
                    "the Perplexity re-login browser is signed out of Google; run "
                    "`python -m monster_search.clients.perplexity_relogin --setup` once")
            account = google.locator("[data-identifier]").first
            if account.count():
                account.click()
                continue
            for label in ("Continue", "Allow"):
                button = google.get_by_role("button", name=label)
                if button.count():
                    button.first.click()
                    break
    urls = [pg.url for pg in ctx.pages]
    raise TimeoutError(f"Perplexity Google sign-in did not finish within {timeout_s}s (pages {urls})")


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


def relogin_anywhere(timeout_s: int = 300) -> tuple[str, float]:
    """Sign in again, from a background task too.

    Chrome's cookie store is locked to the signed-in Windows desktop, so a task that runs
    without one (S4U) cannot use the Google login. On Windows this hands the job to the
    RELOGIN_TASK scheduled task, which runs on the desktop, and waits for its result.
    """
    from monster_search.clients.perplexity_client import SESSION_CACHE, _read_cache

    if os.name != "nt":
        return relogin()
    before = SESSION_CACHE.stat().st_mtime if SESSION_CACHE.exists() else 0.0
    # Never open the profile from here: a desktop-less Chrome cannot unlock its key and
    # replaces it, which silently deletes the saved Google login.
    started = subprocess.run(["schtasks", "/Run", "/TN", RELOGIN_TASK], capture_output=True, text=True)
    if started.returncode != 0:
        raise RuntimeError(f"cannot start the {RELOGIN_TASK!r} task: {started.stderr.strip() or started.stdout.strip()}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(3)
        if SESSION_CACHE.exists() and SESSION_CACHE.stat().st_mtime > before:
            cached = _read_cache()
            if cached:
                return cached
    raise TimeoutError(f"the {RELOGIN_TASK!r} task did not sign in within {timeout_s}s; see {LOG}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign back in to Perplexity with Google.")
    parser.add_argument("--setup", action="store_true", help="one-time visible Google sign-in")
    args = parser.parse_args()
    from monster_search.clients.perplexity_client import PerplexityClient, save_session

    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        token, expires = setup() if args.setup else relogin()
        save_session(token, expires)
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
