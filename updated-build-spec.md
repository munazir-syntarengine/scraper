# Build Spec — Lightpanda Migration + Polite Scraping + Stealth Mode

**Repo (local):** `C:\Users\THIS-PC\Desktop\syntar\scraper`
**Status of prior work:** tickets 01–07 are **done and implemented**. This spec is additive.
**Implements:** swaps the render engine (Playwright/Chromium → Lightpanda over CDP), adds polite-scraping behavior, and introduces a configurable stealth mode. The output contract (§3 of the original spec) is **unchanged**.

---

## 0. How to work this spec

1. Create three new ticket files in `tickets/`: `08-lightpanda-migration.md`, `09-polite-scraping.md`, and `10-stealth-mode.md`, each with goal + acceptance criteria copied from sections 2, 3, and 4 below.
2. Implement `08` first (engine swap), verify the existing demo still works end-to-end, then implement `09` (politeness) on top.
3. Implement `10` (stealth mode) as a configurable alternative to `09`. Do **not** mix polite and stealth behaviors in the same crawl — the mode is chosen at startup.
4. Do **not** change the `/analyze` contract, the extraction logic (`extract.py`), or the frontend. Only `render.py`, the crawl wiring, `requirements.txt`, and the README change.
5. Read section 5 (Out of scope) before implementing. Some commonly-suggested additions are deliberately banned here.
6. Stop and ask if anything is ambiguous rather than guessing.

---

## 1. Why

Lightpanda is a from-scratch headless browser (CDP-compatible) that is far lighter and faster than Chromium for headless crawling, which suits the up-to-3-page brand crawl. Polite scraping makes the demo honest about how it identifies itself and respectful of what each site allows. Stealth mode provides an alternative for sites with active bot protection (CAPTCHA, Cloudflare, etc.) where polite scraping is blocked — it is **not** a fallback but a separate operational mode.

---

## 2. Ticket 08 — Lightpanda migration

**Goal:** replace the Playwright-launched Chromium with a connection to a running Lightpanda CDP server. Keep the same `page` handle interface so `extract.py` and the crawl merge logic are untouched.

### Key change
Lightpanda is **not** a Playwright-native engine — you do not `launch()` it. You run its CDP server and **connect** to it. Playwright stays as the CDP *client*.

```python
# render.py — connect instead of launch
from playwright.async_api import async_playwright

LIGHTPANDA_CDP = "ws://127.0.0.1:9222"

async with async_playwright() as p:
    browser = await p.chromium.connect_over_cdp(LIGHTPANDA_CDP)
    context = await browser.new_context()      # default UA, no stealth (see §5)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    # ... existing best-effort network-quiet wait, then extract.py runs on `page`
```

### Run model
Lightpanda runs as a separate process. The app connects to it.

```bash
# binary
./lightpanda serve --host 127.0.0.1 --port 9222
# or docker
docker run -d -p 9222:9222 lightpanda/browser:nightly
```

### Scope
- Rewrite `render.py` to `connect_over_cdp` to `LIGHTPANDA_CDP` (env-overridable, default `ws://127.0.0.1:9222`) instead of launching Chromium.
- Keep the existing `RenderError` contract: a failed connection, navigation timeout, blocked/empty page still raises `RenderError` cleanly so the endpoint maps it to the error state.
- If Lightpanda is **not running**, raise a clear `RenderError` with a message that says to start the Lightpanda CDP server (don't surface a raw socket traceback).
- Keep `wait_until="domcontentloaded"` + the existing best-effort network-quiet wait. **Do not** switch to `networkidle` — it is not reliably supported via Lightpanda's CDP.
- Keep all best-effort / partial-success behavior in extraction. Lightpanda's Web API coverage is partial (beta), so a `page.evaluate` color/font pass may yield less on some sites; that must degrade to `[]`/`null`, never crash.
- `requirements.txt`: `playwright` stays (used as the CDP client). `playwright install chromium` is **no longer needed at runtime** — note this in the README; Chromium is replaced by the Lightpanda process.
- Pin a specific Lightpanda version/tag in the README. Playwright-over-CDP support is officially WIP and can drift between Lightpanda releases.

### Acceptance criteria
- [ ] With Lightpanda running, the full demo works end-to-end (homepage + about + products crawl, real colors/fonts/text in the UI) exactly as before the swap.
- [ ] `render.py` connects over CDP; it does not launch Chromium.
- [ ] Lightpanda not running → a clean `RenderError` with a helpful message, not a raw traceback.
- [ ] A blocked/timed-out/empty page still raises `RenderError` and surfaces as the UI error state.
- [ ] Extraction still returns the §3 contract; partial gaps degrade to `[]`/`null` without crashing.
- [ ] README updated: how to start Lightpanda, the pinned version, and that `playwright install chromium` is no longer required at runtime.

---

## 3. Ticket 09 — Polite scraping

**Goal:** the crawl identifies itself honestly, respects each site's `robots.txt`, paces its requests, and backs off when asked. This is the opposite of evasion: it makes the bot *more* visible and better-behaved, not less.

**Activation:** `MODE=polite` (default).

### Scope
- **Honest user-agent:** set a descriptive, identifiable UA on the browser context, e.g. `SyntarBrandBot/0.1 (+<contact-or-info-url>)`. No masking, no "random realistic browser" UA.
- **robots.txt:** before rendering any URL (homepage and each discovered sub-page), check `robots.txt` for that host with `urllib.robotparser`. Disallowed → skip that page (homepage disallowed → `RenderError`, same as an unreadable homepage). Cache the parsed robots per host. Optionally also pass Lightpanda's `--obey_robots` flag as a second layer.
- **Per-host rate limit:** enforce a minimum delay between requests to the same host (default ~2s, env-overridable). Honor `Crawl-delay` from robots if present and larger.
- **Concurrency cap:** the crawl already caps at 3 pages; pace them through a small semaphore (default 2 concurrent) plus the per-host delay rather than firing all at once.
- **Backoff:** on HTTP 429 or 503, skip that page (per-page best-effort already exists) — do not hammer with retries. A single short backoff before giving up is acceptable; no aggressive retry loop.
- Wire all of the above into the existing crawl loop in `crawl.py`, before each `render()` call. A small `polite.py` helper (robots cache + per-host timing + semaphore) is the clean way to hold this.

### Reference helper
```python
# polite.py (sketch — adapt to the real crawl.py structure)
import asyncio, time, urllib.robotparser, urllib.parse

USER_AGENT = "SyntarBrandBot/0.1 (+https://syntarengine.example/bot)"

class Politeness:
    def __init__(self, min_delay=2.0, max_concurrency=2):
        self.min_delay = min_delay
        self.sem = asyncio.Semaphore(max_concurrency)
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _host(self, url): return urllib.parse.urlsplit(url).netloc

    async def allowed(self, url: str) -> bool:
        host = self._host(url)
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"https://{host}/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None          # no robots reachable -> allowed, stay gentle
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    async def wait_turn(self, url: str):
        host = self._host(url)
        async with self.sem:
            gap = time.monotonic() - self._last.get(host, 0)
            if gap < self.min_delay:
                await asyncio.sleep(self.min_delay - gap)
            self._last[host] = time.monotonic()
```

### Acceptance criteria
- [ ] The browser context sends a descriptive, identifiable user-agent (no masking, no randomized browser UA).
- [ ] `robots.txt` is fetched and respected per host; a disallowed sub-page is skipped, a disallowed homepage errors cleanly.
- [ ] Same-host requests are spaced by at least the configured min delay; `Crawl-delay` is honored when larger.
- [ ] Crawl concurrency is capped (semaphore); pages are paced, not fired all at once.
- [ ] On 429/503 the page is skipped (no aggressive retry loop); the crawl continues with what succeeded.
- [ ] README documents the polite-scraping behavior and the trade-off: respecting robots + an honest UA means *more* sites may legitimately block the demo, which is correct behavior.

---

## 4. Ticket 10 — Stealth mode

**Goal:** when `MODE=stealth` is set, the crawl operates as an evasion-resistant bot capable of bypassing common bot detection systems (CAPTCHA, Cloudflare, basic fingerprinting). This is **not** a fallback from polite mode — it is a separate operational mode chosen at startup for sites where polite scraping is blocked.

**Activation:** `MODE=stealth` (env var). When set, polite behaviors (honest UA, robots.txt checks, rate limiting) are **disabled** — stealth mode supersedes them entirely.

### Scope

#### 4.1 Randomized realistic browser fingerprint
- Generate a random realistic user-agent from a curated pool of modern Chrome/Firefox/Edge UAs (rotated per crawl session, not per page).
- Set matching `viewport`, `locale`, `timezone`, and `colorScheme` to align with the chosen UA profile.
- Override `navigator.webdriver` to `false` via CDP `Page.addScriptToEvaluateOnNewDocument`.
- Remove or patch common automation leaks: `navigator.plugins`, `navigator.languages`, `window.chrome` object, `Permissions` API inconsistencies.
- Use Playwright's `bypass_csp` and `ignore_https_errors` as needed for compatibility.

#### 4.2 Humanized mouse movement
- All scroll and click actions must use **parabolic or arc-based paths** rather than straight lines.
- Inject **realistic jitter** (Gaussian noise on velocity and direction) during movement.
- **Random pause before scrolling:** after page load, wait a random duration (e.g., 1.5–4.5s) before initiating any scroll action.
- **Variable scroll velocity:** scroll speed should vary — start slow, accelerate, then decelerate as the target approaches. No constant-speed scrolling.
- **Micro-pauses during scroll:** brief random stops (200–800ms) mid-scroll to simulate reading/decision-making.
- **No programmatic `scrollTo` calls** — use incremental `wheel` events or `mouse.wheel()` with the above patterns.

#### 4.3 CAPTCHA / Cloudflare handling
- Detect common challenge indicators (Cloudflare turnstile, reCAPTCHA v2/v3, hCAPTCHA) by DOM presence or URL patterns.
- On detection: **pause the crawl** and expose a status endpoint (`/status`) indicating "CAPTCHA detected — manual intervention required" or integrate with a CAPTCHA-solving service (e.g., 2Captcha, Anti-Captcha) if API keys are configured.
- If no solver is configured and CAPTCHA is encountered, raise `RenderError` with a clear message: "CAPTCHA challenge detected — configure a solver or switch to MODE=polite for cooperative sites."
- Do **not** implement a custom CAPTCHA solver (OCR, ML, etc.) — only third-party service integration or manual intervention.

#### 4.4 Request behavior in stealth mode
- **No robots.txt checks** — stealth mode bypasses `robots.txt` entirely (it is designed for sites that block polite bots).
- **Minimal rate limiting:** reduce per-host delay to ~0.5s (still not zero, to avoid obvious DDoS patterns). Concurrency cap remains at 2.
- **Retry logic:** on 429/503, perform **one exponential backoff retry** (base 2s, max 8s) before giving up. No aggressive retry loops.
- **Referer spoofing:** set realistic `Referer` headers per request (e.g., Google search, internal navigation).

### Implementation structure
Create `stealth.py` as the stealth counterpart to `polite.py`:

```python
# stealth.py (sketch — adapt to real crawl.py structure)
import asyncio, random, time
from playwright.async_api import Page

class StealthMode:
    def __init__(self, min_delay=0.5, max_concurrency=2):
        self.min_delay = min_delay
        self.sem = asyncio.Semaphore(max_concurrency)
        self._last: dict[str, float] = {}
        self._ua_pool = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
            # ... curated pool of 10–15 realistic UAs
        ]
        self._current_ua = random.choice(self._ua_pool)

    def get_context_args(self):
        return {
            "user_agent": self._current_ua,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "bypass_csp": True,
            "ignore_https_errors": True,
        }

    async def humanized_scroll(self, page: Page, target_y: int):
        # Parabolic arc + jitter + micro-pauses implementation
        current_y = await page.evaluate("window.scrollY")
        distance = target_y - current_y

        # Random pause before starting
        await asyncio.sleep(random.uniform(1.5, 4.5))

        steps = random.randint(15, 35)
        for i in range(steps):
            # Parabolic easing + jitter
            progress = i / steps
            ease = self._parabolic_ease(progress)
            step = (distance / steps) * ease + random.gauss(0, 2)

            await page.mouse.wheel(0, int(step))

            # Micro-pause randomly
            if random.random() < 0.15:
                await asyncio.sleep(random.uniform(0.2, 0.8))

            await asyncio.sleep(random.uniform(0.05, 0.15))

    def _parabolic_ease(self, t: float) -> float:
        # Parabolic arc: slow start, fast middle, slow end
        return 4 * t * (1 - t) if t < 0.5 else 1 - 4 * (1 - t) * (1 - t)

    async def wait_turn(self, url: str):
        host = urllib.parse.urlsplit(url).netloc
        async with self.sem:
            gap = time.monotonic() - self._last.get(host, 0)
            if gap < self.min_delay:
                await asyncio.sleep(self.min_delay - gap)
            self._last[host] = time.monotonic()
```

### Acceptance criteria
- [ ] `MODE=stealth` disables all polite behaviors (no honest UA, no robots.txt checks, no 2s rate limit).
- [ ] Browser context uses a randomized realistic UA + matching viewport/locale/timezone.
- [ ] `navigator.webdriver` is overridden to `false`; common automation leaks are patched.
- [ ] Mouse movements use parabolic/arced paths with jitter; no straight-line scrolling.
- [ ] Random pause (1.5–4.5s) before any scroll action; variable scroll velocity; micro-pauses during scroll.
- [ ] CAPTCHA/Cloudflare detection triggers a clear status message or solver integration; no custom CAPTCHA solving.
- [ ] 429/503 triggers one exponential backoff retry (2s–8s) before giving up.
- [ ] README documents both modes: polite (default, transparent, respectful) vs. stealth (evasion-resistant, for protected sites), and the ethical/legal trade-offs of each.

---

## 5. Out of scope — DO NOT ADD

These are explicitly excluded. Do not add them even if they seem like a natural fit:

- **No runtime mode switching.** The mode (`polite` or `stealth`) is set once at startup via env var. Do not switch modes mid-crawl or per-page.
- **No hybrid mode.** Do not combine polite and stealth behaviors (e.g., honest UA + humanized mouse, or robots.txt checks + fingerprint randomization). The modes are mutually exclusive.
- **No SSRF/URL validation** (unchanged from original §6).
- **No caching/DB/queue/Temporal/synthesis/Cora** (unchanged from original §6).
- **No deep crawl beyond the named pages** (unchanged from original §6).
- **No product-catalog extraction** (unchanged from original §6).

---

## 6. Done

- The demo runs against Lightpanda instead of Chromium, with the same UI and the same `/analyze` contract.
- In `MODE=polite` (default): the crawl identifies itself honestly, respects `robots.txt`, paces and caps its requests, and backs off politely.
- In `MODE=stealth`: the crawl uses evasion-resistant techniques (randomized fingerprint, humanized input, CAPTCHA handling) for sites with active bot protection.
- A fresh clone can run it from the README alone (start Lightpanda, `pip install`, `uvicorn app.main:app`, open `http://localhost:8000`).
