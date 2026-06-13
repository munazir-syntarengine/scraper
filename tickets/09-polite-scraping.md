# 09 — Polite Scraping

**Implements:** updated-build-spec.md §3
**Status:** done
**Depends on:** 08 (engine swap) implemented and verified first.

## Goal
The crawl identifies itself honestly, respects each site's `robots.txt`, paces
its requests, and backs off when asked. This is the opposite of evasion — it
makes the bot *more* visible and better-behaved, not less.

**Activation:** `MODE=polite` (default).

## Scope
- **Honest user-agent:** set a descriptive, identifiable UA on the browser
  context, e.g. `SyntarBrandBot/0.1 (+<contact-or-info-url>)`. No masking, no
  "random realistic browser" UA.
- **robots.txt:** before rendering any URL (homepage and each discovered
  sub-page), check `robots.txt` for that host with `urllib.robotparser`.
  Disallowed → skip that page (homepage disallowed → `RenderError`, same as an
  unreadable homepage). Cache the parsed robots per host.
- **Per-host rate limit:** enforce a minimum delay between requests to the same
  host (default ~2s, env-overridable). Honor `Crawl-delay` from robots if
  present and larger.
- **Concurrency cap:** the crawl already caps at 3 pages; pace them through a
  small semaphore (default 2 concurrent) plus the per-host delay rather than
  firing all at once.
- **Backoff:** on HTTP 429 or 503, skip that page (per-page best-effort already
  exists) — do not hammer with retries. A single short backoff before giving up
  is acceptable; no aggressive retry loop.
- Wire all of the above into the existing crawl loop in `crawl.py`, before each
  `render()` call. A small `polite.py` helper (robots cache + per-host timing +
  semaphore) is the clean way to hold this.

## Reference helper
See updated-build-spec.md §3 for the `Politeness` class sketch
(`robots` cache, `allowed(url)`, `wait_turn(url)` with semaphore + per-host
min-delay).

## Acceptance criteria
- [ ] The browser context sends a descriptive, identifiable user-agent (no
      masking, no randomized browser UA).
- [ ] `robots.txt` is fetched and respected per host; a disallowed sub-page is
      skipped, a disallowed homepage errors cleanly.
- [ ] Same-host requests are spaced by at least the configured min delay;
      `Crawl-delay` is honored when larger.
- [ ] Crawl concurrency is capped (semaphore); pages are paced, not fired all at once.
- [ ] On 429/503 the page is skipped (no aggressive retry loop); the crawl
      continues with what succeeded.
- [ ] README documents the polite-scraping behavior and the trade-off:
      respecting robots + an honest UA means *more* sites may legitimately block
      the demo, which is correct behavior.

## Scope guards
- This is mutually exclusive with stealth mode — mode is chosen once at startup
  via env var, never switched mid-crawl, never combined (spec §5).
