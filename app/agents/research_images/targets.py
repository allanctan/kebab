"""Find Wikipedia article targets in a curated article body.

Pure regex over the body's existing footnote definitions. The list of
``WikiTarget`` objects this returns is the input to the rest of the
research-images pipeline. No I/O, no LLM calls.

Because targets come from existing footnotes, ``research-images`` requires
that ``research`` (claim verification) has run on the article at least
once. Articles with no Wikipedia footnotes get no images.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

# Matches footnote defs of the form:
#   [^N]: [Title](https://en.wikipedia.org/wiki/<encoded title>)
# Tolerates an inline source-id prefix the writer sometimes adds:
#   [^N]: [42] [Title](https://en.wikipedia.org/wiki/<encoded title>)
_WIKI_FOOTNOTE_RE = re.compile(
    r"^\[\^\d+\]:\s.*?\[(?P<title>[^\]]+)\]\((?P<url>https?://en\.wikipedia\.org/wiki/[^)]+)\)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class WikiTarget:
    """One Wikipedia article candidate to fetch images from."""

    title: str  # Decoded from the URL path; matches what the wiki adapter expects.
    url: str    # The canonical Wikipedia URL as it appears in the body.


def extract_wikipedia_targets(body: str) -> list[WikiTarget]:
    """Return distinct Wikipedia targets parsed from footnote definitions.

    Order: first-occurrence wins. Duplicates (by URL) are dropped while
    preserving the order they first appear in the body.
    """
    seen_urls: set[str] = set()
    targets: list[WikiTarget] = []
    for match in _WIKI_FOOTNOTE_RE.finditer(body):
        url = match.group("url")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Decode the title from the URL path (the footnote display title may
        # have been edited by hand and is less reliable). The wiki adapter's
        # locator format is the human-readable title with spaces.
        slug = url.rsplit("/wiki/", 1)[-1]
        title = unquote(slug).replace("_", " ")
        targets.append(WikiTarget(title=title, url=url))
    return targets


__all__ = ["WikiTarget", "extract_wikipedia_targets"]
