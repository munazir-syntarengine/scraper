"""Shallow brand crawl (ticket 07; engine + politeness from tickets 08/09).

Homepage + the single best-matching about page + the single best-matching
products/services page (hard cap of 3). Pages are discovered by scoring the
homepage's own links — no hardcoded URLs. Same registrable domain only.

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

# Pattern groups — matched on BOTH path and link text, case-insensitive.
ABOUT_PATTERNS = [
    "about", "story", "who-we-are", "who-we-our", "what-we-do",
    "our-mission", "mission", "philosophy", "team", "company", "our-story",
]
PRODUCT_PATTERNS = [
    "product", "products", "service", "services", "shop", "store",
    "collection", "collections", "what-we-offer", "solutions", "menu", "pricing",
]

# Friendly labels for the response / frontend.
ROLE_LABELS = {"home": "home", "about": "about", "products": "products"}

# Max pages rendered (homepage + about + products).
MAX_PAGES = 3

# Minimum score for a link to be selected. The path keyword (+3) or a multi-word
# text phrase (+3) clears this; a lone ambiguous text token like "story" in
# "Read the story" (+1) does not — so we skip the group rather than crawl a
# customer success story as if it were the brand's about page.
MIN_SELECT_SCORE = 3

# When a shallower (more top-level) page scores within this many points of the
# best match, prefer it — /pricing reads cleaner in the demo than a deep
# /solutions/email-sms-professional-services page that only out-scored it by
# matching a second path keyword.
SHALLOW_TIEBREAK_DELTA = 3


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


def _choose(candidates: list[tuple[str, int]]) -> tuple[int, str | None]:
    """Pick the best URL from scored (url, score) candidates for one group.

    Returns (score, url) or (best_score, None) if nothing clears the threshold.
    Among matches within SHALLOW_TIEBREAK_DELTA of the top score, prefer the
    shallowest path (cleaner top-level page), then the higher score, then the
    first seen (stable order).
    """
    if not candidates:
        return (0, None)
    best_score = max(score for _, score in candidates)
    if best_score < MIN_SELECT_SCORE:
        return (best_score, None)
    contenders = [
        (url, score)
        for url, score in candidates
        if score >= best_score - SHALLOW_TIEBREAK_DELTA
    ]
    # Among close-scoring matches, prefer the cleaner page: shallowest path,
    # then shortest path string, then higher score (stable for first-seen).
    def _rank(item):
        url, score = item
        path = urlparse(url).path.rstrip("/")
        return (_path_depth(url), len(path), -score)

    contenders.sort(key=_rank)
    url, score = contenders[0]
    return (score, url)


def select_pages(home_url: str, links: list[dict]) -> dict[str, str | None]:
    """Pick the best about + products URL from the homepage's links.

    Returns {'about': url|None, 'products': url|None}. Excludes the homepage and
    off-domain links; ensures the two roles don't resolve to the same URL.
    """
    home_canon = _canonical(home_url)
    registrable = _registrable_domain(home_url)

    # role -> {canonical_url: best_score}, insertion order = first seen on page.
    candidates: dict[str, dict[str, int]] = {"about": {}, "products": {}}
    groups = (("about", ABOUT_PATTERNS), ("products", PRODUCT_PATTERNS))

    for link in links:
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
        for role, patterns in groups:
            s = _score(parsed.path, text, patterns)
            if s <= 0:
                continue
            if s > candidates[role].get(canon, 0):
                candidates[role][canon] = s

    about_score, about_url = _choose(list(candidates["about"].items()))
    product_score, product_url = _choose(list(candidates["products"].items()))

    # If both groups picked the same URL, keep it for the higher-scoring role.
    if about_url and about_url == product_url:
        if about_score >= product_score:
            product_url = None
        else:
            about_url = None

    return {"about": about_url, "products": product_url}


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
        page = await render_in(context, url)
        try:
            result = await extract_all(page)
            return {"role": role, "url": page.url, **result}
        finally:
            await page.close()
    except Exception:
        return None


async def crawl(start_url: str) -> dict:
    """Crawl homepage + best about + best products page (<= MAX_PAGES) and merge.

    Subpages render concurrently in one Chromium context. Each request is
    robots-checked and per-host paced first (MODE=polite).

    Raises `RenderError` if the homepage can't be read or is disallowed by robots.
    """
    policy = _build_policy()  # raises RenderError if MODE=stealth (gated)

    async with browser_session(policy.context_args()) as context:
        # Homepage — failure (or robots-disallow) propagates (nothing to read).
        if not await policy.allowed(start_url):
            raise RenderError("the homepage is disallowed by robots.txt")
        await policy.wait_turn(start_url)
        home_page = await render_in(context, start_url)
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

        # Discover the about/products pages, then render them concurrently
        # (best-effort), each robots-gated and per-host paced.
        selected = select_pages(home_url, links)
        subpages = [
            (role, selected[role])
            for role in ("about", "products")
            if selected.get(role)
        ]
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
