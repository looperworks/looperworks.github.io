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
HOMEPAGE = REPO / "index.html"
ABOUT = REPO / "about" / "index.html"
CHEAT_SHEET = REPO / "application-timeline" / "cheat-sheet" / "index.html"


def load_firms() -> list[dict]:
    text = FIRMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const firms = (\[.*\]);?\s*$", text, re.DOTALL)
    if not m:
        sys.exit("could not parse firms.js")
    return json.loads(m.group(1))


def main() -> int:
    all_firms = load_firms()
    # The hardcoded marketing copy is US-only: the hero badge says "U.S.
    # FIRMS" and the modal says "50 States". International firms carry a
    # `country` field and must not move those numbers. They live in firms.js
    # for the map (and get their own Country filter), but the counts below
    # are computed from the US subset only.
    firms = [f for f in all_firms if not f.get("country")]
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
    # Rounded for body copy that doesn't need exact counts. Floors to the
    # nearest 100. fmt_rounded_bare for "over X,X00" patterns; fmt_rounded
    # for "X,X00+" patterns. e.g. 2,187 -> "2,100" or "2,100+".
    rounded_total = (total // 100) * 100
    fmt_rounded_bare = f"{rounded_total:,}"
    fmt_rounded = f"{rounded_total:,}+"

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

    # ── Homepage SEO paragraph (hidden inside .sr-only block) ──
    home_html = HOMEPAGE.read_text(encoding="utf-8")
    home_original = home_html
    home_html = re.sub(
        r"(Browse )[\d,]+( firms across all 50 states)",
        rf"\g<1>{fmt_total}\g<2>",
        home_html, count=1,
    )
    if home_html != home_original:
        HOMEPAGE.write_text(home_html, encoding="utf-8")
        print(f"✓ updated {HOMEPAGE.relative_to(REPO)}")
    else:
        print(f"  no changes needed in {HOMEPAGE.relative_to(REPO)}")

    # ── About page body copy: "over X,X00 verified firms" ──
    if ABOUT.exists():
        about_html = ABOUT.read_text(encoding="utf-8")
        about_original = about_html
        about_html = re.sub(
            r"(with over )[\d,]+\+?( verified firms across all 50 states)",
            rf"\g<1>{fmt_rounded_bare}\g<2>",
            about_html, count=1,
        )
        if about_html != about_original:
            ABOUT.write_text(about_html, encoding="utf-8")
            print(f"✓ updated {ABOUT.relative_to(REPO)}")
        else:
            print(f"  no changes needed in {ABOUT.relative_to(REPO)}")

    # ── Application timeline cheat sheet: "Explore X,X00+ firms" ──
    if CHEAT_SHEET.exists():
        cheat_html = CHEAT_SHEET.read_text(encoding="utf-8")
        cheat_original = cheat_html
        cheat_html = re.sub(
            r"(Explore )[\d,]+\+( firms on our)",
            rf"\g<1>{fmt_rounded}\g<2>",
            cheat_html, count=1,
        )
        if cheat_html != cheat_original:
            CHEAT_SHEET.write_text(cheat_html, encoding="utf-8")
            print(f"✓ updated {CHEAT_SHEET.relative_to(REPO)}")
        else:
            print(f"  no changes needed in {CHEAT_SHEET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
