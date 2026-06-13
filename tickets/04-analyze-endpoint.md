# 04 — Analyze Endpoint

**Implements:** spec §2, §3 (output contract), §7.4
**Status:** done

## Goal
Wire fetch+render (02) and extraction (03) behind `POST /analyze`, returning exactly the section-3 contract. Best-effort partial success; a clean error response only when the page genuinely can't be read. Serve the frontend from FastAPI so it's one process.

## Scope
- `POST /analyze` request body: `{ "url": "string" }`.
- Success response (HTTP 200):
  ```json
  {
    "source_url": "string",
    "text": "string",
    "colors": ["#HEXCODE", "..."],
    "fonts": { "heading": "string | null", "body": "string | null" }
  }
  ```
- **Best-effort partial success:** if a sub-extraction fails or returns nothing, return what succeeded with `null`/`[]`/`""` for the rest — do NOT error the whole request (spec §3).
- **Error response:** only when the page genuinely can't be read (`RenderError` from 02 — blocked / timeout / empty). Return a non-200 (e.g. 502) with a small `{ "error": "..." }` body the frontend maps to the error state.
- Use a Pydantic model for request and response so the contract is enforced.
- **Frontend serving:** mount `static/` and serve `index.html` at `GET /` (single-process, simplest). CORS only needed if the frontend is served separately — prefer static-serve.

## Out of scope
- No caching, queue, DB, auth (spec §6).
- No SSRF validation (spec §6).

## Acceptance criteria
- [ ] `POST /analyze {"url": "<real brand site>"}` returns the section-3 contract JSON with populated fields.
- [ ] A blocked/timeout URL returns the error shape (non-200 + `error` message), not a 500 traceback.
- [ ] A page where (e.g.) the heading font can't be determined still returns 200 with `fonts.heading: null` and the rest populated.
- [ ] `GET /` serves the frontend; `GET /health` still works.
