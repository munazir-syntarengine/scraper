"""FastAPI app for the Brand Website Scraper demo.

Wires the render (02) + extract (03) modules behind `POST /analyze`, returning
the section-3 contract, and serves the frontend from the same process so the
whole demo runs as one `uvicorn` command.

  GET  /health   liveness probe
  POST /analyze  { "url": ... } -> { source_url, text, colors, fonts }
  GET  /         the frontend (static/index.html, wired in ticket 05)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.crawl import crawl
from app.render import RenderError

logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="Brand Website Scraper — demo",
    description="Render a page and extract its brand colors, fonts, and clean text.",
)


@app.on_event("startup")
async def _startup_banner() -> None:
    """Log the render engine + active mode so the operator sees the config."""
    logger.info("Render engine: Playwright + headless Chromium.")
    if config.MODE == "polite":
        logger.info("MODE=polite — honest UA, robots.txt respected, per-host paced.")
    elif config.MODE == "stealth":
        logger.warning("MODE=stealth requested but GATED — /analyze will error. %s",
                       config.STEALTH_GATED_MESSAGE)
    else:
        logger.warning("Unknown MODE=%r — /analyze will error. Set MODE=polite.",
                       config.MODE)

# Resolve the static dir relative to this file so it works regardless of CWD.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


# ─────────────────────────────────────────────────────────────────────────────
# Contract models (spec §3)
# ─────────────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    url: str


class Fonts(BaseModel):
    heading: Optional[str] = None
    body: Optional[str] = None


class PageRef(BaseModel):
    role: str  # "home" | "about" | "products"
    url: str


class AnalyzeResponse(BaseModel):
    source_url: str
    text: str
    colors: list[str]
    fonts: Fonts
    pages: list[PageRef]


class ErrorResponse(BaseModel):
    error: str


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"ok": True}


@app.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={502: {"model": ErrorResponse}},
)
async def analyze(req: AnalyzeRequest):
    """Crawl the homepage + best about + best products page, return the contract.

    Best-effort: sub-pages and sub-extractions that fail are skipped rather than
    failing the request. Only an unreadable homepage (blocked, timed out, empty)
    returns a 502 `{ "error": ... }` the frontend maps to its error state.
    """
    try:
        result = await crawl(req.url)
    except RenderError as exc:
        return JSONResponse(status_code=502, content={"error": exc.message})
    except Exception:
        # Never leak a raw 500 to the UI — any failure to read the site becomes
        # the clean error state.
        return JSONResponse(
            status_code=502, content={"error": "could not read this site"}
        )

    return AnalyzeResponse(
        source_url=result["source_url"],
        text=result["text"],
        colors=result["colors"],
        fonts=Fonts(**result["fonts"]),
        pages=[PageRef(**p) for p in result["pages"]],
    )


@app.get("/", include_in_schema=False)
async def index():
    """Serve the frontend. Until ticket 05 drops in the wired index.html, show a
    short placeholder rather than a 404."""
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return HTMLResponse(
        "<p style='font-family:sans-serif;padding:40px'>"
        "Frontend not wired yet (ticket 05). The API is live at "
        "<code>POST /analyze</code>.</p>"
    )


# Serve any sibling assets the frontend might add later (the reference HTML is
# self-contained, so this is just for completeness / future use).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
