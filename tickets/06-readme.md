# 06 — README

**Implements:** spec §7.6, §8 (done = demoable), §6 (honest framing) + ticket 07 (crawl behavior)
**Status:** done

## Goal
A README that lets a fresh clone run the demo from the document alone, and frames honestly — for Michael — what this proves and what it deliberately does not.

## Scope
- **Setup:**
  1. `pip install -r requirements.txt`
  2. `playwright install chromium`
- **Run:** the one documented command (e.g. `uvicorn app.main:app --reload`), then open `http://localhost:8000`.
- **Example:** paste a real brand URL (e.g. the reference's `https://www.theglenlivet.com`), click Analyze, see palette + fonts + cleaned text.
- **What this proves:** the extraction technique works — real computed brand colors, heading/body fonts, and clean rendered text pulled live from a headless-rendered page.
- **What this does NOT prove / out of scope (spec §6), stated honestly:**
  - No SSRF / URL validation — test URLs are trusted operator input; this is a known production requirement deliberately omitted.
  - No anti-bot / Cloudflare / CAPTCHA handling, and no bot-evasion — a blocking site simply shows the error state.
  - No caching, DB, queue, Temporal, synthesis, Cora.
  - No crawling beyond the single given page; no product-catalog extraction.
  - These production concerns are the real-build follow-on.

## Out of scope
- No CI, Docker, or deploy docs (it's a local demo).

## Acceptance criteria
- [ ] A fresh clone can install, render, and run the demo following ONLY the README.
- [ ] The run command and example URL are correct and copy-pasteable.
- [ ] The "what this proves / does not prove" framing is present and matches spec §6 and §8.
