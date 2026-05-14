#!/usr/bin/env python3
"""
audit-firm-urls.py

Verify every firm website and careersUrl in mapvoid/firms.js.

What it does:
  1. Loads mapvoid/firms.js and collects all unique website + careersUrl values.
  2. Issues parallel HTTP requests with realistic browser headers (pass 1).
  3. For URLs that fail, tries variants (http <-> https, www <-> bare host)
     (pass 2). If a variant works, the data is auto-updated so the firm card
     links to the working URL.
  4. For URLs that STILL fail, falls back to a real Chromium browser via
     Playwright (pass 3). Many small-firm sites sit behind Cloudflare bot
     challenges or TLS-fingerprint filtering that block urllib but pass a
     full browser. This stage cuts the false-positive rate in the audit
     significantly.
  5. Writes an updated markdown report listing the firms whose URLs are
     still broken after all three passes, grouped by failure mode.

Defaults:
  - firms.js path:    mapvoid/firms.js (relative to repo root)
  - report path:      ../AUDIT_broken_firm_urls.md (one level above repo root,
                      so it is not served by GitHub Pages)
  - parallelism:      15 workers (urllib stages)
  - per-request timeout: 12 seconds (urllib), 20 seconds (browser)
  - User-Agent:       a current desktop Chrome string

Browser fallback setup (one-time, optional but recommended):
  pip install playwright
  playwright install chromium

If playwright is not installed, the script logs a warning and skips the
browser fallback. Everything else still runs; only the false-positive rate
on Cloudflare-protected sites stays higher.

Usage:
  python3 scripts/audit-firm-urls.py                  # run, write firms.js + report
  python3 scripts/audit-firm-urls.py --no-write       # dry run, no file changes
  python3 scripts/audit-firm-urls.py --report PATH    # custom report path
  python3 scripts/audit-firm-urls.py --workers N      # adjust concurrency
  python3 scripts/audit-firm-urls.py --no-browser     # skip Playwright stage
"""

from __future__ import annotations
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMS_JS = REPO_ROOT / "mapvoid" / "firms.js"
DEFAULT_REPORT = REPO_ROOT.parent / "AUDIT_broken_firm_urls.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Mode": "navigate",
    "Upgrade-Insecure-Requests": "1",
}


def load_firms() -> list[dict]:
    content = FIRMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const firms = (\[.*\]);?\s*$", content, re.DOTALL)
    if not m:
        sys.exit(f"could not parse {FIRMS_JS}")
    return json.loads(m.group(1))


def save_firms(firms: list[dict]) -> None:
    FIRMS_JS.write_text(
        "const firms = " + json.dumps(firms, ensure_ascii=False) + ";",
        encoding="utf-8",
    )


def collect_urls(firms: list[dict]) -> set[str]:
    urls = set()
    for f in firms:
        for key in ("website", "careersUrl"):
            u = (f.get(key) or "").strip()
            if u:
                urls.add(u)
    return urls


def variants(url: str) -> list[str]:
    """http<->https and www<->bare combinations, original first."""
    p = urlparse(url)
    if not p.netloc:
        return [url]
    host = p.netloc
    alt_host = host[4:] if host.startswith("www.") else "www." + host
    alt_scheme = "http" if p.scheme == "https" else "https"
    out = [url]
    seen = {url}
    for h in (host, alt_host):
        for s in (p.scheme, alt_scheme):
            candidate = f"{s}://{h}{p.path or ''}"
            if p.query:
                candidate += "?" + p.query
            if candidate not in seen:
                out.append(candidate)
                seen.add(candidate)
    return out


def check(url: str, timeout: int) -> tuple[str, str]:
    """Return (status_label, url). Status: 200..599 as str, or category name."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers=HEADERS)
        r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return (str(r.status), url)
    except urllib.error.HTTPError as e:
        return (str(e.code), url)
    except Exception as e:
        return (type(e).__name__, url)


def is_ok(status: str) -> bool:
    return status.isdigit() and 200 <= int(status) < 400


def try_recover(url: str, timeout: int) -> tuple[str, str]:
    """Try variants; return (status, working_url) where working_url may differ."""
    for v in variants(url):
        s, _ = check(v, timeout)
        if is_ok(s):
            return (s, v)
    s, _ = check(url, timeout)
    return (s, url)


def try_browser(urls: list[str], timeout: int = 20) -> dict[str, tuple[str, str]]:
    """Browser-realistic check for URLs that failed earlier stages.

    Launches one headless Chromium, then for each URL tries the original and
    its variants until one returns 2xx/3xx. This is the slow path, intended
    only for URLs that urllib could not reach (often Cloudflare-protected
    small-firm sites that gate on TLS fingerprint or JS challenge).

    Returns dict mapping original_url -> (status_label, working_url). If
    the original URL works, working_url == original_url. If a variant works,
    working_url is the variant.

    If `playwright` is not installed, returns {} and logs a hint.
    """
    if not urls:
        return {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "browser fallback skipped: playwright not installed. "
            "Install with: pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return {}

    results: dict[str, tuple[str, str]] = {}
    print(
        f"browser fallback: launching Chromium for {len(urls)} URLs (~{len(urls) * 3}s)",
        file=sys.stderr,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            },
            ignore_https_errors=True,
        )
        try:
            for url in urls:
                page = ctx.new_page()
                last_status = "ERR"
                chosen: tuple[str, str] | None = None
                try:
                    for cand in variants(url):
                        try:
                            resp = page.goto(
                                cand,
                                timeout=timeout * 1000,
                                wait_until="domcontentloaded",
                            )
                            code = str(resp.status) if resp is not None else "NoResp"
                            last_status = code
                            if is_ok(code):
                                chosen = (code, cand)
                                break
                        except Exception as e:
                            last_status = type(e).__name__[:15]
                finally:
                    page.close()
                results[url] = chosen if chosen else (last_status, url)
        finally:
            browser.close()

    return results


def categorize(status: str) -> str:
    if is_ok(status):
        return "ok"
    if status == "404":
        return "page_404"
    if status.isdigit() and 500 <= int(status) < 600:
        return "server_error"
    if status in ("400", "429"):
        return "server_error"
    return "dead_domain"


def render_report(
    grouped: dict[str, list[tuple]],
    total_urls: int,
    recovered: int,
    cleared: int,
) -> str:
    today = datetime.date.today().isoformat()
    lines: list[str] = []
    lines.append("# Broken firm URLs — audit report")
    lines.append("")
    lines.append(
        f"Audit run on {today} against `mapvoid/firms.js`. "
        f"Of {total_urls} unique firm URLs checked, "
        f"{recovered} were auto-recovered by trying http/https and www variants. "
        f"{cleared} broken careersUrl entries were cleared automatically. "
        f"The entries below are URLs that are still broken after those fixes "
        f"and need manual review."
    )
    lines.append("")
    counts = {k: len(v) for k, v in grouped.items()}
    lines.append("**Summary**")
    lines.append(
        f"- Dead domains (not reachable at all): "
        f"**{counts.get('dead_domain', 0)}** firm records"
    )
    lines.append(
        f"- 404 (URL resolves but page is gone): "
        f"**{counts.get('page_404', 0)}** firm records"
    )
    lines.append(
        f"- Server errors (possibly transient): "
        f"**{counts.get('server_error', 0)}** firm records"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    for title, key in (
        ("Dead domains", "dead_domain"),
        ("404 pages", "page_404"),
        ("Server errors", "server_error"),
    ):
        lines.append(f"## {title}")
        lines.append("")
        rows = sorted(grouped.get(key, []), key=lambda r: r[1].lower())
        if not rows:
            lines.append("_None._")
            lines.append("")
            continue
        lines.append("| # | Firm | Location | Field | URL |")
        lines.append("|---|------|----------|-------|-----|")
        for fid, name, city, state, field, url in rows:
            lines.append(
                f"| {fid} | {name} | {city}, {state} | `{field}` | {url} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="Path to write the markdown audit report.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Dry run; do not modify firms.js or the report file.",
    )
    parser.add_argument(
        "--workers", type=int, default=15, help="Concurrent HTTP workers."
    )
    parser.add_argument(
        "--timeout", type=int, default=12, help="Per-request timeout (seconds)."
    )
    parser.add_argument(
        "--clear-careers-404",
        action="store_true",
        default=True,
        help="Auto-clear careersUrl values that 404 (default on).",
    )
    parser.add_argument(
        "--keep-careers-404",
        dest="clear_careers_404",
        action="store_false",
        help="Keep broken careersUrl values; report only.",
    )
    parser.add_argument(
        "--browser",
        dest="use_browser",
        action="store_true",
        default=True,
        help="Use Playwright Chromium for URLs that failed earlier stages (default on).",
    )
    parser.add_argument(
        "--no-browser",
        dest="use_browser",
        action="store_false",
        help="Skip the browser fallback stage; faster but more false positives.",
    )
    parser.add_argument(
        "--browser-timeout",
        type=int,
        default=20,
        help="Per-request timeout for the browser stage (seconds).",
    )
    args = parser.parse_args()

    socket.setdefaulttimeout(args.timeout + 3)
    firms = load_firms()
    print(f"loaded {len(firms)} firms", file=sys.stderr)

    urls = sorted(collect_urls(firms))
    print(f"checking {len(urls)} unique URLs with {args.workers} workers", file=sys.stderr)

    # First pass: check each URL as written
    raw_status: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for status, url in ex.map(lambda u: check(u, args.timeout), urls):
            raw_status[url] = status

    # Second pass: for failures, try variants
    recoveries: dict[str, str] = {}  # original_url -> working_url
    failed = [u for u, s in raw_status.items() if not is_ok(s)]
    print(f"first pass: {len(urls) - len(failed)} ok, {len(failed)} failed; trying variants", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for orig, result in zip(
            failed, ex.map(lambda u: try_recover(u, args.timeout), failed)
        ):
            new_status, new_url = result
            if is_ok(new_status) and new_url != orig:
                recoveries[orig] = new_url
                raw_status[orig] = new_status  # mark original as effectively ok
            else:
                raw_status[orig] = new_status

    print(f"recoveries after urllib variants: {len(recoveries)}", file=sys.stderr)

    # Third pass: real browser via Playwright for URLs that still fail.
    # This catches Cloudflare challenges and TLS-fingerprint bot blocking.
    if args.use_browser:
        still_failing = [u for u, s in raw_status.items() if not is_ok(s)]
        if still_failing:
            browser_results = try_browser(still_failing, timeout=args.browser_timeout)
            browser_recovered = 0
            for orig, (status, working_url) in browser_results.items():
                if is_ok(status):
                    raw_status[orig] = status
                    if working_url != orig:
                        recoveries[orig] = working_url
                    browser_recovered += 1
                else:
                    raw_status[orig] = status
            if browser_results:
                print(
                    f"browser pass: {browser_recovered} additional recoveries "
                    f"({browser_recovered}/{len(still_failing)} false positives fixed)",
                    file=sys.stderr,
                )

    # Apply recoveries to firm records
    applied_recoveries = 0
    cleared_careers = 0
    cleared_records: list[tuple] = []
    for fm in firms:
        for key in ("website", "careersUrl"):
            u = (fm.get(key) or "").strip()
            if u in recoveries:
                fm[key] = recoveries[u]
                applied_recoveries += 1
            elif (
                args.clear_careers_404
                and key == "careersUrl"
                and u
                and raw_status.get(u) == "404"
            ):
                cleared_records.append((fm["id"], fm["name"], u))
                fm[key] = ""
                cleared_careers += 1

    if cleared_records:
        print(f"cleared {cleared_careers} broken careersUrl entries:", file=sys.stderr)
        for r in cleared_records:
            print(f"  #{r[0]:5d}  {r[1]}  (was: {r[2]})", file=sys.stderr)

    # Build the broken-firms report from the final state
    grouped: dict[str, list[tuple]] = {}
    for fm in firms:
        for key in ("website", "careersUrl"):
            u = (fm.get(key) or "").strip()
            if not u:
                continue
            status = raw_status.get(u, "")
            if is_ok(status):
                continue
            cat = categorize(status)
            grouped.setdefault(cat, []).append(
                (fm["id"], fm["name"], fm["city"], fm["state"], key, u)
            )

    report = render_report(grouped, len(urls), len(recoveries), cleared_careers)

    if args.no_write:
        print("--no-write: not modifying files", file=sys.stderr)
        print(report)
    else:
        save_firms(firms)
        args.report.write_text(report, encoding="utf-8")
        print(f"wrote firms.js and {args.report}", file=sys.stderr)

    total_broken = sum(len(v) for v in grouped.values())
    print(
        f"summary: {len(urls)} checked, "
        f"{len(recoveries)} recovered, "
        f"{cleared_careers} careersUrl cleared, "
        f"{total_broken} still broken",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
