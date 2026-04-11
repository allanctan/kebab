"""Web scraping via httpx + BeautifulSoup. Sync — CLI-first."""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from app.core.errors import IngestError

logger = logging.getLogger(__name__)

_USER_AGENT = "KEBAB/0.1 (https://github.com/kebab-kb; kebab@kebab.local)"

_client = httpx.Client(
    timeout=30.0,
    follow_redirects=True,
    headers={"User-Agent": _USER_AGENT},
)


def fetch(url: str) -> tuple[str, str]:
    """Fetch ``url`` and return ``(raw_html, cleaned_text)``."""
    try:
        response = _client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise IngestError(f"failed to fetch {url}: {exc}") from exc
    html = response.text
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    return html, text
