"""Fetch + render module — Playwright headless Chromium.

Launches headless Chromium, navigates to a URL, waits for the network to go
idle so JS-rendered content is present, and yields the *live* page handle to
the caller (the extraction step runs `page.evaluate(...)` against this same
handle). The browser is always torn down afterwards, even on error.

Failures (timeout, blocked, empty) raise a single typed `RenderError` that the
endpoint maps to the frontend's error state.

NO bot-evasion (spec §6): default user-agent, no `navigator.webdriver` override,
no stealth. If a site blocks us, that surfaces as a `RenderError`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# How long to wait for the navigation (DOMContentLoaded) to complete.
NAV_TIMEOUT_MS = 30_000
# How long to additionally wait for the network to go quiet. Analytics/polling
# keep many real sites permanently "busy", so a timeout here is NOT fatal — the
# DOM has rendered and we proceed with what loaded. Kept short (the main content
# is present right after DOMContentLoaded) so a 3-page crawl stays demo-fast.
NETWORK_IDLE_TIMEOUT_MS = 7_000


class RenderError(Exception):
    """The page genuinely could not be read (blocked, timed out, or empty).

    Carries an optional HTTP status for context. The endpoint turns this into
    the section-3 error response; the frontend shows its clean error state.
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def _normalize_url(url: str) -> str:
    """Add a scheme if the operator pasted a bare domain (e.g. `example.com`).

    NOT validation — there is no SSRF/allowlist check by design (spec §6); this
    only makes Playwright's `goto` happy with bare-domain input.
    """
    url = (url or "").strip()
    if not url:
        raise RenderError("no URL was provided")
    if not urlparse(url).scheme:
        url = "https://" + url
    return url


@asynccontextmanager
async def browser_session() -> AsyncIterator[Browser]:
    """Launch one headless Chromium and yield it, closing it on exit.

    Lets a caller render several pages (e.g. a shallow crawl) in a single
    browser instead of paying the launch cost per page.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            await browser.close()


async def render_in(browser: Browser, url: str) -> Page:
    """Render `url` in a new page of an existing browser and return it live.

    Raises `RenderError` if the page can't be loaded or has no readable content
    (the new page is closed before raising). On success the caller owns the
    returned page and must close it.
    """
    target = _normalize_url(url)
    page = await browser.new_page()
    try:
        # 1) Load the document. A timeout or connection failure here is a
        #    genuine "couldn't read it" → RenderError.
        try:
            response = await page.goto(
                target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
            )
        except PlaywrightTimeoutError as exc:
            raise RenderError("the page took too long to load") from exc
        except PlaywrightError as exc:
            raise RenderError("could not reach the page") from exc

        # 2) Best-effort wait for JS-rendered content to settle. A site that
        #    never goes idle (constant analytics) is fine — keep going.
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            pass

        # 3) A hard HTTP error means the site blocked us or the page is gone.
        if response is not None and response.status >= 400:
            raise RenderError(
                f"the site returned HTTP {response.status}", status=response.status
            )

        # 4) Nothing readable rendered → treat as unreadable (e.g. a blank
        #    block/challenge page).
        body_text = (await page.evaluate(
            "() => (document.body && document.body.innerText || '').trim()"
        )) or ""
        if not body_text:
            raise RenderError("the page returned no readable content")

        return page
    except BaseException:
        await page.close()
        raise


@asynccontextmanager
async def render_page(url: str) -> AsyncIterator[Page]:
    """Render `url` in its own browser and yield the live Playwright page.

    Usage:
        async with render_page(url) as page:
            ...  # run page.evaluate(...) / page.content() while the page is live

    The Chromium instance is closed when the context exits, on success or error.
    Raises `RenderError` if the page can't be loaded or has no readable content.
    """
    async with browser_session() as browser:
        page = await render_in(browser, url)
        try:
            yield page
        finally:
            await page.close()
