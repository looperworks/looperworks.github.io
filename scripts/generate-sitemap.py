#!/usr/bin/env python3
"""generate-sitemap.py — rebuild sitemap.xml from the actual HTML pages.

The sitemap used to be hand-maintained and drifted (22 of 82 pages listed).
This regenerates it from every index.html on disk so it can't fall behind again.
Run after adding or removing pages:

  python3 scripts/generate-sitemap.py
"""
from __future__ import annotations
import glob, os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = "https://thresholdarch.com/"

def route(p: Path) -> str:
    rel = p.relative_to(REPO).as_posix()
    rel = rel.replace("index.html", "")
    return BASE + rel

def rule(rel: str):
    # returns (changefreq, priority)
    if rel == "":                       return ("monthly", "1.0")
    if rel == "mapvoid/":               return ("weekly",  "0.9")
    if rel == "blog/":                  return ("weekly",  "0.8")
    if rel in ("about/",):              return ("monthly", "0.8")
    top = rel.rstrip("/")
    # section landing pages
    if top in ("resume-guide","cover-letter-guide","portfolio-guide",
               "application-timeline","resources"):
        return ("monthly", "0.8")
    if rel.startswith("blog/posts/"):   return ("yearly",  "0.6")
    if rel.startswith("resources/"):    return ("yearly",  "0.7")
    if top in ("terms","privacy"):      return ("yearly",  "0.3")
    # guide sub-pages (evergreen articles)
    return ("monthly", "0.6")

pages = []
for p in glob.glob(str(REPO / "**" / "*.html"), recursive=True):
    p = Path(p)
    if p.name == "404.html":
        continue
    rel = p.relative_to(REPO).as_posix().replace("index.html", "")
    pages.append(rel)

# stable order: homepage, mapvoid, then alphabetical
def sort_key(rel):
    if rel == "": return (0, "")
    if rel == "mapvoid/": return (1, "")
    return (2, rel)
pages.sort(key=sort_key)

lines = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
for rel in pages:
    cf, pr = rule(rel)
    loc = BASE + rel
    lines.append(f'  <url><loc>{loc}</loc><changefreq>{cf}</changefreq><priority>{pr}</priority></url>')
lines.append('</urlset>')

out = REPO / "sitemap.xml"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Wrote {out.relative_to(REPO)} with {len(pages)} URLs (was 22).")
