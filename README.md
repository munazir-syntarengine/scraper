# Brand Website Scraper — demo

A standalone proof-of-concept: paste a brand's website URL, and it renders the
site with a headless browser and pulls out the brand's colors, heading/body
fonts, and clean text. It does a shallow crawl (homepage + the best-matching
*about* and *products* pages) and shows the result in a simple web UI.

The render engine is **Playwright + headless Chromium**, which exposes real
computed styles — so the color and font extraction works. It does a shallow
crawl and shows the result in a simple web UI.

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

Then open **http://localhost:8000** in your browser. (If you didn't activate the
venv: `.\.venv\Scripts\python.exe -m uvicorn app.main:app`.) The startup log
shows the render engine + active mode.

## Try it

- `https://www.mailchimp.com` is pre-filled — click **Analyze**. You'll see the
  processing state, then the brand palette, heading/body fonts, and the cleaned
  text pooled across the pages it crawled (home, about, products).
- Other sites that demo well: `https://stripe.com`, `https://www.oatly.com`.
- **Error-state example:** `https://www.theglenlivet.com` — in the default
  polite mode its robots.txt disallows our bot, so the UI shows its clean error
  state (we don't evade it).

Each analyze renders up to 3 real pages (subpages concurrently) with polite
per-host pacing — roughly **10–25 seconds** per site.

---

## How it works

One FastAPI process serves both the API and the frontend.

1. **Render** ([app/render.py](app/render.py)) — launches headless Chromium,
   applies the polite honest-UA context, navigates with
   `wait_until="domcontentloaded"`, then waits up to 7s for the network to go
   quiet (best-effort — analytics-heavy sites never truly idle, so we proceed
   with what rendered). A launch failure, timeout, hard HTTP error, or empty
   page raises a clean `RenderError`. The crawl opens the homepage + subpages as
   separate pages in one context.

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
   - Renders the selected pages **concurrently**, each robots-checked and
     per-host paced first (homepage + up to 2 = **max 3**).
   - **Merges:** text pooled with per-page headers; colors merged across pages by
     frequency (top 3–5 overall); fonts taken from the homepage (the hero defines
     the brand), falling back to other pages only for a missing role.
   - Per-page best-effort: a sub-page that blocks / times out / 404s / 429s /
     503s, or is disallowed by robots.txt, is skipped and the crawl continues.
     Only an unreadable (or robots-disallowed) **homepage** errors the request.

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

> Best-effort: if an extraction pass finds nothing it degrades to `[]` / `null`
> rather than failing the request — the contract shape is unchanged.

---

## Modes

The mode is chosen **once at startup** via the `MODE` env var. The two modes are
mutually exclusive — never switched mid-crawl, never combined.

### `MODE=polite` (default)

The crawl identifies itself honestly and behaves well — the opposite of evasion:

- **Honest user-agent:** `SyntarBrandBot/0.1 (+…)` on the browser context (no
  masking, no random-browser UA). Override with `SCRAPER_USER_AGENT`.
- **robots.txt respected** per host: a disallowed sub-page is skipped; a
  disallowed homepage errors cleanly. `Crawl-delay` is honored when larger than
  the floor.
- **Per-host pacing:** at least `POLITE_MIN_DELAY` (default 2s) between requests
  to the same host; concurrency cap `POLITE_MAX_CONCURRENCY` (default 2).
- **Backoff:** HTTP 429/503 → the page is skipped (no aggressive retry loop).

**Trade-off (by design):** an honest UA + robots compliance means *more* sites
may legitimately block the demo. That is correct, respectful behavior.

### `MODE=stealth` (gated — not enabled in this build)

Stealth mode is specified as bot-detection **evasion** (randomized realistic
fingerprint, `navigator.webdriver` patching, humanized mouse/scroll, robots.txt
bypass, referer spoofing, CAPTCHA/Cloudflare handling). Its purpose is to access
sites that have actively chosen to block bots.

It is **intentionally not implemented here** and is gated behind explicit
authorization for a specific, permitted target. Setting `MODE=stealth` logs a
warning at startup and makes `/analyze` return a clear "stealth not enabled"
error. Evasion of access controls and challenge systems is legally and ethically
fraught; enable it only against sites you own or are explicitly authorized to
test. See `tickets/10-stealth-mode.md`.

---

## Render engine

**Playwright + headless Chromium** (`playwright install chromium`). Chromium
exposes real computed styles, so the color and font extraction works — this is
what gives the demo its brand palette and typography. Subpages render
**concurrently** in one browser context, with a best-effort `networkidle` settle
(capped, so analytics-heavy sites don't hang the crawl). Polite mode (honest UA,
robots.txt, per-host pacing) applies here.

---

## What this proves / does not prove

**Proves** — the extraction technique works: real computed brand colors, the
actual heading/body fonts, and clean rendered text, pulled live from a
headless-rendered page, across a shallow multi-page brand crawl with no
hardcoded URLs, plus polite self-identifying scraping (honest UA, robots.txt).

**Does NOT prove** — the production concerns, deliberately left out (these are
the real-build follow-on):

- **No SSRF / URL validation.** Test URLs are trusted operator input. This is a
  known production requirement, omitted on purpose.
- **No bot-evasion in the default (polite) mode** — robots.txt is respected and
  the UA is honest, so a site that blocks the request simply shows the error
  state. (Evasion lives only in the **gated, unimplemented** stealth mode.)
- **No caching, database, queue, Temporal, synthesis, or Cora.**
- **No deep crawling** — only the homepage plus the named about/products pages,
  same registrable domain only, no link-following from sub-pages.
- **No product-catalog extraction.**

The same-domain check uses a simple eTLD+1 heuristic (last two host labels),
which is imperfect for multi-part suffixes like `.co.uk` — fine for a demo.
