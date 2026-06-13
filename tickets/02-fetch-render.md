# 02 — Fetch & Render

**Implements:** spec §1.2, §2 (render module), §4 (wait for `networkidle`), §7.2
**Status:** done

## Goal
A render module (`app/render.py`) that launches headless Chromium, navigates to a URL, waits for the network to go idle so JS-rendered content is present, and hands back the live `page` handle (and/or rendered HTML) for the extraction step. Failures (timeout, blocked, empty) raise one clean, typed error the endpoint can map to the error state.

## Scope
- Launch headless Chromium via Playwright (sync or async — pick one and stay consistent; async pairs naturally with FastAPI).
- `goto(url, wait_until="networkidle")` with a sane timeout (e.g. 30s). Treat the page as one live render — the extraction module runs `page.evaluate(...)` against this same handle (don't tear it down before extraction).
- Define a `RenderError` (or similar) exception. Raise it on: navigation timeout, non-OK final response status that yields no usable content, or empty/blank body.
- **No bot-evasion** (spec §6): default user-agent, do NOT override `navigator.webdriver`, no stealth. If a site blocks us, that surfaces as `RenderError`.
- Provide a small context-manager / helper so the browser is always closed even on error.

## Out of scope
- No SSRF / URL validation (spec §6 — trusted operator input).
- No retries, no proxy, no crawling.

## Acceptance criteria
- [ ] Rendering a known JS-heavy page returns non-empty content after `networkidle`.
- [ ] A bad/unreachable/blocked URL raises `RenderError` (not an unhandled traceback), and the browser is closed.
- [ ] No bot-evasion code present (verifiable by inspection).
