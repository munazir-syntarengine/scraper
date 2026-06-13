# 03 — Extraction (the technique being demonstrated)

**Implements:** spec §1.3, §4 (all three sub-techniques), §7.3
**Status:** done

## Goal
An extraction module (`app/extract.py`) that, given the rendered `page` from ticket 02, returns the three brand elements: 3–5 dominant brand colors as hex, the heading + body fonts, and the cleaned readable text. Each sub-extraction is independent and best-effort.

## Scope

### Colors (inline JS via `page.evaluate`)
- Collect computed `backgroundColor` and `color` across `h1, h2, button, a, body, header`.
- Count frequencies; normalize `rgb()` / `rgba()` → uppercase hex.
- Drop: transparent / fully-transparent alpha, pure white `#FFFFFF`, and generic near-black / dark-gray *text* colors (e.g. very low-saturation values near black used for body copy).
- Return the top **3–5** by frequency as the palette. If nothing qualifies, return `[]`.

### Fonts (inline JS via `page.evaluate`)
- Heading: `getComputedStyle(document.querySelector('h1')).fontFamily`. Body: same on `'p'`.
- Split on commas, take the first item as the primary font, strip surrounding quotes.
- Strip generic tokens (`serif`, `sans-serif`, `monospace`, `system-ui`, `-apple-system`, `ui-sans-serif`, etc.). If the first token is generic or the element is missing, return `null` for that role.

### Text (boilerplate-stripping cleaner)
- From the rendered HTML, strip `<script>`, `<style>`, `<noscript>`, `<svg>`, `<form>`, `<iframe>`.
- Remove elements whose `class` or `id` contains any of: `cookie`, `banner`, `footer`, `nav`, `popup`, `newsletter`, `ad`.
- Flatten remaining `h1`–`h6`, `p`, `li` into clean text, **preserving heading/paragraph separation** so the scroll panel reads naturally (e.g. headings marked distinctly from paragraphs).
- Collapse whitespace; drop empties.

## Out of scope
- No product-catalog extraction, no link following.
- Don't fail the whole module if one sub-extraction yields nothing — return the empty/null for that part.

## Acceptance criteria
- [ ] On a real brand site, colors returns 3–5 sensible brand hex values (not white, not body-text black).
- [ ] Heading and body fonts return the primary family name with generics stripped, or `null` when absent.
- [ ] Cleaned text is readable, boilerplate (nav/cookie/footer/newsletter) removed, headings vs paragraphs distinguishable.
- [ ] A page missing `h1`/`p` still returns text + colors with the missing font role as `null` (no crash).
