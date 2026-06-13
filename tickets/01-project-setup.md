# 01 — Project Setup

**Implements:** spec §2 (Architecture), §7.1
**Status:** done

## Goal
Initialize the Python project so the app skeleton runs locally with one command. Establish the structure that tickets 02–05 fill in: a FastAPI app, a Playwright-based render module, an extraction module, and a place to serve the frontend.

## Scope
- `requirements.txt` pinning: `fastapi`, `uvicorn[standard]`, `playwright`, and an HTML→text cleaner lib (`beautifulsoup4` for boilerplate stripping + text flattening; `lxml` parser).
- Project layout:
  ```
  scraper/
    app/
      __init__.py
      main.py          # FastAPI app + static frontend mount (filled by 04/05)
      render.py        # Playwright fetch+render (filled by 02)
      extract.py       # color/font/text extraction (filled by 03)
    static/
      index.html       # the wired frontend (filled by 05; copied from reference)
    requirements.txt
    README.md          # (filled by 06)
  ```
- `app/main.py` skeleton: a FastAPI instance with a `GET /health` returning `{"ok": true}` so the skeleton is verifiably runnable before the real endpoint exists.
- Document the two install steps: `pip install -r requirements.txt` then `playwright install chromium`.

## Out of scope
- No endpoint logic, no Playwright calls, no frontend wiring yet (later tickets).

## Acceptance criteria
- [ ] `pip install -r requirements.txt` succeeds.
- [ ] `playwright install chromium` succeeds.
- [ ] `uvicorn app.main:app` boots without error and `GET /health` returns `{"ok": true}`.
- [ ] The folder structure above exists (empty/placeholder modules are fine).
