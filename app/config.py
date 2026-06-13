"""Runtime configuration (env-driven). Mode is chosen once at startup.

  MODE   polite (default) | stealth

Polite-mode knobs are read here too. Stealth mode is intentionally gated — see
STEALTH_GATED_MESSAGE and tickets/10-stealth-mode.md.
"""

from __future__ import annotations

import os
import random

# ── Mode (set once at startup; never switched mid-crawl — spec §5) ────────────
MODE = os.getenv("MODE", "polite").strip().lower()

# ── Polite-scraping knobs (ticket 09) ─────────────────────────────────────────
POLITE_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "SyntarBrandBot/0.1 (+https://syntarengine.example/bot)",
)
POLITE_MIN_DELAY = float(os.getenv("POLITE_MIN_DELAY", "2.0"))
POLITE_MAX_CONCURRENCY = int(os.getenv("POLITE_MAX_CONCURRENCY", "2"))

# ── Dynamic viewport (ticket 02; changing per crawl) ──────────────────────────
# Each crawl picks one viewport from this pool, so the rendered layout — and the
# computed colors/fonts we extract from it — reflects a real, common screen size
# rather than Playwright's fixed 1280x720 default. All sizes are wide enough that
# responsive sites still serve their full desktop layout (this is NOT mobile
# emulation — it varies the desktop size, not the device).
VIEWPORT_POOL = [
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (1280, 720),
]


def _parse_viewport(raw: str) -> tuple[int, int] | None:
    """Parse a `WIDTHxHEIGHT` env value (e.g. `1440x900`) → (w, h), else None."""
    try:
        w, h = raw.lower().split("x")
        return (int(w), int(h))
    except Exception:
        return None


# Pin a single fixed viewport with VIEWPORT=WIDTHxHEIGHT (e.g. VIEWPORT=1440x900)
# to disable the per-crawl rotation — useful for reproducible runs and tests.
VIEWPORT_FIXED = _parse_viewport(os.getenv("VIEWPORT", ""))


def pick_viewport() -> dict:
    """Return a `{'width', 'height'}` viewport for one crawl.

    Fixed if VIEWPORT=WIDTHxHEIGHT is set; otherwise a random pick from
    VIEWPORT_POOL so consecutive crawls vary. Consumed once per crawl by the
    request policy (polite.py `context_args`), which passes it straight to
    Playwright's `browser.new_context(viewport=...)`.
    """
    w, h = VIEWPORT_FIXED or random.choice(VIEWPORT_POOL)
    return {"width": w, "height": h}

# ── Stealth mode: gated (ticket 10) ───────────────────────────────────────────
# Stealth mode is bot-detection evasion (fingerprint randomization,
# robots.txt bypass, CAPTCHA/Cloudflare circumvention). It is deliberately NOT
# implemented in this build and is gated behind explicit authorization. The
# polite, transparent mode is the supported path.
STEALTH_GATED_MESSAGE = (
    "MODE=stealth is not enabled in this build. Stealth mode performs "
    "bot-detection evasion (fingerprint randomization, robots.txt bypass, "
    "CAPTCHA/Cloudflare circumvention) and is intentionally gated pending "
    "explicit authorization for a specific, permitted target. Use MODE=polite."
)
