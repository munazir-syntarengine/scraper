"""Polite-scraping policy (ticket 09, MODE=polite — the default).

The opposite of evasion: identify the bot honestly, respect each host's
robots.txt, pace requests per host, cap concurrency, and back off when asked.

Trade-off (by design): an honest UA + robots compliance means MORE sites may
legitimately block the demo. That is correct, respectful behavior.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app import config

# Timeout for the robots.txt fetch (off-thread).
_ROBOTS_TIMEOUT = 10


class Politeness:
    """Robots cache + per-host rate limit + concurrency cap + honest UA."""

    def __init__(self, user_agent: str, min_delay: float, max_concurrency: int):
        self.user_agent = user_agent
        self.min_delay = min_delay
        self.sem = asyncio.Semaphore(max_concurrency)
        self._last: dict[str, float] = {}            # host -> last request monotonic time
        self._locks: dict[str, asyncio.Lock] = {}    # host -> pacing lock
        self._robots: dict[str, RobotFileParser | None] = {}  # host -> parsed robots

    # The browser context advertises this UA — no masking, no random browser UA.
    # The context viewport (config.pick_viewport) is the crawl's default size;
    # individual pages re-roll their own via next_viewport() below, so the pages
    # of one crawl render at varying real desktop sizes. Not evasion — just a
    # representative layout.
    def context_args(self) -> dict:
        return {"user_agent": self.user_agent, "viewport": config.pick_viewport()}

    # A fresh viewport for a single page. Re-rolled per page (render.py applies
    # it with page.set_viewport_size before navigating) so the homepage and each
    # subpage of one crawl can render at different real desktop sizes.
    def next_viewport(self) -> dict:
        return config.pick_viewport()

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(url).netloc

    def _fetch_robots(self, host: str) -> tuple[int | None, str]:
        """Fetch robots.txt with OUR honest UA. Returns (status, body).

        Status is None on a network/transport error. Fetching with our declared
        UA (not urllib's default `Python-urllib/x`) is both more polite and more
        accurate — many CDNs 403 the default UA, which urllib would then misread
        as a blanket disallow.
        """
        req = urllib.request.Request(
            f"https://{host}/robots.txt", headers={"User-Agent": self.user_agent}
        )
        try:
            with urllib.request.urlopen(req, timeout=_ROBOTS_TIMEOUT) as resp:
                return resp.status, resp.read(1_000_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            return err.code, ""
        except Exception:
            return None, ""

    async def _get_robots(self, host: str) -> RobotFileParser | None:
        """Fetch + cache robots.txt for a host (off-thread; never blocks the loop).

        Status handling follows RFC 9309 §2.3.1: a 4xx (absent/forbidden
        robots.txt, including 404/403) means "no restrictions" → allow all; a
        5xx (server error/unreachable) means "assume complete disallow"; a 2xx
        is parsed for real rules. A transport error → None (allowed, stay gentle).
        """
        if host in self._robots:
            return self._robots[host]

        status, body = await asyncio.to_thread(self._fetch_robots, host)
        rp: RobotFileParser | None = RobotFileParser()
        rp.set_url(f"https://{host}/robots.txt")
        if status is None:
            rp = None                                   # transport error → allowed
        elif 400 <= status < 500:
            rp.allow_all = True                         # RFC 9309: 4xx → allow all
        elif status >= 500:
            rp.disallow_all = True                      # RFC 9309: 5xx → disallow all
        else:
            rp.parse(body.splitlines())                 # 2xx/3xx → real rules

        self._robots[host] = rp
        return rp

    async def allowed(self, url: str) -> bool:
        """True if robots.txt permits our UA to fetch `url` (or no robots exists)."""
        rp = await self._get_robots(self._host(url))
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def _delay_for(self, host: str) -> float:
        """Min delay for a host: configured floor, raised by robots Crawl-delay."""
        delay = self.min_delay
        rp = self._robots.get(host)
        if rp is not None:
            try:
                crawl_delay = rp.crawl_delay(self.user_agent)
                if crawl_delay:
                    delay = max(delay, float(crawl_delay))
            except Exception:
                pass
        return delay

    async def wait_turn(self, url: str) -> None:
        """Block until it's polite to hit this host (concurrency cap + min delay)."""
        host = self._host(url)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with self.sem:               # concurrency cap
            async with lock:               # serialize same-host pacing
                delay = self._delay_for(host)
                gap = time.monotonic() - self._last.get(host, 0.0)
                if gap < delay:
                    await asyncio.sleep(delay - gap)
                self._last[host] = time.monotonic()
