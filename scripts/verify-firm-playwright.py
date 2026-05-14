#!/usr/bin/env python3
"""
verify-firm-playwright.py

Second-pass content verification using a real headless Chromium that waits
for the page to fully render (post-JS) and reads:

  - Rendered <title>, all visible body text
  - Every <img alt="..."> attribute (firm names often appear only in logos)
  - <meta name="description"> and <meta property="og:*"> tags
  - The page's final URL (after JS-driven redirects too)

For each firm in the NAME_MISMATCH list from the first content audit, this
script reloads with Playwright and checks again. Many "false positives"
from the urllib pass disappear here because firms commonly put their name
in a logo image's alt tag, or use a sparse splash page that hydrates with
JavaScript.

Output: a TSV at ../FIRM_PLAYWRIGHT_REVIEW.tsv listing only firms whose
name still doesn't appear anywhere after full rendering. Also writes a
screenshot for each to /tmp/firm-shots/<id>.png so I can review visually.

Usage:
  python3 scripts/verify-firm-playwright.py --input ../FIRM_CONTENT_AUDIT.md
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMS_JS = REPO_ROOT / "mapvoid" / "firms.js"
DEFAULT_INPUT = REPO_ROOT.parent / "FIRM_CONTENT_AUDIT.md"
DEFAULT_OUTPUT = REPO_ROOT.parent / "FIRM_PLAYWRIGHT_REVIEW.tsv"
SHOTS_DIR = Path("/tmp/firm-shots")

GENERIC_WORDS = {
    "architecture", "architects", "design", "designs", "studio", "studios",
    "group", "associates", "inc", "llc", "plc", "co", "company", "partners",
    "partnership", "and", "the", "of", "for", "an", "a", "&", "+", "/", "-",
    "ny", "la", "nyc", "new", "york", "york.", "ltd", "limited", "office",
    "offices", "corporation", "corp", "consulting", "consultants",
    "international", "p.c.", "pc", "pllc", "us", "usa",
}


def load_firms() -> dict[int, dict]:
    content = FIRMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const firms = (\[.*\]);?\s*$", content, re.DOTALL)
    if not m:
        sys.exit(f"could not parse {FIRMS_JS}")
    return {f["id"]: f for f in json.loads(m.group(1))}


def parse_audit_warnings(path: Path) -> list[dict]:
    """Pull rows from the 'Possible mismatches' table of the content audit."""
    rows = []
    in_warn = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Possible mismatches"):
            in_warn = True
            continue
        if line.startswith("## ") and in_warn:
            break
        if not (in_warn and line.startswith("|")):
            continue
        if line.startswith("|---") or line.startswith("| # "):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 7:
            continue
        try:
            fid = int(parts[0])
        except ValueError:
            continue
        rows.append({
            "id": fid,
            "name": parts[1],
            "city": parts[2],
            "url": parts[3],
            "reason": parts[6],
        })
    return rows


def significant_words(name: str) -> list[str]:
    n = name.lower()
    n = re.sub(r"[^a-z0-9\s&+/-]", " ", n)
    out, seen = [], set()
    for w in n.split():
        if w in GENERIC_WORDS or len(w) < 3 or w in seen:
            continue
        out.append(w)
        seen.add(w)
    return out


def check_with_browser(rows: list[dict], firms_by_id: dict[int, dict], timeout_s: int = 25) -> list[dict]:
    from playwright.sync_api import sync_playwright

    SHOTS_DIR.mkdir(exist_ok=True, parents=True)
    flagged: list[dict] = []
    total = len(rows)
    print(f"opening Chromium, processing {total} entries...", file=sys.stderr)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            ignore_https_errors=True,
        )
        try:
            for idx, row in enumerate(rows, 1):
                firm = firms_by_id.get(row["id"])
                if not firm:
                    continue
                url = (firm.get("website") or "").strip()
                if not url:
                    continue
                page = ctx.new_page()
                try:
                    resp = page.goto(url, timeout=timeout_s * 1000, wait_until="networkidle")
                except Exception:
                    try:
                        # Fall back to domcontentloaded if networkidle never settles
                        resp = page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
                    except Exception:
                        page.close()
                        continue

                if resp is None or resp.status >= 400:
                    page.close()
                    continue

                final_url = page.url
                # Pull a rich text bundle
                try:
                    title = (page.title() or "").strip()
                    # All body text
                    body_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                    # All image alt attributes
                    alts = page.evaluate("() => Array.from(document.images).map(i => i.alt || '').join(' ')") or ""
                    # Meta description + og:* + twitter:*
                    metas = page.evaluate("""() => {
                        const out = [];
                        document.querySelectorAll('meta').forEach(m => {
                            const c = m.getAttribute('content') || '';
                            if (c) out.push(c);
                        });
                        return out.join(' ');
                    }""") or ""
                except Exception:
                    title = body_text = alts = metas = ""

                haystack = (title + " " + body_text[:12000] + " " + alts + " " + metas + " " + final_url).lower()
                sigs = significant_words(firm["name"])

                hits = [w for w in sigs if w in haystack] if sigs else []
                if hits:
                    # The firm name IS on the page (alt tags, meta, etc.). Drop from review.
                    page.close()
                    continue

                # Real miss after full rendering. Screenshot it and log.
                shot_path = SHOTS_DIR / f"{firm['id']}.png"
                try:
                    page.screenshot(path=str(shot_path), full_page=False)
                except Exception:
                    pass

                flagged.append({
                    "id": firm["id"],
                    "name": firm["name"],
                    "city": firm["city"],
                    "state": firm["state"],
                    "url": url,
                    "final_url": final_url,
                    "title": title[:120],
                    "preview": (body_text[:200] if body_text else "").replace("\n", " ").replace("\t", " "),
                    "screenshot": str(shot_path),
                })
                page.close()

                if idx % 25 == 0:
                    print(f"  ... {idx}/{total} ({len(flagged)} flagged so far)", file=sys.stderr)
        finally:
            browser.close()

    return flagged


def write_tsv(rows: list[dict], path: Path) -> None:
    cols = ["id", "name", "city", "state", "url", "final_url", "title", "preview", "screenshot"]
    lines = ["\t".join(cols)]
    for r in rows:
        lines.append("\t".join(str(r.get(c, "")).replace("\t", " ").replace("\n", " ") for c in cols))
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--only-name-mismatch", action="store_true", default=True)
    args = parser.parse_args()

    firms = load_firms()
    rows = parse_audit_warnings(args.input)
    if args.only_name_mismatch:
        rows = [r for r in rows if "NAME_MISMATCH" in r["reason"]]
    # Only firms that still exist after the cleanup batches
    rows = [r for r in rows if r["id"] in firms]
    print(f"loaded {len(rows)} NAME_MISMATCH rows still in firms.js", file=sys.stderr)

    flagged = check_with_browser(rows, firms, args.timeout)
    write_tsv(flagged, args.output)
    print(f"wrote {args.output} with {len(flagged)} entries still failing after full render", file=sys.stderr)
    print(f"screenshots in {SHOTS_DIR}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
