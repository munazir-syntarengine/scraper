# 05 — Frontend Wiring

**Implements:** spec §1.4, §5 (frontend wiring against the reference), §7.5
**Status:** done

## Goal
Make `scraper-demo-reference.html` functional against the real `/analyze` endpoint. Keep its markup, CSS, and all four states intact. The ONLY change is replacing the mock `setTimeout` flow with a real `fetch`. Do NOT restyle.

## Scope
- Copy the reference HTML into `static/index.html` (served by ticket 04). Keep: Inter font, the single `--blue` accent, the card structure, and the four state blocks `#empty`, `#proc`, `#error`, `#result`.
- Replace the mock `<script>` (the `setTimeout` block) with the real flow on **Analyze** click:
  1. Read the URL from `#url`.
  2. Hide `#empty`; remove `.show` from `#result` and `#error`; add `.show` to `#proc`; disable `#go`.
  3. Keep the rotating processing messages (the `msgs` interval).
  4. `fetch('/analyze', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ url }) })`.
  5. On success → populate result cards from the response, show `#result`.
  6. On failure OR error response → show `#error`.
  7. Always → clear the interval, hide `#proc`, re-enable `#go`.
- **Populate dynamically from the response:**
  - `colors` → one `.sw` per hex (`.chip` background = hex, `.hex` label = uppercase hex). Clear any hardcoded reference swatches first.
  - `fonts.heading` / `fonts.body` → set each `.font-name`; set the `.font-preview` `font-family` to the returned font name so it previews the real font if installed, falling back gracefully. If a role is `null`, show a neutral placeholder (e.g. "—" / "not detected") without breaking layout.
  - `text` → render into `.text-scroll`, preserving heading vs paragraph separation (`<h4>` for headings, `<p>` for paragraphs). Insert as text content, not raw HTML, to avoid injecting markup from the scraped page.
  - `source_url` → set the `.src-line` to the domain (keep the "· rendered with headless Chromium" tail).

## Out of scope
- No restyling, no new states, no layout changes (spec §5: reference is source of truth).

## Acceptance criteria
- [ ] Pasting a real brand URL and clicking Analyze shows the processing state, then live colors/fonts/text in the reference layout.
- [ ] A failed/blocked fetch shows the `#error` state cleanly; the button re-enables.
- [ ] Swatches, font names/previews, text panel, and the source domain all come from the live response (no mock data left).
- [ ] Visual appearance is unchanged from the reference (same Inter, same blue, same cards).
- [ ] Scraped text is inserted as text content (no HTML injection from the target page).
