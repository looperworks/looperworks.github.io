#!/usr/bin/env python3
"""
verify-firm-content.py

Content-verification pass over every firm's website. Catches what the URL
audit can't: domains that return 200 but don't actually belong to the firm
(cybersquatted, parked, rebranded-and-redirected, or original data error).

Algorithm per firm:
  1. Fetch the website HTML with a realistic Chrome user-agent, following
     redirects up to 5 hops.
  2. Note the FINAL URL after redirects. If the final host differs from
     the original (modulo www/scheme), flag REDIRECTED_AWAY.
  3. Extract <title> and visible body text.
  4. Check for parking-page markers in title/body. Flag PARKED.
  5. Check whether ANY significant word from the firm's name appears in
     the title or first 2 KB of body. If none, flag NAME_MISMATCH.
  6. If the page is suspiciously thin (<400 chars of visible body text)
     and not bot-blocked, flag THIN_PAGE.

For sites returning bot-block status codes (401/403/429/503) or timeouts,
the urllib pass cannot read content reliably. Those are escalated to
Playwright Chromium in a second stage to fetch real-browser HTML.

The script does NOT modify firms.js. It writes a report at
../FIRM_CONTENT_AUDIT.md (sibling of the repo, not served by Pages).

Usage:
  python3 scripts/verify-firm-content.py
  python3 scripts/verify-firm-content.py --workers 12
  python3 scripts/verify-firm-content.py --no-browser  # skip Playwright

Defaults: 15 urllib workers, 15s urllib timeout, 25s Playwright timeout.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMS_JS = REPO_ROOT / "mapvoid" / "firms.js"
DEFAULT_REPORT = REPO_ROOT.parent / "FIRM_CONTENT_AUDIT.md"

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

# Parking / domain-marketplace markers. Case-insensitive search in body.
PARKING_MARKERS = [
    "this domain is for sale",
    "buy this domain",
    "domain for sale",
    "domain parking",
    "parked domain",
    "sedo.com",
    "godaddy auction",
    "namecheap marketplace",
    "huge domains",
    "hugedomains.com",
    "domain name registration",
    "premium domain",
    "checkout this domain",
    "register this domain",
    "domain.com listings",
    "afternic",
    "dan.com",
    "uniregistry",
    "the owner of",  # often "the owner of [domain] is offering it for sale"
]


def load_firms() -> list[dict]:
    content = FIRMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const firms = (\[.*\]);?\s*$", content, re.DOTALL)
    if not m:
        sys.exit(f"could not parse {FIRMS_JS}")
    return json.loads(m.group(1))


def host_of(url: str) -> str:
    """Lowercase host, www stripped."""
    h = (urlparse(url).netloc or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def fetch_urllib(url: str, timeout: int) -> tuple[int, str, str]:
    """Return (status_code, final_url, html). status_code is int (0 on connection error)."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            data = r.read(200_000)  # cap at 200KB
            final_url = r.geturl()
            # Decode HTML; tolerate failures
            try:
                html = data.decode("utf-8", errors="replace")
            except Exception:
                html = ""
            return (r.status, final_url, html)
    except urllib.error.HTTPError as e:
        # Bot-block territory: surface code for escalation, no html
        return (e.code, url, "")
    except Exception:
        return (0, url, "")


def extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)


def extract_body_text(html: str, cap_chars: int = 8000) -> str:
    s = _SCRIPT_RE.sub(" ", html)
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    # Decode entities
    s = s.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#x27;", "'").replace("&#39;", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:cap_chars]


# Words too generic to use as firm-name signal
GENERIC_WORDS = {
    "architecture", "architects", "design", "designs", "studio", "studios",
    "group", "associates", "inc", "llc", "plc", "co", "company", "partners",
    "partnership", "and", "the", "of", "for", "an", "a", "&", "+", "/", "-",
    "ny", "la", "nyc", "new", "york", "york.", "ltd", "limited", "office",
    "offices", "corporation", "corp", "consulting", "consultants", "international",
    "studio.", "p.c.", "pc", "pllc",
}


def significant_words(name: str) -> list[str]:
    """Return distinct uppercase or distinctive tokens from the firm name.
    Filters out generic words like 'Architecture', 'Design', 'Inc'."""
    n = name.lower()
    n = re.sub(r"[^a-z0-9\s&+/-]", " ", n)
    out = []
    seen = set()
    for w in n.split():
        if w in GENERIC_WORDS:
            continue
        if len(w) < 3:
            continue
        if w in seen:
            continue
        out.append(w)
        seen.add(w)
    return out


def classify(
    firm: dict,
    status: int,
    final_url: str,
    title: str,
    body: str,
    original_url: str,
) -> tuple[str, str]:
    """Return (severity, reason). severity in {ok, warn, bad}."""
    body_lower = body.lower()
    title_lower = title.lower()

    # 1. Parking markers
    for marker in PARKING_MARKERS:
        if marker in body_lower or marker in title_lower:
            return ("bad", f"PARKED: '{marker}' found on page")

    # 2. Redirected away to different host
    orig_host = host_of(original_url)
    final_host = host_of(final_url)
    if orig_host and final_host and orig_host != final_host:
        # Allow www variation already stripped. Allow same eTLD+1.
        # Crude eTLD+1: take last two labels
        def etld1(h):
            parts = h.split(".")
            return ".".join(parts[-2:]) if len(parts) >= 2 else h
        if etld1(orig_host) != etld1(final_host):
            return ("warn", f"REDIRECTED_AWAY: {orig_host} → {final_host}")

    # 3. Name mismatch — none of the firm's significant words appear
    sigs = significant_words(firm["name"])
    if sigs:
        haystack = (title_lower + " " + body_lower)[:6000]
        hits = [w for w in sigs if w in haystack]
        if not hits:
            return ("warn", f"NAME_MISMATCH: '{', '.join(sigs)}' not in title or body — title='{title[:80]}'")

    # 4. Thin page (very short body text)
    if status == 200 and len(body) < 400 and not title:
        return ("warn", f"THIN_PAGE: {len(body)} chars body, no title — possible broken/parked")

    return ("ok", "")


def check_firm_urllib(firm: dict, timeout: int) -> dict | None:
    """Return result dict for problematic firms, None if ok or unknown."""
    url = (firm.get("website") or "").strip()
    if not url:
        return None
    status, final_url, html = fetch_urllib(url, timeout)
    if status == 0:
        # Connection error — likely bot block or DNS issue; escalate to Playwright
        return {
            "firm": firm,
            "stage": "needs_browser",
            "status": status,
            "final_url": final_url,
            "title": "",
            "body": "",
            "severity": "skip",
            "reason": "connection_error_or_bot_block",
        }
    if status in (401, 403, 429, 503):
        return {
            "firm": firm,
            "stage": "needs_browser",
            "status": status,
            "final_url": final_url,
            "title": "",
            "body": "",
            "severity": "skip",
            "reason": f"bot_block_{status}",
        }
    if status >= 400:
        # 404 etc — already caught by URL audit; not a content issue, skip here
        return None
    title = extract_title(html)
    body = extract_body_text(html)
    severity, reason = classify(firm, status, final_url, title, body, url)
    if severity == "ok":
        return None
    return {
        "firm": firm,
        "stage": "urllib",
        "status": status,
        "final_url": final_url,
        "title": title,
        "body": body[:200],
        "severity": severity,
        "reason": reason,
    }


def check_firms_browser(needs_browser: list[dict], timeout_s: int = 25) -> list[dict]:
    """Run Playwright over the bot-blocked subset to fetch real-browser HTML."""
    if not needs_browser:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; cannot escalate bot-blocked firms", file=sys.stderr)
        return []

    print(f"browser pass: launching Chromium for {len(needs_browser)} bot-blocked firms (~{len(needs_browser)*4}s)", file=sys.stderr)
    out: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            ignore_https_errors=True,
        )
        try:
            for entry in needs_browser:
                firm = entry["firm"]
                url = firm["website"].strip()
                page = ctx.new_page()
                try:
                    resp = page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
                    code = resp.status if resp is not None else 0
                    if code and 200 <= code < 400:
                        try:
                            html = page.content()[:200_000]
                        except Exception:
                            html = ""
                        final_url = page.url
                        title = extract_title(html)
                        body = extract_body_text(html)
                        severity, reason = classify(firm, code, final_url, title, body, url)
                        if severity != "ok":
                            out.append({
                                "firm": firm,
                                "stage": "browser",
                                "status": code,
                                "final_url": final_url,
                                "title": title,
                                "body": body[:200],
                                "severity": severity,
                                "reason": reason,
                            })
                    else:
                        # Even the browser couldn't reach it (uncommon). Skip — URL audit handles it.
                        pass
                except Exception as e:
                    # Browser-level failure; skip (URL audit's job)
                    pass
                finally:
                    page.close()
        finally:
            browser.close()
    return out


def render_report(flagged: list[dict]) -> str:
    today = datetime.date.today().isoformat()
    by_sev = {"bad": [], "warn": []}
    for r in flagged:
        if r["severity"] in by_sev:
            by_sev[r["severity"]].append(r)

    lines: list[str] = []
    lines.append("# Firm content audit")
    lines.append("")
    lines.append(f"Run on {today}. For each firm with a website URL, fetched the page and checked whether the content actually represents the firm.")
    lines.append("")
    lines.append("**Categories:**")
    lines.append("- **PARKED** — page contains domain-marketplace / for-sale markers")
    lines.append("- **REDIRECTED_AWAY** — final URL is on a different eTLD+1 than the original")
    lines.append("- **NAME_MISMATCH** — no significant word from the firm name appears in title or first 6KB of body")
    lines.append("- **THIN_PAGE** — under 400 chars of body and no title; possible broken")
    lines.append("")
    lines.append(f"**Severity counts:** {len(by_sev['bad'])} bad (parked), {len(by_sev['warn'])} warn (mismatches/redirects)")
    lines.append("")
    lines.append("---")
    lines.append("")
    for label, sev in (("Parked / for-sale domains", "bad"), ("Possible mismatches", "warn")):
        rows = by_sev[sev]
        lines.append(f"## {label}")
        lines.append("")
        if not rows:
            lines.append("_None._")
            lines.append("")
            continue
        rows.sort(key=lambda r: r["firm"]["name"].lower())
        lines.append("| # | Firm | City | URL | Final URL | Title | Reason |")
        lines.append("|---|------|------|-----|-----------|-------|--------|")
        for r in rows:
            f = r["firm"]
            url = (f.get("website") or "").strip()
            final = r["final_url"] if r["final_url"] != url else ""
            title = (r.get("title") or "")[:80].replace("|", "\\|")
            reason = r["reason"].replace("|", "\\|")[:100]
            lines.append(f"| {f['id']} | {f['name']} | {f['city']}, {f['state']} | {url} | {final} | {title} | {reason} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--browser-timeout", type=int, default=25)
    parser.add_argument("--no-browser", dest="use_browser", action="store_false", default=True)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    firms = load_firms()
    print(f"loaded {len(firms)} firms", file=sys.stderr)

    with_url = [f for f in firms if (f.get("website") or "").strip()]
    print(f"checking content for {len(with_url)} firms with website URLs ({args.workers} workers)", file=sys.stderr)

    results: list[dict] = []
    needs_browser: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(check_firm_urllib, f, args.timeout): f for f in with_url}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  ... {done}/{len(with_url)}", file=sys.stderr)
            r = fut.result()
            if r is None:
                continue
            if r["stage"] == "needs_browser":
                needs_browser.append(r)
            else:
                results.append(r)

    print(f"urllib pass: {len(results)} flagged, {len(needs_browser)} bot-blocked → browser", file=sys.stderr)

    if args.use_browser and needs_browser:
        browser_results = check_firms_browser(needs_browser, args.browser_timeout)
        results.extend(browser_results)
        print(f"browser pass: {len(browser_results)} additional flags", file=sys.stderr)

    report = render_report(results)
    args.report.write_text(report, encoding="utf-8")
    print(f"wrote {args.report}", file=sys.stderr)
    print(f"total flagged: {len(results)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
