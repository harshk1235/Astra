"""Async HTTP client for the ClashKing API (also used for the official
Clash of Clans API, which has the same JSON-over-HTTPS shape plus a token).

Failure handling, in one place for every fetcher:
- At most `max_concurrency` requests in flight (don't hammer a free API).
- Retry with exponential backoff + jitter on timeouts, connection errors,
  429 (rate limited) and 5xx (server trouble). Honour Retry-After on 429.
- Don't retry other 4xx: a bad tag stays bad no matter how often we ask.
- Sleep between retries *outside* the concurrency slot, so one struggling
  request doesn't block the others.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.clashk.ing"


class ClashKingError(Exception):
    """The request failed and retrying won't help (or retries ran out)."""


class NotFound(ClashKingError):
    pass


class ClashKingClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        max_concurrency: int = 5,
        max_retries: int = 3,
        timeout_s: float = 15.0,
        backoff_base_s: float = 1.0,
        max_backoff_s: float = 30.0,
        session: aiohttp.ClientSession | None = None,
        headers: dict[str, str] | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self._base = base_url.rstrip("/")
        self._sem = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._backoff_base = backoff_base_s
        self._max_backoff = max_backoff_s
        self._session = session
        self._owns_session = session is None
        self._sleep = sleep
        self._jitter = jitter
        self._headers = {"User-Agent": "Astra-bot", **(headers or {})}

    async def __aenter__(self) -> "ClashKingClient":
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _backoff(self, attempt: int) -> float:
        # 1s, 2s, 4s, ... capped, plus up to one base unit of random jitter so
        # many failed requests don't all retry at the same instant.
        delay = min(self._backoff_base * (2 ** attempt), self._max_backoff)
        return delay + self._jitter() * self._backoff_base

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("Use 'async with ClashKingClient(...)' or pass a session")
        url = f"{self._base}/{path.lstrip('/')}"
        last_error = "unknown error"

        for attempt in range(self._max_retries + 1):
            retry_after: float | None = None
            async with self._sem:
                try:
                    async with self._session.get(url, params=params, timeout=self._timeout) as resp:
                        if resp.status == 404:
                            raise NotFound(f"404 Not Found: {path}")
                        if resp.status == 429 or resp.status >= 500:
                            last_error = f"HTTP {resp.status}"
                            if resp.status == 429:
                                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        elif resp.status >= 400:
                            raise ClashKingError(f"HTTP {resp.status} for {path}: {(await resp.text())[:200]}")
                        else:
                            return await resp.json(content_type=None)
                except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
                    last_error = f"{type(e).__name__}: {e}"

            if attempt == self._max_retries:
                break
            delay = retry_after if retry_after is not None else self._backoff(attempt)
            log.warning("ClashKing %s failed (%s); retry %d/%d in %.1fs",
                        path, last_error, attempt + 1, self._max_retries, delay)
            await self._sleep(delay)

        raise ClashKingError(f"{path} failed after {self._max_retries + 1} attempts: {last_error}")


def _parse_retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None  # HTTP-date form; fall back to our own backoff


OFFICIAL_API_URL = "https://api.clashofclans.com/v1"


def official_client(token: str, base_url: str = OFFICIAL_API_URL) -> ClashKingClient:
    """Client for the official API. Its keys are tied to the server's IP
    address, which is why it's only usable from a host with a fixed IP (EC2
    with an Elastic IP); ClashKing needs no key."""
    return ClashKingClient(base_url, headers={"Authorization": f"Bearer {token}"}, max_concurrency=10)
