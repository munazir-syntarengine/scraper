"""Extraction module — brand colors, fonts, and cleaned text (spec §4).

Given the live Playwright `page` from `render.py`, pull the three brand
elements. Each sub-extraction is independent and best-effort: a failure in one
returns its empty/null value rather than killing the others.

  - Colors: computed `backgroundColor`/`color` across key elements → frequency
    → hex palette, dropping transparent / white / gray body-text colors.
  - Fonts: primary `font-family` of `h1` (heading) and `p` (body), generics
    stripped, else null.
  - Text: boilerplate-stripped, flattened headings/paragraphs/list-items.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import Page

# ─────────────────────────────────────────────────────────────────────────────
# Colors
# ─────────────────────────────────────────────────────────────────────────────

# Selectors whose computed colors tend to carry the brand (spec §4).
_COLOR_SELECTOR = "h1, h2, button, a, body, header"

# Channel value at/above which we call a color "effectively white" (spec drops
# pure white #FFFFFF; we allow a small tolerance so 254/255 still counts).
_WHITE_MIN = 250
# Max chroma (max-min channel) for a color to count as gray/black. Body-copy
# grays measured ~9-20; brand colors measure far higher (a blue link ~167).
# Applied ONLY to text colors — a dark *background* like #1A1A1A is a real
# brand color and is kept.
_GRAY_CHROMA_MAX = 30

# Browser user-agent defaults for unstyled links — never a brand color, but
# they're saturated enough to survive the gray filter, so drop them explicitly.
_UA_DEFAULT_COLORS = {"#0000EE", "#551A8B", "#EE0000"}

_RGB_RE = re.compile(
    r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)\s*(?:[,/]\s*([\d.]+)\s*)?\)",
    re.I,
)


def _parse_rgb(value: str) -> Optional[tuple[int, int, int, float]]:
    """Parse a computed `rgb()`/`rgba()` string → (r, g, b, alpha) or None."""
    m = _RGB_RE.search(value or "")
    if not m:
        return None
    r, g, b = (int(round(float(m.group(i)))) for i in (1, 2, 3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return r, g, b, a


def _to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_white(r: int, g: int, b: int) -> bool:
    return r >= _WHITE_MIN and g >= _WHITE_MIN and b >= _WHITE_MIN


def _is_grayish(r: int, g: int, b: int) -> bool:
    return (max(r, g, b) - min(r, g, b)) <= _GRAY_CHROMA_MAX


def rank_color_counts(counts: dict[str, int]) -> list[str]:
    """Top 3-5 hex values by frequency. Shared by single-page and crawl merge."""
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [hex_val for hex_val, _ in ranked[:5]]


async def extract_color_counts(page: Page) -> dict[str, int]:
    """Return {hex: frequency} for qualifying brand colors on the page.

    Collects computed background + text colors in the browser, then normalizes
    and filters in Python. The frequency map (not just the top-5) is the raw
    material the crawl merges across pages.
    """
    # Aggregate counts in the page to keep the payload small. We keep background
    # and text colors separate so the gray-text filter can be applied to text
    # only (dark brand *backgrounds* must survive).
    raw = await page.evaluate(
        """
        (selector) => {
          const bg = {}, fg = {};
          for (const el of document.querySelectorAll(selector)) {
            const cs = getComputedStyle(el);
            if (cs.backgroundColor) bg[cs.backgroundColor] = (bg[cs.backgroundColor] || 0) + 1;
            if (cs.color)          fg[cs.color]            = (fg[cs.color] || 0) + 1;
          }
          return { bg, fg };
        }
        """,
        _COLOR_SELECTOR,
    )

    counts: dict[str, int] = {}
    for kind, mapping in (("bg", raw.get("bg", {})), ("text", raw.get("fg", {}))):
        for color_str, freq in mapping.items():
            parsed = _parse_rgb(color_str)
            if not parsed:
                continue
            r, g, b, a = parsed
            if a == 0:                                  # transparent
                continue
            if _is_white(r, g, b):                      # pure/near white
                continue
            if kind == "text" and _is_grayish(r, g, b):  # gray body-text noise
                continue
            hex_val = _to_hex(r, g, b)
            if hex_val in _UA_DEFAULT_COLORS:           # unstyled-link defaults
                continue
            counts[hex_val] = counts.get(hex_val, 0) + int(freq)

    return counts


async def extract_colors(page: Page) -> list[str]:
    """Return 3-5 dominant brand colors as uppercase hex, ranked by frequency."""
    return rank_color_counts(await extract_color_counts(page))


# ─────────────────────────────────────────────────────────────────────────────
# Links (for the shallow crawl — ticket 07)
# ─────────────────────────────────────────────────────────────────────────────

async def extract_links(page: Page) -> list[dict]:
    """Return [{href, text}] for every `<a href>` on the page.

    `href` is the browser-resolved absolute URL. `text` combines visible link
    text with `aria-label`/`title` so icon-only links are still matchable.
    """
    return await page.evaluate(
        """
        () => {
          const out = [];
          for (const a of document.querySelectorAll('a[href]')) {
            const parts = [a.textContent || '', a.getAttribute('aria-label') || '', a.getAttribute('title') || ''];
            const text = parts.join(' ').replace(/\\s+/g, ' ').trim();
            if (a.href) out.push({ href: a.href, text });
          }
          return out;
        }
        """
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fonts
# ─────────────────────────────────────────────────────────────────────────────

# Generic CSS font keywords — if the primary family is one of these, the site
# has no distinctive brand font for that role → null (spec §4).
_GENERIC_FONTS = {
    "serif", "sans-serif", "monospace", "system-ui",
    "-apple-system", "blinkmacsystemfont",
    "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded",
    "cursive", "fantasy", "math", "emoji",
    "inherit", "initial", "unset",
}


def _primary_font(font_family: Optional[str]) -> Optional[str]:
    """First family in the stack, quotes stripped; None if generic/absent."""
    if not font_family:
        return None
    first = font_family.split(",")[0].strip().strip("'\"").strip()
    if not first or first.lower() in _GENERIC_FONTS:
        return None
    return first


async def extract_fonts(page: Page) -> dict[str, Optional[str]]:
    """Return {'heading': <h1 font|null>, 'body': <p font|null>}."""
    families = await page.evaluate(
        """
        () => {
          const pick = (sel) => {
            const el = document.querySelector(sel);
            return el ? getComputedStyle(el).fontFamily : null;
          };
          return { heading: pick('h1'), body: pick('p') };
        }
        """
    )
    return {
        "heading": _primary_font(families.get("heading")),
        "body": _primary_font(families.get("body")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Text
# ─────────────────────────────────────────────────────────────────────────────

# Whole elements to drop before flattening.
_STRIP_TAGS = ("script", "style", "noscript", "svg", "form", "iframe")
# Semantic boilerplate containers. The spec's class/id tokens (below) miss
# modern sites that use these tags with hashed/styled-component class names, so
# strip the tags themselves too. (<header> is intentionally NOT here — hero
# <h1>s often live inside it.)
_BOILER_TAGS = ("nav", "footer", "aside")
# Substrings in class/id that mark boilerplate (spec §4).
_BOILER_SUBSTR = ("cookie", "banner", "footer", "nav", "popup", "newsletter")
# Consent-platform (CMP) container markers. Their preference panels are hidden
# via CSS, but BeautifulSoup can't see `display:none`, so their "Manage Consent
# Preferences" markup would otherwise leak into the text once we accept cookies.
# These vendor strings never appear in real brand content, so — unlike the
# generic tokens above — they're stripped regardless of how much text they hold
# (a CMP panel can out-text a sparse visual homepage and slip past the guard).
_CMP_SUBSTR = ("onetrust", "optanon", "ot-sdk", "cookiebot", "usercentrics")
# A class/id-flagged element is only stripped if it holds at most this share of
# the page's text blocks. Above it, the "boilerplate" token is almost certainly
# a theme marker on a big wrapper (e.g. WordPress puts `ehf-footer
# nav-float-right` on <body>), not an actual nav/footer — stripping it would
# nuke the whole page.
_BOILER_MAX_CONTENT_SHARE = 0.5
# Tags we flatten into the cleaned text, in document order.
_TEXT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li")
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Generous cap so a pathological page can't produce a multi-MB response.
_MAX_TEXT_CHARS = 30_000


def _is_boilerplate(class_id: str) -> bool:
    """True if a class/id string marks a boilerplate element."""
    if any(tok in class_id for tok in _BOILER_SUBSTR):
        return True
    # 'ad' (advertising) matched as a WHOLE token only — a naive substring would
    # nuke header / breadcrumb / shadow / loading / thread, destroying content.
    for token in re.split(r"[^a-z0-9]+", class_id):
        if token in ("ad", "ads") or token.startswith("advert"):
            return True
    return False


def clean_text(html: str) -> str:
    """Strip boilerplate, flatten headings/paragraphs/list-items to clean text.

    Output is newline-delimited blocks (separated by a blank line). Block
    markers let the frontend reconstruct structure (ticket 05):
      - `## ` prefix  → heading
      - `• ` prefix   → list item
      - otherwise     → paragraph
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Drop non-content tags and semantic boilerplate containers outright.
    for tag in soup(list(_STRIP_TAGS) + list(_BOILER_TAGS)):
        tag.decompose()

    # Drop consent-platform (CMP) containers outright — always boilerplate,
    # stripped regardless of size (their hidden panels can hold more text than a
    # sparse homepage). Skip the structural root so we never wipe the page.
    for el in soup.find_all(True):
        if getattr(el, "decomposed", False) or el.name in ("html", "body"):
            continue
        class_id = (" ".join(el.get("class") or []) + " " + (el.get("id") or "")).lower()
        if class_id.strip() and any(tok in class_id for tok in _CMP_SUBSTR):
            el.decompose()

    # Drop boilerplate by class/id (nav, cookie banner, footer, newsletter, ads).
    # Two guards stop a theme's boilerplate-ish token on a big container from
    # nuking real content: never strip the structural root (<html>/<body>), and
    # never strip an element holding most of the page's text (a genuine
    # nav/footer/cookie widget is a small fraction of it).
    text_tag_total = len(soup.find_all(list(_TEXT_TAGS)))
    for el in soup.find_all(True):
        if getattr(el, "decomposed", False):
            continue
        if el.name in ("html", "body"):
            continue
        class_id = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
        if class_id.strip() and _is_boilerplate(class_id.lower()):
            if text_tag_total and (
                len(el.find_all(list(_TEXT_TAGS)))
                > text_tag_total * _BOILER_MAX_CONTENT_SHARE
            ):
                continue
            el.decompose()

    blocks: list[str] = []
    total = 0
    for el in soup.find_all(list(_TEXT_TAGS)):
        # Skip elements nested inside another flattened tag (e.g. <p> in <li>)
        # so their text isn't emitted twice.
        if el.find_parent(list(_TEXT_TAGS)):
            continue
        text = re.sub(r"\s+", " ", el.get_text(separator=" ", strip=True)).strip()
        if not text:
            continue
        if el.name in _HEADING_TAGS:
            block = f"## {text}"
        elif el.name == "li":
            block = f"• {text}"
        else:
            block = text
        # Skip an exact repeat of the previous block (common with sr-only dupes).
        if blocks and blocks[-1] == block:
            continue
        blocks.append(block)
        total += len(block)
        if total >= _MAX_TEXT_CHARS:
            break

    return "\n\n".join(blocks)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

async def extract_all(page: Page) -> dict:
    """Run all three extractions best-effort and return the section-3 fields.

    A failure in any one sub-extraction yields its empty/null value rather than
    raising, so a partial result still reaches the caller. `color_counts` (the
    pre-ranking frequency map) is included so the crawl can merge across pages;
    single-page callers just use `colors`.
    """
    try:
        color_counts = await extract_color_counts(page)
    except Exception:
        color_counts = {}

    try:
        fonts = await extract_fonts(page)
    except Exception:
        fonts = {"heading": None, "body": None}

    try:
        text = clean_text(await page.content())
    except Exception:
        text = ""

    return {
        "colors": rank_color_counts(color_counts),
        "fonts": fonts,
        "text": text,
        "color_counts": color_counts,
    }
