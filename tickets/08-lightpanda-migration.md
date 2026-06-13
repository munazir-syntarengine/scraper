# 08 — Lightpanda Migration

**Implements:** updated-build-spec.md §2 (engine swap Playwright/Chromium → Lightpanda over CDP)
**Status:** REVERTED — Lightpanda removed; reverted to Playwright + Chromium.
Lightpanda's beta has no computed CSS (`getComputedStyle` returns defaults), so
color/font extraction returned `[]`/`null` on every site, killing the demo's
core value-prop. Chromium restored as the sole engine. Ticket 09 (polite
scraping) was kept and now runs on the Chromium path. See README "Render engine".

## Goal
Replace the Playwright-launched Chromium with a connection to a running
Lightpanda CDP server. Keep the same `page` handle interface so `extract.py`
and the crawl merge logic are untouched. Lightpanda is lighter/faster for the
up-to-3-page brand crawl. The `/analyze` contract (§3 of the original spec) is
unchanged.

### Key change
Lightpanda is **not** a Playwright-native engine — you do not `launch()` it. Run
its CDP server and **connect** to it; Playwright stays as the CDP *client*.

```python
# render.py — connect instead of launch
LIGHTPANDA_CDP = "ws://127.0.0.1:9222"   # env-overridable
async with async_playwright() as p:
    browser = await p.chromium.connect_over_cdp(LIGHTPANDA_CDP)
    context = await browser.new_context()      # default UA, no stealth
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    # ... existing best-effort network-quiet wait, then extract.py runs on `page`
```

### Run model
Lightpanda runs as a separate process; the app connects to it.
```bash
./lightpanda serve --host 127.0.0.1 --port 9222
# or: docker run -d -p 9222:9222 lightpanda/browser:nightly
```

## Scope
- Rewrite `render.py` to `connect_over_cdp(LIGHTPANDA_CDP)` (env-overridable,
  default `ws://127.0.0.1:9222`) instead of launching Chromium.
- Keep the existing `RenderError` contract: failed connection, navigation
  timeout, blocked/empty page still raise `RenderError` cleanly so the endpoint
  maps it to the error state.
- If Lightpanda is **not running**, raise a clear `RenderError` telling the user
  to start the Lightpanda CDP server — not a raw socket traceback.
- Keep `wait_until="domcontentloaded"` + the existing best-effort network-quiet
  wait. **Do not** switch to `networkidle` (not reliably supported via
  Lightpanda's CDP).
- Keep all best-effort / partial-success behavior in extraction. Lightpanda's
  Web API coverage is partial (beta), so a `page.evaluate` color/font pass may
  yield less on some sites; that must degrade to `[]`/`null`, never crash.
- `requirements.txt`: `playwright` stays (used as the CDP client).
  `playwright install chromium` is **no longer needed at runtime** — note this
  in the README; Chromium is replaced by the Lightpanda process.
- Pin a specific Lightpanda version/tag in the README (Playwright-over-CDP
  support is WIP and can drift between Lightpanda releases).

## Acceptance criteria
- [ ] With Lightpanda running, the full demo works end-to-end (homepage + about
      + products crawl, real colors/fonts/text in the UI) exactly as before the swap.
- [ ] `render.py` connects over CDP; it does not launch Chromium.
- [ ] Lightpanda not running → a clean `RenderError` with a helpful message, not
      a raw traceback.
- [ ] A blocked/timed-out/empty page still raises `RenderError` and surfaces as
      the UI error state.
- [ ] Extraction still returns the §3 contract; partial gaps degrade to
      `[]`/`null` without crashing.
- [ ] README updated: how to start Lightpanda, the pinned version, and that
      `playwright install chromium` is no longer required at runtime.

## Notes
- Deploy impact: the Dockerfile (deploy prep) currently uses the
  Playwright/Chromium base image. Migrating to Lightpanda changes the container
  story — Lightpanda must run as a sidecar/second process the app connects to.
  The current Dockerfile is left as-is; the Railway deploy would need a second
  Lightpanda service. Not changed in this pass.
- **Lightpanda-beta limitation (verified against `lightpanda/browser:nightly`):**
  `getComputedStyle` returns defaults, not resolved styles (e.g. `fontFamily` is
  `""`, colors read as transparent/black; even inline styles don't reflect). So
  the computed-style **color and font extraction yields `[]` / `null` on every
  site**. Text extraction, link discovery, and the crawl work well. The code
  degrades gracefully (no crash) per the spec's partial-success requirement, but
  the colors/fonts value-prop is unavailable under Lightpanda until it implements
  computed styles. To demo real colors/fonts, run the prior Chromium build.
- Also verified: Lightpanda permits only one context + one page at a time, so the
  crawl reuses a single page and renders sequentially; `networkidle` is not used.
