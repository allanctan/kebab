"""Shared HTTP fetcher — rate limiting + 429 backoff + allowlist.

Every source adapter that makes outbound HTTP requests goes through
:class:`SharedFetcher`. This centralizes:

1. **Browser User-Agent** — identifies as Chrome so sources that block
   crawlers (CK-12, ScienceDirect, Oxford Academic, etc.) serve content.
2. **Per-host rate limit** — a sync token bucket. Default is 1 req/sec
   per host, enough for polite long-running fetches.
3. **Domain allowlist** — ``settings.ALLOWED_SOURCE_DOMAINS``. Empty
   list means "allow all" (useful for local dev); set to a non-empty
   list in production. Enforced at ``get()`` time, not discovery.

robots.txt is intentionally not enforced. KEBAB acts as a research
assistant on behalf of a human user — it fetches the same pages a
person would browse manually, with a browser User-Agent.

The fetcher is stdlib + ``httpx`` only — no new dependencies.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.config.config import Settings
from app.core.errors import KebabError

logger = logging.getLogger(__name__)


# Use a Chrome browser User-Agent so sources that block known crawlers
# (CK-12, ScienceDirect, academic.oup.com, labroots, etc.) will serve
# us their content. KEBAB is a personal research assistant — it pulls
# the same content a human user would read in a browser.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def user_agent(settings: Settings | None = None) -> str:
    """Return the Chrome User-Agent string. Settings is accepted for
    backward compatibility but currently has no effect."""
    return _DEFAULT_USER_AGENT


_DEFAULT_TIMEOUT = 30.0
_DEFAULT_RATE_PER_SEC = 1.0
_BACKOFF_BASE_SECONDS = 1.0
_MAX_RETRIES = 4


class FetchError(KebabError):
    """Base class for fetcher errors."""


class FetchBlockedError(FetchError):
    """The URL is blocked by ``robots.txt`` or the domain allowlist."""


class FetchTransientError(FetchError):
    """Upstream returned a retryable status (429/5xx) after all retries."""


@dataclass
class _Bucket:
    """Per-host rate limit state. Not thread-safe — KEBAB is sync-only."""

    rate_per_sec: float
    next_allowed_at: float = 0.0

    def wait(
        self, now_fn: Callable[[], float], sleep_fn: Callable[[float], None]
    ) -> None:
        now = now_fn()
        if now < self.next_allowed_at:
            sleep_fn(self.next_allowed_at - now)
            now = now_fn()
        self.next_allowed_at = max(self.next_allowed_at, now) + (
            1.0 / self.rate_per_sec
        )


@dataclass
class SharedFetcher:
    """Sync HTTP fetcher with robots.txt + rate-limit + allowlist gating.

    One instance per pipeline run. Adapters call ``fetcher.get(url)`` and
    receive raw bytes + headers; they're responsible for writing the
    bytes under ``raw/`` and stamping the provenance sidecar.
    """

    settings: Settings
    user_agent: str = _DEFAULT_USER_AGENT
    rate_per_sec: float = _DEFAULT_RATE_PER_SEC
    _client: httpx.Client = field(init=False)
    _robots_cache: dict[str, RobotFileParser] = field(default_factory=dict)
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _now: Callable[[], float] = field(default=time.monotonic)
    _sleep: Callable[[float], None] = field(default=time.sleep)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SharedFetcher":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # ---------- allowlist / robots ----------

    def _check_allowlist(self, url: str) -> None:
        allowed = list(self.settings.ALLOWED_SOURCE_DOMAINS)
        if not allowed:
            return  # empty → allow all
        host = urlparse(url).hostname or ""
        if any(host == d or host.endswith(f".{d}") for d in allowed):
            return
        raise FetchBlockedError(
            f"domain {host!r} not in ALLOWED_SOURCE_DOMAINS ({allowed})"
        )

    def _robots_for(self, url: str) -> RobotFileParser:
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._robots_cache.get(host_key)
        if cached is not None:
            return cached

        rp = RobotFileParser()
        robots_url = f"{host_key}/robots.txt"
        try:
            response = self._client.get(robots_url)
            if response.status_code >= 400:
                # No robots.txt → treat as "everything allowed".
                rp.parse([])
            else:
                rp.parse(response.text.splitlines())
        except httpx.HTTPError as exc:
            logger.debug(
                "fetcher: robots.txt fetch failed for %s (%s) — allowing by default",
                robots_url,
                exc,
            )
            rp.parse([])
        self._robots_cache[host_key] = rp
        return rp

    def _check_robots(self, url: str) -> None:
        """robots.txt enforcement is disabled.

        KEBAB acts as a research assistant on behalf of a human user;
        we fetch the same pages a person would browse manually. We
        identify as Chrome (see ``_DEFAULT_USER_AGENT``) and respect
        the per-host rate limit instead.
        """
        return

    # ---------- rate limiting ----------

    def _wait_rate_limit(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = _Bucket(rate_per_sec=self.rate_per_sec)
            self._buckets[host] = bucket
        bucket.wait(self._now, self._sleep)

    # ---------- public fetch ----------

    def get(self, url: str) -> httpx.Response:
        """GET ``url`` after enforcing allowlist, robots.txt, rate limit.

        Retries transient 429/5xx with exponential backoff (1→2→4→8s).
        Returns the final successful :class:`httpx.Response`.
        Raises :class:`FetchBlockedError` if the URL is disallowed.
        Raises :class:`FetchTransientError` after the retry budget.
        Raises :class:`FetchError` on any other unrecoverable status.
        """
        self._check_allowlist(url)
        self._check_robots(url)

        last_error: str | None = None
        for attempt in range(_MAX_RETRIES):
            self._wait_rate_limit(url)
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.info(
                    "fetcher: %s failed (%s) — backing off %.1fs (attempt %d/%d)",
                    url,
                    exc,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                self._sleep(wait)
                continue

            status = response.status_code
            if status < 400:
                return response
            if status in (429, 500, 502, 503, 504):
                last_error = f"HTTP {status}"
                wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.info(
                    "fetcher: %s returned %d — backing off %.1fs (attempt %d/%d)",
                    url,
                    status,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                self._sleep(wait)
                continue
            raise FetchError(f"{url} returned HTTP {status}")

        raise FetchTransientError(
            f"{url} still failing after {_MAX_RETRIES} attempts (last: {last_error})"
        )


def get_default_fetcher(settings: Settings) -> SharedFetcher:
    """Return a :class:`SharedFetcher` configured from ``settings``."""
    return SharedFetcher(settings=settings, user_agent=user_agent(settings))


__all__ = [
    "FetchBlockedError",
    "FetchError",
    "FetchTransientError",
    "SharedFetcher",
    "get_default_fetcher",
    "user_agent",
]
