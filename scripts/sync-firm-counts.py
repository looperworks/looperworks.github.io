#!/usr/bin/env python3
"""sync-firm-counts.py — keep static firm counts in sync with mapvoid/firms.js

Reads firms.js, computes total + per-discipline counts + state count, then
updates every hardcoded count in the static HTML. Idempotent — safe to run
on every commit.

Run after editing firms.js:
  python3 scripts/sync-firm-counts.py

Or wire as a pre-commit hook:
  echo "python3 scripts/sync-firm-counts.py" > .git/hooks/pre-commit
  chmod +x .git/hooks/pre-commit
"""
from __future__ import annotations
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIRMS_JS = REPO / "mapvoid" / "firms.js"
MAPVOID = REPO / "mapvoid" / "index.html"


def load_firms() -> list[dict]:
    text = FIRMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const firms = (\[.*\]);?\s*$", text, re.DOTALL)
    if not m:
        sys.exit("could not parse firms.js")
    return json.loads(m.group(1))


def main() -> int:
    firms = load_firms()
    total = len(firms)
    disc = Counter(f["discipline"] for f in firms)
    # "States" count for marketing copy excludes DC + PR (which are
    # represented in firms.js but aren't states). Hero badge says "ALL 50
    # STATES" — modal stat should match the same convention.
    all_state_codes = set(f["state"] for f in firms)
    states = len(all_state_codes - {"DC", "PR"})

    n_arch = disc.get("architecture", 0)
    n_land = disc.get("landscape", 0)
    n_multi = disc.get("multi", 0)
    n_urban = disc.get("urban", 0)

    fmt_total = f"{total:,}"

    print(f"Firm counts:")
    print(f"  total:          {fmt_total}")
    print(f"  architecture:   {n_arch:,}")
    print(f"  multidiscipl.:  {n_multi:,}")
    print(f"  landscape:      {n_land:,}")
    print(f"  urban design:   {n_urban:,}")
    print(f"  states:         {states}")
    print()

    html = MAPVOID.read_text(encoding="utf-8")
    original = html

    # 1) Hero badge
    html = re.sub(
        r'(<div class="hero-badge">FREE RESOURCE // )[\d,]+( U\.S\. FIRMS // ALL 50 STATES</div>)',
        rf'\g<1>{fmt_total}\g<2>',
        html, count=1,
    )

    # 2) Hero subtitle (any number prefix on "architecture, landscape, ...")
    html = re.sub(
        r'(<p class="subtitle">Search )[\d,]+( architecture, landscape, and multidisciplinary firms)',
        rf'\g<1>{fmt_total}\g<2>',
        html, count=1,
    )

    # 3) Data banner: "X firms across Architecture (a), Multidisciplinary (m), and Landscape Architecture (l)"
    banner_pattern = (
        r'([\d,]+ firms across Architecture \()[\d,]+(\), Multidisciplinary \()[\d,]+(\), and Landscape Architecture \()[\d,]+(\))'
    )
    if re.search(banner_pattern, html):
        html = re.sub(
            r'[\d,]+( firms across Architecture \()[\d,]+(\), Multidisciplinary \()[\d,]+(\), and Landscape Architecture \()[\d,]+(\))',
            rf'{fmt_total}\g<1>{n_arch:,}\g<2>{n_multi:,}\g<3>{n_land:,}\g<4>',
            html, count=1,
        )

    # 4) Welcome-modal static fallback for #aboutStatFirms
    html = re.sub(
        r'(<strong id="aboutStatFirms">)[\d,]+(</strong>)',
        rf'\g<1>{fmt_total}\g<2>',
        html, count=1,
    )
    html = re.sub(
        r'(<strong id="aboutStatStates">)\d+(</strong>)',
        rf'\g<1>{states}\g<2>',
        html, count=1,
    )

    if html != original:
        MAPVOID.write_text(html, encoding="utf-8")
        print(f"✓ updated {MAPVOID.relative_to(REPO)}")
    else:
        print(f"  no changes needed in {MAPVOID.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
