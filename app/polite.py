"""Polite-scraping policy (ticket 09, MODE=polite — the default).

The opposite of evasion: identify the bot honestly, respect each host's
robots.txt, pace requests per host, cap concurrency, and back off when asked.

Trade-off (by design): an honest UA + robots compliance means MORE sites may
legitimately block the demo. That is correct, respectful behavior.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


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
    def context_args(self) -> dict:
        return {"user_agent": self.user_agent}

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(url).netloc

    async def _get_robots(self, host: str) -> RobotFileParser | None:
        """Fetch + cache robots.txt for a host (off-thread; never blocks the loop)."""
        if host in self._robots:
            return self._robots[host]
        rp: RobotFileParser | None = RobotFileParser()
        rp.set_url(f"https://{host}/robots.txt")
        try:
            await asyncio.to_thread(rp.read)
        except Exception:
            rp = None  # robots unreachable → treat as allowed, but stay gentle
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
