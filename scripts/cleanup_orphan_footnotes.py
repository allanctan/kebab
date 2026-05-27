"""One-off cleanup: remove orphan footnote definitions from curated articles.

An *orphan* footnote definition is a line ``[^N]: ...`` that has no
matching inline ``[^N]`` reference anywhere else in the body. These were
left behind by research-agent runs before the writer's orphan-fix commit
(``a8417c0``, 2026-05-24).

The script is safe by default (``--dry-run`` shows what would change).
Pass ``--apply`` to actually rewrite files.

Usage::

    uv run python scripts/cleanup_orphan_footnotes.py             # dry-run
    uv run python scripts/cleanup_orphan_footnotes.py --apply     # apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CURATED = Path("knowledge/curated")


def find_orphans(body: str) -> list[int]:
    """Return footnote numbers defined but never referenced inline."""
    defs: set[int] = set()
    refs: set[int] = set()

    for m in re.finditer(r"^\[\^(\d+)\]:", body, re.M):
        defs.add(int(m.group(1)))

    for m in re.finditer(r"\[\^(\d+)\]", body):
        start = m.start()
        line_start = body.rfind("\n", 0, start) + 1
        prefix = body[line_start:start]
        suffix = body[m.end() : m.end() + 2]
        if prefix.strip() == "" and suffix == ": ":
            continue  # this is a definition, not a reference
        refs.add(int(m.group(1)))

    return sorted(defs - refs)


def strip_orphans(body: str, orphan_nums: list[int]) -> str:
    """Remove the ``[^N]: ...`` lines for the given numbers.

    The match is line-anchored. The orphan number is the only one removed
    — surviving footnotes keep their existing numbering (no renumbering).
    """
    out_lines: list[str] = []
    for line in body.splitlines(keepends=True):
        stripped = line.lstrip()
        m = re.match(r"\[\^(\d+)\]:", stripped)
        if m and int(m.group(1)) in orphan_nums:
            continue
        out_lines.append(line)
    return "".join(out_lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write changes back. Default is dry-run.",
    )
    args = p.parse_args()

    affected = 0
    removed = 0
    for path in sorted(CURATED.rglob("*.md")):
        body = path.read_text(encoding="utf-8")
        orphans = find_orphans(body)
        if not orphans:
            continue
        affected += 1
        removed += len(orphans)
        rel = path.relative_to(CURATED.parent)
        print(f"{rel}  →  remove {orphans}")
        if args.apply:
            new_body = strip_orphans(body, orphans)
            path.write_text(new_body, encoding="utf-8")

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print()
    print(f"[{mode}] {affected} files, {removed} orphan footnote defs")
    if not args.apply:
        print("Re-run with --apply to actually remove them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
