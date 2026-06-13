"""Runtime configuration (env-driven). Mode is chosen once at startup.

  MODE   polite (default) | stealth

Polite-mode knobs are read here too. Stealth mode is intentionally gated — see
STEALTH_GATED_MESSAGE and tickets/10-stealth-mode.md.
"""

from __future__ import annotations

import os

# ── Mode (set once at startup; never switched mid-crawl — spec §5) ────────────
MODE = os.getenv("MODE", "polite").strip().lower()

# ── Polite-scraping knobs (ticket 09) ─────────────────────────────────────────
POLITE_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "SyntarBrandBot/0.1 (+https://syntarengine.example/bot)",
)
POLITE_MIN_DELAY = float(os.getenv("POLITE_MIN_DELAY", "2.0"))
POLITE_MAX_CONCURRENCY = int(os.getenv("POLITE_MAX_CONCURRENCY", "2"))

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
