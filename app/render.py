"""Fetch + render module — Playwright headless Chromium.

Launches headless Chromium, navigates to a URL, waits for the network to go
idle (best-effort, capped) so JS-rendered content and real computed styles are
present, and returns the live page handle. The extraction step runs
`page.evaluate(...)` / `page.content()` against the same page; `extract.py` is
unchanged. The crawl renders the homepage + subpages concurrently in one
context.

The browser context carries the polite honest-UA (ticket 09) via `context_args`.

Failures (launch error, timeout, blocked, empty) raise a typed `RenderError`
the endpoint maps to the frontend's error state.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app import config
from app.interact import handle_overlays

# Navigation (DOMContentLoaded) timeout.
NAV_TIMEOUT_MS = 30_000
# Best-effort wait for the network to go quiet after DOMContentLoaded.
# Analytics-heavy sites never truly idle, so a timeout here is NOT fatal — the
# DOM has rendered and we proceed with what loaded. Kept short so a 3-page crawl
# stays demo-fast.
NETWORK_IDLE_TIMEOUT_MS = 7_000

# Statuses that mean we got a block/challenge or an outage page, not the site
# itself — the body is never real content, so we fail fast regardless of it.
# Other error statuses (notably 404) are NOT fatal on their own: some SPAs/CDNs
# serve the real, fully-rendered page with an odd status, so we trust the
# rendered content instead (see the content gate in render_in).
_BLOCK_STATUSES = {401, 403, 407, 429}
# Below this many characters of rendered text, an error-status (>=400) page is
# treated as a genuine not-found/error page rather than usable content.
_MIN_CONTENT_CHARS = 200


class RenderError(Exception):
    """The page genuinely could not be read (launch failed, blocked, timed out, empty).

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
async def browser_session(
    context_args: Optional[dict] = None,
) -> AsyncIterator[BrowserContext]:
    """Launch headless Chromium and yield one context (open pages in it).

    The crawl renders the homepage and subpages as separate pages in this single
    context, so they all share the polite UA. The browser and context are closed
    on exit, on success or error.
    """
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as exc:
            raise RenderError(
                "could not launch Chromium — run `playwright install chromium`."
            ) from exc
        try:
            context = await browser.new_context(**(context_args or {}))
            try:
                yield context
            finally:
                await context.close()
        finally:
            await browser.close()


async def render_in(
    context: BrowserContext, url: str, viewport: Optional[dict] = None
) -> Page:
    """Open a new page in `context`, load `url`, and return it live.

    `viewport` ({'width','height'}) overrides this page's size — the crawl rolls
    a fresh one per page so each renders at a different real desktop size. When
    omitted, the page inherits the context's default viewport (context_args).

    Raises `RenderError` on a load failure, a hard HTTP error, or an empty body
    (the new page is closed before raising). On success the caller owns the
    returned page and must close it.
    """
    target = _normalize_url(url)
    page = await context.new_page()
    try:
        # 0) Per-page viewport override. Set BEFORE navigation so the page loads
        #    — and computes its styles (the colors/fonts we extract) — at this
        #    size, not at the context default it would briefly start with.
        if viewport:
            await page.set_viewport_size(viewport)

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

        # 3) Block / outage statuses (401/403/429/5xx) mean we got a challenge or
        #    error page, not the site — fail fast. Other error statuses (notably
        #    404) are NOT fatal on their own: some SPAs/CDNs serve the real,
        #    fully-rendered page with an odd status, so we defer to the content
        #    gate below and trust what actually rendered.
        status = response.status if response is not None else None
        if status is not None and (status in _BLOCK_STATUSES or status >= 500):
            raise RenderError(f"the site returned HTTP {status}", status=status)

        # 3.5) Accept cookies + pass any age gate. Best-effort: a no-op on pages
        #      without gates. Only re-wait for the network when we actually acted
        #      (the revealed content needs to settle) — otherwise skip the wait.
        if config.HANDLE_OVERLAYS:
            report = await handle_overlays(page)
            if report.get("cookies") or report.get("age_gate"):
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
                    )
                except PlaywrightTimeoutError:
                    pass

        # 4) Content gate. Empty → unreadable. A non-2xx status with only a thin
        #    body is a genuine error page (e.g. "404 Not Found"); a rich body
        #    means the page rendered fine despite the status, so we keep it.
        body_text = (await page.evaluate(
            "() => (document.body && document.body.innerText || '').trim()"
        )) or ""
        if not body_text:
            raise RenderError("the page returned no readable content")
        if status is not None and status >= 400 and len(body_text) < _MIN_CONTENT_CHARS:
            raise RenderError(f"the site returned HTTP {status}", status=status)

        return page
    except BaseException:
        await page.close()
        raise


@asynccontextmanager
async def render_page(
    url: str, context_args: Optional[dict] = None
) -> AsyncIterator[Page]:
    """Render a single `url` and yield the live page (standalone convenience).

    Usage:
        async with render_page(url) as page:
            ...  # page.evaluate(...) / page.content() while live
    """
    async with browser_session(context_args) as context:
        page = await render_in(context, url)
        try:
            yield page
        finally:
            await page.close()
