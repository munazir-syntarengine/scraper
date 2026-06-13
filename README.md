# Brand Website Scraper — demo

A standalone proof-of-concept: paste a brand's website URL, and it renders the
site with a headless browser and pulls out the brand's **real colors**, its
**heading and body fonts**, and its **clean text** — straight from the live
computed styles. It does a shallow crawl (homepage + the best-matching *about*
and *products* pages) and shows the result in a simple web UI.

This is a **demo**, not production. See [What this proves / does not prove](#what-this-proves--does-not-prove).

---

## Setup

Requires Python 3.11+ (developed on 3.14).

```bash
# from the repo root: c:\Users\THIS-PC\Desktop\syntar\scraper
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell/CMD
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
playwright install chromium        # downloads the headless browser (~once)
```

## Run

```bash
uvicorn app.main:app
```

Then open **http://localhost:8000** in your browser.

(If you didn't activate the venv, run `.\.venv\Scripts\python.exe -m uvicorn app.main:app`.)

## Try it

- `https://www.mailchimp.com` is pre-filled — click **Analyze**. You'll see the
  processing state, then a palette, the heading/body fonts, and the cleaned text
  pooled across the pages it crawled (home, about, products).
- Other sites that demo well: `https://stripe.com`, `https://www.oatly.com`.
- **Error-state example:** `https://www.theglenlivet.com` — alcohol brands gate
  behind bot protection and return HTTP 403. We don't evade it (by design), so
  the UI shows its clean error state. That's the expected "site blocked us" path.

Each analyze renders up to 3 real pages in a headless browser, so expect roughly
**10–20 seconds** per site (the first run after launch is a little slower while
Chromium warms up).

---

## How it works

One FastAPI process serves both the API and the frontend.

1. **Render** ([app/render.py](app/render.py)) — launches headless Chromium,
   navigates to the URL, waits for `DOMContentLoaded`, then waits up to 7s for
   the network to go quiet (best-effort — analytics-heavy sites never truly
   idle, so we proceed with what rendered). A blocked / timed-out / empty page
   raises a clean `RenderError`. **No bot-evasion**: default user-agent, no
   `navigator.webdriver` override.

2. **Extract** ([app/extract.py](app/extract.py)) — runs inline JS against the
   live page via `page.evaluate`:
   - **Colors:** computed `backgroundColor`/`color` across `h1, h2, button, a,
     body, header` → frequency → top 3–5 hex. Drops transparent, pure white,
     gray body-text colors, and browser default link colors.
   - **Fonts:** primary `font-family` of `h1` (heading) and `p` (body), generic
     keywords (`sans-serif`, `system-ui`, …) stripped, else `null`.
   - **Text:** strips `script/style/svg/form/iframe` and semantic
     `nav/footer/aside`, removes elements whose class/id marks boilerplate
     (cookie, banner, footer, nav, popup, newsletter, ads), then flattens
     `h1–h6 / p / li` into readable text.

3. **Crawl** ([app/crawl.py](app/crawl.py)) — the shallow brand crawl:
   - Renders the homepage, then collects its same-domain links (href + visible
     text).
   - Scores links against two pattern groups — **about** (about, story,
     who-we-are, what-we-do, our-mission, mission, philosophy, team, company,
     our-story) and **products/services** (product(s), service(s), shop, store,
     collection(s), what-we-offer, solutions, menu, pricing) — matching on both
     path and link text (whole-token, case-insensitive). The URL path is
     weighted higher than anchor text; among close-scoring matches the cleaner
     (shallowest, shortest) page wins.
   - Picks the single best **about** and **products** page (no hardcoded URLs).
     A group with no confident match is **skipped** — never a wrong guess.
   - Renders the selected pages **concurrently** (homepage + up to 2 = **max 3**).
   - **Merges:** text pooled with per-page headers; colors merged across pages by
     frequency (top 3–5 overall); fonts taken from the homepage (the hero defines
     the brand), falling back to other pages only for a missing role.
   - Per-page best-effort: a sub-page that blocks / times out / 404s is skipped
     and the crawl continues. Only an unreadable **homepage** errors the request.

4. **Frontend** ([static/index.html](static/index.html)) — the design reference,
   wired to the live endpoint. The source line lists the pages read
   (e.g. "read from mailchimp.com · 3 pages: home, about, products").

### API

`POST /analyze`

```json
// request
{ "url": "https://www.mailchimp.com" }

// response (200)
{
  "source_url": "https://mailchimp.com/",
  "text": "## Home — mailchimp.com\n\n...",
  "colors": ["#004E56", "#FFE01B", "#3860BE", "#241C15", "#EFEEEA"],
  "fonts": { "heading": "Means Web", "body": "Graphik Web" },
  "pages": [
    { "role": "home",     "url": "https://mailchimp.com/" },
    { "role": "about",    "url": "https://mailchimp.com/about/" },
    { "role": "products", "url": "https://mailchimp.com/pricing/marketing/" }
  ]
}

// response (502) — the homepage couldn't be read
{ "error": "the site returned HTTP 403" }
```

`GET /health` → `{ "ok": true }`.

---

## What this proves / does not prove

**Proves** — the extraction technique works: real computed brand colors, the
actual heading/body fonts, and clean rendered text, pulled live from a
headless-rendered page, across a shallow multi-page brand crawl.

**Does NOT prove** — the production concerns, deliberately left out (these are
the real-build follow-on):

- **No SSRF / URL validation.** Test URLs are trusted operator input. This is a
  known production requirement, omitted on purpose.
- **No anti-bot / Cloudflare / CAPTCHA handling, and no bot-evasion.** A site
  that blocks the request simply shows the error state.
- **No caching, database, queue, Temporal, synthesis, or Cora.**
- **No deep crawling** — only the homepage plus the named about/products pages,
  same registrable domain only, no link-following from sub-pages.
- **No product-catalog extraction.**

The same-domain check uses a simple eTLD+1 heuristic (last two host labels),
which is imperfect for multi-part suffixes like `.co.uk` — fine for a demo.
