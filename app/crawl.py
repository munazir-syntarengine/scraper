"""Shallow brand crawl (ticket 07; engine + politeness from tickets 08/09).

Homepage (always) + the top 4 best-matching internal pages (hard cap of 5).
Every same-domain link is scored against informative-page patterns (about,
products/services, contact, news); the 4 highest-scoring distinct pages are
crawled — no hardcoded URLs, no per-category quota. Same registrable domain only.

  crawl(url) -> { source_url, text, colors, fonts, pages }

Rendering uses Playwright + headless Chromium; subpages render concurrently in
one browser context (real computed colors/fonts). MODE=polite (ticket 09) is
applied: each request is robots-checked and per-host paced before it fires.
MODE=stealth is gated (ticket 10).

Sub-page failures are skipped best-effort; only a homepage failure (or a
homepage disallowed by robots.txt) errors the whole request.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from app import config
from app.extract import extract_all, extract_links, rank_color_counts
from app.polite import Politeness
from app.render import RenderError, browser_session, render_in

# Pattern groups — matched on BOTH path and link text, case-insensitive. Each
# group also names the role a selected page is labelled with. Selection is NOT
# one-per-group: every link is scored against all groups and the top matches win
# (see select_pages), so a site can contribute e.g. two "about" pages if those
# happen to be its strongest links.
ABOUT_PATTERNS = [
    "about", "story", "who-we-are", "who-we-our", "what-we-do",
    "our-mission", "mission", "philosophy", "team", "company", "our-story",
]
PRODUCT_PATTERNS = [
    "product", "products", "service", "services", "shop", "store",
    "collection", "collections", "what-we-offer", "solutions", "menu", "pricing",
]
CONTACT_PATTERNS = [
    "contact", "contact-us", "get-in-touch", "reach-us",
    "locations", "find-us", "visit-us",
]
NEWS_PATTERNS = [
    "blog", "news", "press", "insights", "articles",
]

# (role, patterns) in tie-break priority order: when a link scores equally for
# two groups, the earlier group names it; and when two *pages* tie on score,
# the earlier group's page is picked first (brand-core pages over news).
PAGE_CATEGORIES = [
    ("about", ABOUT_PATTERNS),
    ("products", PRODUCT_PATTERNS),
    ("contact", CONTACT_PATTERNS),
    ("news", NEWS_PATTERNS),
]
ROLE_PRIORITY = {role: i for i, (role, _patterns) in enumerate(PAGE_CATEGORIES)}

# Friendly labels for the response / frontend. Unlisted roles fall back to the
# role string itself (title-cased) in the merge.
ROLE_LABELS = {
    "home": "home", "about": "about", "products": "products",
    "contact": "contact", "news": "news",
}

# The rule: always the homepage, then up to this many best-matching subpages.
MAX_SUBPAGES = 4
MAX_PAGES = 1 + MAX_SUBPAGES  # homepage + 4

# Minimum score for a link to be selected. A path keyword (+3) or a multi-word
# path phrase (+4) clears this; a lone ambiguous text token like "story" in
# "Read the story" (+1) does not — so we don't crawl a customer success story as
# if it were the brand's about page.
MIN_SELECT_SCORE = 3


# ─────────────────────────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────────────────────────

def _registrable_domain(url: str) -> str:
    """Approx eTLD+1: last two host labels, `www.` stripped.

    Good enough for .com/.org/.net brands; imperfect for multi-part suffixes
    like .co.uk (acceptable for a demo). Lets us treat www / bare / shop. /
    store. subdomains of the same brand as same-domain.
    """
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _same_domain(href: str, registrable: str) -> bool:
    host = (urlparse(href).hostname or "").lower()
    return bool(registrable) and (host == registrable or host.endswith("." + registrable))


def _canonical(url: str) -> str:
    """Canonical form for dedup: scheme://host/path (no fragment, no trailing /)."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    query = f"?{p.query}" if p.query else ""
    return f"{p.scheme}://{p.netloc.lower()}{path}{query}"


# ─────────────────────────────────────────────────────────────────────────────
# Scoring + selection
# ─────────────────────────────────────────────────────────────────────────────

def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _score(path: str, text: str, patterns: list[str]) -> int:
    """Score a link against a pattern group, on both path and link text.

    Whole-token / phrase matching (not raw substring) so `production` doesn't
    match `product`, `permission` doesn't match `mission`, etc. The path is
    weighted higher than link text — `/about` is a far stronger signal than the
    word "about" appearing somewhere in anchor text.
    """
    path_tokens = set(_tokens(path))
    text_tokens = set(_tokens(text))
    path_phrase = " ".join(_tokens(path))
    text_phrase = " ".join(_tokens(text))

    score = 0
    for pattern in patterns:
        parts = pattern.split("-")
        if len(parts) > 1:  # multi-word pattern (e.g. who-we-are) → phrase match
            phrase = " ".join(parts)
            if phrase in path_phrase:
                score += 4
            if phrase in text_phrase:
                score += 2
        else:  # single token → whole-word match
            if pattern in path_tokens:
                score += 3
            if pattern in text_tokens:
                score += 1
    return score


def _path_depth(url: str) -> int:
    """Number of non-empty path segments (/about → 1, /a/b/c → 3)."""
    return len([seg for seg in urlparse(url).path.split("/") if seg])


def _classify(path: str, text: str) -> tuple[int, str | None]:
    """Best (score, role) for a link across all category groups.

    The link is scored against every group; the highest-scoring group wins and
    names the page's role. Ties break toward the earlier group in PAGE_CATEGORIES.
    Returns (0, None) if the link matches nothing.
    """
    best_score, best_role = 0, None
    for role, patterns in PAGE_CATEGORIES:
        score = _score(path, text, patterns)
        if score > best_score:
            best_score, best_role = score, role
    return best_score, best_role


def select_pages(home_url: str, links: list[dict]) -> list[tuple[str, str]]:
    """The top `MAX_SUBPAGES` best-matching internal pages from the homepage.

    Every same-domain link (minus the homepage and off-domain links) is scored
    against the category groups; the highest-scoring distinct pages that clear
    MIN_SELECT_SCORE are returned as (role, url), best first. No per-category
    quota — the strongest links win regardless of which group they match, so the
    same section can appear more than once if those are the top links.

    Ranking: score desc, then shallower path, then category priority (brand-core
    over news), then shorter path, then first seen on the page (stable) — so
    ties resolve to the cleaner, more brand-central page.
    """
    home_canon = _canonical(home_url)
    registrable = _registrable_domain(home_url)

    # canonical_url -> (score, role, first_seen_index); keep the best per URL.
    best: dict[str, tuple[int, str, int]] = {}
    for idx, link in enumerate(links):
        href = link.get("href", "")
        text = link.get("text", "")
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        if not _same_domain(href, registrable):
            continue
        canon = _canonical(href)
        if canon == home_canon:
            continue
        score, role = _classify(parsed.path, text)
        if score < MIN_SELECT_SCORE or role is None:
            continue
        prev = best.get(canon)
        if prev is None:
            best[canon] = (score, role, idx)
        elif score > prev[0]:
            best[canon] = (score, role, prev[2])  # keep first-seen position

    def _rank(item):
        url, (score, role, idx) = item
        path = urlparse(url).path.rstrip("/")
        return (-score, _path_depth(url), ROLE_PRIORITY.get(role, 99), len(path), idx)

    ranked = sorted(best.items(), key=_rank)
    return [(role, url) for url, (_score, role, _idx) in ranked[:MAX_SUBPAGES]]


# ─────────────────────────────────────────────────────────────────────────────
# Merge
# ─────────────────────────────────────────────────────────────────────────────

def _host_path(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").replace("www.", "", 1)
    path = p.path.rstrip("/")
    return host + path


def _merge(pages: list[dict]) -> dict:
    """Merge per-page extractions into the single contract."""
    # colors — sum frequencies across all pages, then top 3-5.
    total_counts: dict[str, int] = {}
    for page in pages:
        for hex_val, count in (page.get("color_counts") or {}).items():
            total_counts[hex_val] = total_counts.get(hex_val, 0) + count
    colors = rank_color_counts(total_counts)

    # fonts — homepage defines the brand; fall back to later pages per role.
    fonts = {"heading": None, "body": None}
    fonts.update(pages[0].get("fonts") or {})
    for role in ("heading", "body"):
        if not fonts.get(role):
            for page in pages[1:]:
                value = (page.get("fonts") or {}).get(role)
                if value:
                    fonts[role] = value
                    break

    # text — pool with a readable per-page header (renders as an <h4> divider).
    sections = []
    for page in pages:
        header = f"## {ROLE_LABELS.get(page['role'], page['role']).title()} — {_host_path(page['url'])}"
        body = (page.get("text") or "").strip()
        sections.append(f"{header}\n\n{body}" if body else header)
    text = "\n\n".join(sections)

    return {
        "colors": colors,
        "fonts": fonts,
        "text": text,
        "pages": [{"role": p["role"], "url": p["url"]} for p in pages],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def _build_policy() -> Politeness:
    """Return the active mode's request policy. Stealth mode is gated (ticket 10)."""
    if config.MODE == "polite":
        return Politeness(
            config.POLITE_USER_AGENT,
            config.POLITE_MIN_DELAY,
            config.POLITE_MAX_CONCURRENCY,
        )
    if config.MODE == "stealth":
        raise RenderError(config.STEALTH_GATED_MESSAGE)
    raise RenderError(f"unknown MODE={config.MODE!r}; set MODE=polite")


async def _render_extract_subpage(context, policy, role: str, url: str) -> dict | None:
    """Robots-gate + pace + render + extract one sub-page. None on any failure.

    Best-effort: a block / timeout / 404 / 429 / 503 / robots-disallow → None.
    """
    try:
        if not await policy.allowed(url):   # robots → skip this page
            return None
        await policy.wait_turn(url)         # per-host pacing
        page = await render_in(context, url, viewport=policy.next_viewport())
        try:
            result = await extract_all(page)
            return {"role": role, "url": page.url, **result}
        finally:
            await page.close()
    except Exception:
        return None


async def crawl(start_url: str) -> dict:
    """Crawl the homepage + top MAX_SUBPAGES best-matching pages, then merge.

    The homepage is always read first; the subpages are then rendered
    concurrently in one Chromium context. Each request is robots-checked and
    per-host paced first (MODE=polite).

    Raises `RenderError` if the homepage can't be read or is disallowed by robots.
    """
    policy = _build_policy()  # raises RenderError if MODE=stealth (gated)

    async with browser_session(policy.context_args()) as context:
        # Homepage — failure (or robots-disallow) propagates (nothing to read).
        if not await policy.allowed(start_url):
            raise RenderError("the homepage is disallowed by robots.txt")
        await policy.wait_turn(start_url)
        home_page = await render_in(context, start_url, viewport=policy.next_viewport())
        try:
            home_url = home_page.url
            home_result = await extract_all(home_page)
            try:
                links = await extract_links(home_page)
            except Exception:
                links = []
        finally:
            await home_page.close()
        pages = [{"role": "home", "url": home_url, **home_result}]

        # Discover the best-matching subpages, then render them concurrently
        # (best-effort), each robots-gated and per-host paced. select_pages
        # already caps the list at MAX_SUBPAGES.
        subpages = select_pages(home_url, links)
        results = await asyncio.gather(
            *(_render_extract_subpage(context, policy, role, url)
              for role, url in subpages)
        )
        for result in results:
            if result and len(pages) < MAX_PAGES:
                pages.append(result)

    merged = _merge(pages)
    merged["source_url"] = pages[0]["url"]
    return merged
