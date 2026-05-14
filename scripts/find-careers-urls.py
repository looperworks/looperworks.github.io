#!/usr/bin/env python3
"""find-careers-urls.py

For every firm in firms.js without a careersUrl, probe the firm's
website at common careers-page paths and record the first one that
responds 200 OK.

Strategy:
  - Dedupe by website domain (multi-office firms share one URL)
  - Try paths in priority order: /careers, /careers/, /jobs, /join-us,
    /work-with-us, /about/careers, /careers/positions
  - Use realistic Chrome headers + 10s timeout
  - Only accept paths where the response is 200 AND the body contains
    careers-relevant words (job, career, hiring, position, openings,
    apply, opportunity) — guards against generic catch-all home pages
  - Write findings back to firms.js, applying to ALL branches of each firm

This is an opt-in feature: it doesn't modify firms that already have a
careersUrl, doesn't add a URL we couldn't verify, and never overwrites
the main website field.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMS_JS = REPO_ROOT / "mapvoid" / "firms.js"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Try these in order; first match wins
CAREER_PATHS = [
    "/careers",
    "/careers/",
    "/careers/positions",
    "/careers/positions/",
    "/jobs",
    "/jobs/",
    "/join-us",
    "/join-us/",
    "/join",
    "/join/",
    "/work-with-us",
    "/work-with-us/",
    "/about/careers",
    "/about/careers/",
    "/people/careers",
    "/people/careers/",
    "/firm/careers",
    "/studio/careers",
    "/opportunities",
]

# Words that should appear on a real careers page (case-insensitive)
CAREERS_WORDS = re.compile(
    r"\b(career|jobs?|hiring|hire|position|openings?|apply|employment|opportunit|recruit|internship|join (?:our|us|the team))\b",
    re.IGNORECASE,
)


def load_firms() -> list[dict]:
    text = FIRMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const firms = (\[.*\]);?\s*$", text, re.DOTALL)
    return json.loads(m.group(1))


def save_firms(firms: list[dict]) -> None:
    FIRMS_JS.write_text("const firms = " + json.dumps(firms, ensure_ascii=False) + ";", encoding="utf-8")


def fetch(url: str, timeout: int = 10) -> tuple[int, str]:
    """Return (status, body[:50000]). status=0 on any connection error."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read(50_000).decode("utf-8", errors="replace")
            return (r.status, body)
    except urllib.error.HTTPError as e:
        return (e.code, "")
    except Exception:
        return (0, "")


def best_careers_path(base_url: str) -> str | None:
    """Probe paths in priority order. Return absolute URL of the first match,
    or None if no path qualifies."""
    base = base_url.rstrip("/")
    # Skip if base URL itself is unreachable — saves time on dead domains
    base_status, _ = fetch(base, timeout=8)
    if base_status == 0 or base_status >= 500:
        return None
    for path in CAREER_PATHS:
        url = base + path
        status, body = fetch(url, timeout=8)
        if status != 200:
            continue
        # Must look like a careers page
        if CAREERS_WORDS.search(body):
            return url
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-size", type=int, default=20,
                        help="Skip firms smaller than this. Default 20.")
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    firms = load_firms()

    # Group firms by website domain. We only need to probe once per domain.
    def normalize(url: str) -> str:
        return (url or "").strip().rstrip("/").lower()

    domain_groups: dict[str, list[dict]] = defaultdict(list)
    for f in firms:
        url = normalize(f.get("website") or "")
        if not url:
            continue
        if (f.get("careersUrl") or "").strip():
            continue  # already has one
        size = f.get("size")
        if isinstance(size, str):
            # "10-19" → 10, "<10" → 5, "50-100" → 50
            m = re.search(r"\d+", size)
            n = int(m.group(0)) if m else 0
        elif isinstance(size, int):
            n = size
        else:
            n = 0
        if n < args.min_size:
            continue
        domain_groups[url].append(f)

    print(f"Probing {len(domain_groups)} unique websites "
          f"(min size {args.min_size}, {sum(len(g) for g in domain_groups.values())} firm records)",
          file=sys.stderr)

    found: dict[str, str] = {}  # website_url -> careers_url
    failed: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(best_careers_path, url): url for url in domain_groups}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            url = futures[fut]
            try:
                careers = fut.result()
            except Exception:
                careers = None
            if careers:
                found[url] = careers
            else:
                failed.append(url)
            if done % 50 == 0:
                print(f"  ... {done}/{len(domain_groups)} probed, {len(found)} careers found",
                      file=sys.stderr)

    # Apply findings to all branches of each firm
    applied = 0
    if not args.dry_run:
        for f in firms:
            url = normalize(f.get("website") or "")
            if url in found and not (f.get("careersUrl") or "").strip():
                f["careersUrl"] = found[url]
                applied += 1
        save_firms(firms)

    print(f"\nFound careers pages: {len(found)} / {len(domain_groups)} domains "
          f"({len(found)/max(1,len(domain_groups))*100:.0f}%)",
          file=sys.stderr)
    print(f"Applied to {applied} firm records (across all branches)",
          file=sys.stderr)
    print(f"\nSample of newly-discovered careers URLs:")
    for url, careers in list(found.items())[:20]:
        print(f"  {url}\n  → {careers}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
