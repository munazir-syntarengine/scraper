# Build Spec — Brand Website Scraper Demo

**Repo (local, already created):** `C:\Users\THIS-PC\Desktop\syntar\scraper`
**Design reference (already in repo):** `scraper-demo-reference.html`
**Purpose:** A standalone proof-of-concept to show Michael that the brand-element extraction technique works. Paste a website URL, render it, and display its real brand colors, heading/body fonts, and cleaned text in a simple web UI that matches the reference.

This is a DEMO, not production. Read section 6 (Out of scope) before implementing.

---

## 0. How to work this spec

1. First, create a `tickets/` folder in the repo and write one markdown ticket file per work item (see section 7 for the ticket list). Each ticket states its goal, the spec section it implements, and its acceptance criteria. This is the local task tracker — no GitHub issues needed.
2. Then implement the tickets in order, off this spec, using `scraper-demo-reference.html` as the authoritative visual reference for the frontend.
3. Do NOT redesign the UI. The reference HTML is the source of truth for appearance. Match its layout, fonts (Inter), the single-blue accent, the card structure, and all four states (empty, processing, result, error). The only change from the reference: replace the mock setTimeout result with a real call to the backend.
4. Stop and ask if anything is ambiguous rather than guessing.

---

## 1. What it does

1. User pastes a website URL and clicks "Analyze".
2. Backend fetches and renders that page with headless Playwright (Chromium), waits for `networkidle`.
3. It extracts and returns:
   - **Clean text** — rendered page content, boilerplate stripped, flattened to readable text.
   - **Brand colors** — 3-5 dominant brand colors as hex, from computed styles of the rendered DOM.
   - **Typography** — heading font and body font, from computed styles, fallback chain stripped.
4. Frontend renders the result into the existing reference layout: color swatches with hex labels, the two font names (with previews), and the cleaned text in the scrollable panel. Plus the loading state during the fetch and the error state on failure.

---

## 2. Architecture (minimal)

Two pieces in the one repo:

- **Backend** — Python + FastAPI (matches the real stack). One endpoint `POST /analyze` taking `{ "url": "..." }`, returning the contract in section 3. Internals: one module for fetch+render (Playwright headless Chromium, wait for `networkidle`), one for extraction (color + font via inline JS through `page.evaluate`; text cleaning strips boilerplate then flattens). Enable CORS for the local frontend, or serve the frontend from FastAPI directly (simpler — one process).
- **Frontend** — built from `scraper-demo-reference.html`. Keep its markup, CSS, and states intact; replace the mock JS (the setTimeout block) with a real `fetch('/analyze', …)` call that posts the URL, shows the processing state while waiting, then populates the result cards (swatches, fonts, text) from the response, or shows the error state on failure.

No database, auth, caching, queue, Temporal, or synthesis. Run locally with one documented command.

---

## 3. Output contract

`POST /analyze` request: `{ "url": "string" }`

Response:
```json
{
  "source_url": "string",
  "text": "string (cleaned, readable page text)",
  "colors": ["#HEXCODE", "#HEXCODE", "..."],
  "fonts": { "heading": "string | null", "body": "string | null" }
}
```

Best-effort: if part of extraction fails (e.g. no custom heading font), return what succeeded and null/empty the rest rather than erroring the whole request. Only return an error response when the page genuinely can't be read (blocked, timeout, empty) — the frontend maps that to the error state.

The frontend renders `colors` as swatches (chip + hex label, matching `.sw`/`.chip`/`.hex` in the reference), `fonts.heading`/`fonts.body` into the two `.font-item` blocks, and `text` into `.text-scroll`. `source_url`'s domain goes in the `.src-line`.

---

## 4. Extraction details (the technique being demonstrated)

**Colors** — inline JS in the rendered page: collect computed `backgroundColor` and `color` across `h1, h2, button, a, body, header`; count frequencies; normalize RGB/RGBA → hex; drop transparent, pure white (`#FFFFFF`), and generic near-black/dark-gray text colors; return the top 3-5 by frequency as the brand palette.

**Fonts** — `getComputedStyle(document.querySelector('h1')).fontFamily` for heading, `('p')` for body; split on commas; take the first item as the primary font; strip generic tokens (`serif`, `sans-serif`, `monospace`, `system-ui`, etc.). If the element doesn't exist, return null for that role.

**Text** — strip `<script>`, `<style>`, `<noscript>`, `<svg>`, `<form>`, `<iframe>`; remove elements whose class or id contains `cookie`, `banner`, `footer`, `nav`, `popup`, `newsletter`, `ad`; flatten remaining `h1`-`h6`, `p`, `li` into clean text (preserve heading/paragraph separation so the scroll panel reads naturally). Wait for `networkidle` before extracting so JS-rendered content is present.

---

## 5. Frontend wiring (against the reference)

Take `scraper-demo-reference.html` as-is and make it functional:
- Keep all CSS and the four state blocks (`#empty`, `#proc`, `#error`, `#result`).
- Replace the `<script>`'s mock setTimeout flow with: on Analyze click → hide empty, hide prior result/error, show `#proc`, disable the button → `fetch('/analyze', { method:'POST', body: JSON.stringify({url}) })` → on success, populate the result cards from the response and show `#result` → on failure (or error response), show `#error` → re-enable the button. Keep the rotating processing messages.
- Populate swatches dynamically from `colors` (one `.sw` per hex). Populate the two fonts from `fonts` (set the `.font-name`; the preview can use the returned font name as the `font-family` so it previews the real font if the browser has it, falling back gracefully). Populate `.text-scroll` from `text`. Set the `.src-line` domain from `source_url`.
- Do not restyle. Same Inter, same blue, same cards.

---

## 6. Out of scope (deliberately omitted — it's a demo)

- SSRF / URL validation — NONE. Test URLs are trusted operator input. (This is a known production requirement deliberately left out; note it in the README.)
- Caching, database, queue, Temporal, synthesis, Cora — none.
- Crawling beyond the single given page — no link-following, no depth.
- Anti-bot / Cloudflare / CAPTCHA handling, and NO bot-evasion (no user-agent spoofing, no `navigator.webdriver` override). If a site blocks the fetch, return an error → the UI shows the clean error state.
- Product-catalog extraction.

---

## 7. Tickets to create (in `tickets/`)

Write each as its own `.md` file with goal + acceptance criteria, then implement in this order:

1. **`01-project-setup.md`** — Initialize the Python project (FastAPI + Playwright + an HTML→text/markdown cleaner lib), `requirements.txt`, project structure, `playwright install chromium`. AC: `pip install` + `playwright install` succeed; app skeleton runs.
2. **`02-fetch-render.md`** — Playwright fetch+render module: launch headless Chromium, navigate to a URL, wait for `networkidle`, return the rendered page handle/HTML. Handle timeout/blocked → raise a clean "could not read" error. AC: renders a known JS-heavy page and returns content; a bad/blocked URL raises cleanly.
3. **`03-extraction.md`** — Color, font, and text extraction per section 4 (inline JS for color/font via `page.evaluate`; boilerplate-stripping text cleaner). AC: on a real brand site, returns 3-5 sensible hex colors, a heading + body font, and readable cleaned text.
4. **`04-analyze-endpoint.md`** — `POST /analyze` wiring fetch+render+extract into the section-3 contract, with best-effort partial success and the error response. CORS or static-serve the frontend. AC: posting a URL returns the contract JSON; a blocked URL returns the error shape.
5. **`05-frontend.md`** — Wire `scraper-demo-reference.html` to the real endpoint per section 5, all four states functional, no restyle. AC: paste URL → see live colors/fonts/text in the reference UI; failure shows the error state.
6. **`06-readme.md`** — README: setup (`pip install`, `playwright install chromium`), one run command, an example, and an honest "what this proves / does not prove" note (extraction technique only; production concerns are follow-on). AC: a fresh clone can run it from the README alone.

---

## 8. Done = demoable

Complete when Munazir can run it locally, paste a real brand's website URL, click Analyze, and see in the reference UI: the page's brand colors as swatches, its heading and body fonts, and its cleaned text — with a working loading state and a clean error state when a site can't be read.

**Honest framing for Michael:** this demonstrates the extraction technique (real computed colors/fonts, clean rendered text). It does NOT demonstrate the production concerns — SSRF, hostile-site/popup handling, scale, synthesis wiring — which are the real-build follow-on.
