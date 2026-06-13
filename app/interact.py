"""Best-effort overlay handling — accept cookie banners, pass age gates.

Brand sites (especially alcohol brands) hide their content behind a cookie
consent banner and/or an age-verification dialog. Both stop the extractor from
seeing the real page, so before we read a page we try to:

  1. Accept all cookies — known consent-platform buttons (OneTrust, Cookiebot,
     Usercentrics, …) first, then a text fallback ("Accept all", "Allow all").
  2. Pass an age gate — if a date-of-birth form is present, fill an adult DOB
     (config.AGE_GATE_DOB) and submit; otherwise, if an age-gate *overlay* is
     detected, click its affirmative button ("I am 21", "Enter", "Yes", …).

Everything here is best-effort and never raises: a site with no gate simply has
nothing to click, and a gate we can't operate is left alone. The age-gate logic
only acts when a gate is actually detected, so it never touches a normal page's
inputs/buttons.
"""

from __future__ import annotations

import logging

from app import config

logger = logging.getLogger("uvicorn.error")

# Network-settle wait after an interaction reveals new content.
_SETTLE_TIMEOUT_MS = 5_000


# ─────────────────────────────────────────────────────────────────────────────
# Cookie consent
# ─────────────────────────────────────────────────────────────────────────────

# Known "accept all" buttons across the common consent platforms. Tried first
# because they're exact — no risk of clicking the wrong thing.
_COOKIE_ACCEPT_SELECTORS = [
    "#onetrust-accept-btn-handler",                              # OneTrust
    "#accept-recommended-btn-handler",                           # OneTrust (alt)
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",    # Cookiebot
    "#CybotCookiebotDialogBodyButtonAcceptAll",                  # Cookiebot (alt)
    "#CybotCookiebotDialogBodyButtonAccept",                     # Cookiebot (alt)
    "button[data-testid='uc-accept-all-button']",               # Usercentrics
    "#cookiescript_accept",                                      # CookieScript
    ".cc-allow", ".cc-btn.cc-allow",                            # Cookie Consent
    ".js-accept-cookies", ".cookie-consent-accept",
    "[data-cookiebanner='accept_button']",                       # Meta-style
]

# JS: click the first visible match among the given selectors; failing that,
# the first visible button whose text reads like an accept-all control. Returns
# a short label of whatever was clicked, or null.
_ACCEPT_COOKIES_JS = r"""
(selectors) => {
  const isVis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden'
           && s.display !== 'none' && s.opacity !== '0';
  };
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (isVis(el)) { el.click(); return sel; }
    }
  }
  // Text fallback — prefer "accept/allow all", then narrower accept phrasings.
  const cands = [...document.querySelectorAll(
    "button, a[role=button], [role=button], input[type=button], input[type=submit]")];
  const passes = [
    /\b(accept all|allow all|accept all cookies|allow all cookies)\b/i,
    /^(accept|i accept|i agree|agree|allow cookies|accept cookies|got it)$/i,
  ];
  for (const want of passes) {
    for (const el of cands) {
      if (!isVis(el)) continue;
      const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
      if (t && want.test(t)) { el.click(); return t.slice(0, 40); }
    }
  }
  return null;
};
"""


async def _accept_cookies(page) -> str | None:
    try:
        return await page.evaluate(_ACCEPT_COOKIES_JS, _COOKIE_ACCEPT_SELECTORS)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Age verification
# ─────────────────────────────────────────────────────────────────────────────

# JS: detect an age gate. True when visible DOB inputs exist, or a visible
# modal / fixed overlay mentions age verification. Keeps us from touching the
# inputs/buttons of a normal page.
_DETECT_AGE_GATE_JS = r"""
() => {
  const isVis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const dob = [...document.querySelectorAll(
    "input[autocomplete^='bday'], input[name='day' i], input[name='month' i], "
    + "input[name='year' i], input[aria-label*='date of birth' i], "
    + "input[aria-label*='birth' i]")].filter(isVis);
  const AGE = /(drinking age|legal age|verify your age|age verif|are you (over|of legal)|over 21|over 18|21 years|18 years|old enough|date of birth|year of birth|of legal drinking|must be (of legal|21|18))/i;
  let overlay = false;
  for (const el of document.querySelectorAll("[role=dialog], [aria-modal=true], div, section")) {
    if (!isVis(el)) continue;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    const isModal = el.getAttribute('role') === 'dialog'
      || el.getAttribute('aria-modal') === 'true' || s.position === 'fixed';
    const big = r.width > window.innerWidth * 0.4 && r.height > 120;
    if (isModal && big && AGE.test(el.innerText || '')) { overlay = true; break; }
  }
  return { hasDob: dob.length > 0, overlay };
};
"""

# JS: scoped to the DOB form/dialog, click its submit / affirmative button.
# Negatives ("no", "under", "exit") are skipped so we never click the decline.
_SUBMIT_AGE_JS = r"""
() => {
  const day = document.querySelector(
    "input[autocomplete='bday-day'], input[name='day' i], input[aria-label*='day' i]");
  const scope = day && (day.closest('form') || day.closest('[role=dialog]')
    || day.closest('[aria-modal=true]') || day.parentElement);
  if (!scope) return null;
  const isVis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const NEG = /\b(no|under|exit|leave|cancel|decline|reject|back)\b/i;
  const POS = /(enter|submit|confirm|verify|continue|proceed|yes|i am|over 21|over 18|^ok$)/i;
  const btns = [...scope.querySelectorAll(
    "button[type=submit], button, [role=button], input[type=submit], input[type=button]")];
  for (const pred of [b => b.type === 'submit',
                      b => POS.test((b.innerText || b.value || '').trim())]) {
    for (const b of btns) {
      if (!isVis(b)) continue;
      const t = (b.innerText || b.value || '').trim();
      if (NEG.test(t)) continue;
      if (pred(b)) { b.click(); return t || b.type || 'submit'; }
    }
  }
  return null;
};
"""

# JS: for a yes/no style age overlay (no DOB form), click the affirmative button
# inside the detected overlay. Scoped + negative-guarded to avoid mis-clicks.
_AFFIRM_AGE_JS = r"""
() => {
  const isVis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const AGE = /(drinking age|legal age|verify your age|age verif|are you (over|of legal)|over 21|over 18|21 years|18 years|old enough|of legal drinking)/i;
  let scope = null;
  for (const el of document.querySelectorAll("[role=dialog], [aria-modal=true], div, section")) {
    if (!isVis(el)) continue;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    const isModal = el.getAttribute('role') === 'dialog'
      || el.getAttribute('aria-modal') === 'true' || s.position === 'fixed';
    if (isModal && r.width > window.innerWidth * 0.4 && AGE.test(el.innerText || '')) { scope = el; break; }
  }
  if (!scope) return null;
  const NEG = /\b(no|under|not of|exit|leave|cancel|decline)\b/i;
  const POS = /(yes|enter|i am (over|of legal|21|18)|i'?m over|over 21|over 18|21\+|18\+|of legal|enter site|continue|confirm|proceed)/i;
  for (const el of scope.querySelectorAll("button, a, [role=button], input[type=submit], input[type=button]")) {
    if (!isVis(el)) continue;
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
    if (!t || NEG.test(t)) continue;
    if (POS.test(t)) { el.click(); return t.slice(0, 40); }
  }
  return null;
};
"""


def _dob_field_selectors() -> list[tuple[str, list[str]]]:
    """(value, selectors) for each DOB field, most-specific selectors first."""
    dob = config.AGE_GATE_DOB
    return [
        (dob["year"], [
            "input[autocomplete='bday-year']", "input[name='year' i]",
            "input[aria-label*='year' i]", "input[placeholder='YYYY' i]",
        ]),
        (dob["month"], [
            "input[autocomplete='bday-month']", "input[name='month' i]",
            "input[aria-label*='month' i]", "input[placeholder='MM' i]",
        ]),
        (dob["day"], [
            "input[autocomplete='bday-day']", "input[name='day' i]",
            "input[aria-label*='day' i]", "input[placeholder='DD' i]",
        ]),
    ]


async def _fill_first(page, selectors: list[str], value: str) -> bool:
    """Fill the first visible input matching any selector. React-safe.

    Uses fill() (fast, dispatches input) then verifies the value stuck; if a
    controlled (e.g. react-aria) input rejected it, retries by typing keys.
    """
    for sel in selectors:
        loc = page.locator(sel)
        try:
            count = await loc.count()
        except Exception:
            continue
        for i in range(count):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                await el.fill(value, timeout=1_500)
                if (await el.input_value()) != value:        # controlled input?
                    await el.fill("", timeout=1_000)
                    await el.press_sequentially(value, delay=25, timeout=2_000)
                return True
            except Exception:
                continue
    return False


async def _pass_age_gate(page) -> str | None:
    """Detect and pass an age gate. Returns a short label of what we did, else None."""
    try:
        detect = await page.evaluate(_DETECT_AGE_GATE_JS)
    except Exception:
        return None
    if not (detect.get("hasDob") or detect.get("overlay")):
        return None

    acted: str | None = None
    if detect.get("hasDob"):
        filled = False
        for value, selectors in _dob_field_selectors():
            if await _fill_first(page, selectors, value):
                filled = True
        if filled:
            try:
                label = await page.evaluate(_SUBMIT_AGE_JS)
            except Exception:
                label = None
            if not label:  # no submit button found → press Enter in the year field
                try:
                    await page.locator(_dob_field_selectors()[0][1][0]).first.press("Enter")
                except Exception:
                    pass
            acted = f"dob+{label or 'enter'}"
    elif detect.get("overlay"):
        try:
            label = await page.evaluate(_AFFIRM_AGE_JS)
        except Exception:
            label = None
        if label:
            acted = f"confirm:{label}"

    if acted:
        try:
            await page.wait_for_load_state("networkidle", timeout=_SETTLE_TIMEOUT_MS)
        except Exception:
            pass
        await page.wait_for_timeout(400)
    return acted


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

async def handle_overlays(page) -> dict:
    """Accept cookies and pass an age gate on `page`, best-effort.

    Returns {'cookies': <label|None>, 'age_gate': <label|None>}. Never raises —
    the whole point is to be a no-op on pages without gates.
    """
    report = {"cookies": None, "age_gate": None}
    try:
        report["cookies"] = await _accept_cookies(page)
        report["age_gate"] = await _pass_age_gate(page)
        # An age gate can reveal a cookie banner that wasn't there before.
        if report["age_gate"] and not report["cookies"]:
            report["cookies"] = await _accept_cookies(page)
    except Exception:
        pass

    if report["cookies"] or report["age_gate"]:
        logger.info(
            "Overlays handled — cookies=%s age_gate=%s", report["cookies"], report["age_gate"]
        )
    return report
