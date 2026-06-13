# 10 — Stealth Mode

**Implements:** updated-build-spec.md §4
**Status:** GATED — not implemented (requires explicit authorization)

## Summary
`MODE=stealth` is specified as an evasion-resistant operating mode for sites with
active bot protection: randomized realistic browser fingerprint,
`navigator.webdriver` override + automation-leak patching, humanized
mouse/scroll, robots.txt **bypass**, referer spoofing, and CAPTCHA/Cloudflare
challenge handling (detection + optional third-party solver integration).

The full intended scope (4.1–4.4) lives in `../updated-build-spec.md` §4.

## Why this is gated (not built)
This mode is **bot-detection evasion** — its purpose is to access sites that have
actively chosen to block automated traffic. That:

1. **Reverses the original spec.** `scraper_build_spec.md` §6 explicitly excluded
   *"Anti-bot / Cloudflare / CAPTCHA handling, and NO bot-evasion (no user-agent
   spoofing, no `navigator.webdriver` override)."*
2. **Circumvents access controls / challenge systems**, which is legally and
   ethically fraught and can violate sites' terms of service and applicable law.
3. Is **not needed for the demo's goal** — proving brand extraction works. The
   polite mode (ticket 09) fully serves that, honestly.

## Current behavior in this build
- `MODE=stealth` is recognized but **disabled**: the app logs a warning at
  startup and `/analyze` returns a clear `RenderError`
  ("stealth mode is not enabled in this build … set MODE=polite").
- The mode-selection architecture is in place (`app/config.py`,
  `app/crawl.py:_build_policy`) so a sanctioned implementation has a clean seam.
- No `stealth.py`, no fingerprint randomization, no CAPTCHA/Cloudflare handling,
  no robots.txt bypass is implemented.

## To un-gate (conditions)
Implement only against a **specific, permitted target** with documented
authorization (e.g. a site the operator owns, or a written pen-test / scraping
agreement). Even then:
- Prefer manual-intervention CAPTCHA handling over solver services.
- Keep it scoped to the authorized target, not a general-purpose bypass.

## Acceptance criteria (deferred until authorized)
The §4 acceptance criteria from updated-build-spec.md apply if/when this is
authorized and implemented. They are intentionally **not** checked off here.
