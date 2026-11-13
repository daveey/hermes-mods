#!/usr/bin/env python3
"""List every PR merged into a repo since a UTC time, selected by merge time and fully paged.

Usage: merged_since.py --repo OWNER/REPO --since 2026-11-13T15:00:00Z [--base main] [--per-page 100]

`gh pr list --state merged --limit N` returns the N newest-*created* PRs, so a long-lived PR merged inside your
window drops out of it. This queries the search API for `is:pr is:merged merged:>=SINCE` and pages until a short or
empty page. Prints JSON: {"since", "window_end", "count", "prs": [{number, title, author, created_at, merged_at,
url}]} sorted by merged_at. `window_end` is the newest merged_at returned (the search index trails merges by
minutes): start the next sweep there, not at the query time. Direct pushes to the branch are not PRs; list them
separately (`gh api 'repos/O/R/commits?sha=main&since=<UTC ISO>'`). Exit 2 on any failed or unreadable page (no
partial output), exit 3 when GitHub caps the result (1000 search results) so the window must be split.
"""
import argparse
import json
import subprocess
import sys

SEARCH_CAP = 1000


def page(query, per_page, n):
    p = subprocess.run(["gh", "api", "-X", "GET", "search/issues", "-f", f"q={query}", "-f", f"per_page={per_page}",
                        "-f", f"page={n}", "-f", "sort=created", "-f", "order=asc"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"page {n}: exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    data = json.loads(p.stdout)
    if data.get("incomplete_results"):
        raise RuntimeError(f"page {n}: GitHub returned incomplete_results; retry")
    return data["total_count"], data["items"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since", required=True, help="UTC ISO time, e.g. 2026-11-13T15:00:00Z")
    ap.add_argument("--base", default="", help="only PRs into this base branch")
    ap.add_argument("--per-page", type=int, default=100)
    a = ap.parse_args(argv)
    query = f"repo:{a.repo} is:pr is:merged merged:>={a.since}" + (f" base:{a.base}" if a.base else "")
    seen, n, total = {}, 1, None
    try:
        while True:
            total, items = page(query, a.per_page, n)
            for it in items:
                seen[it["number"]] = {
                    "number": it["number"], "title": it.get("title", ""),
                    "author": (it.get("user") or {}).get("login", ""), "created_at": it.get("created_at"),
                    "merged_at": (it.get("pull_request") or {}).get("merged_at"), "url": it.get("html_url", "")}
            if len(items) < a.per_page or n * a.per_page >= SEARCH_CAP:
                break
            n += 1
    except (RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"merged_since: read failed, no result: {exc}", file=sys.stderr)
        return 2
    if total is not None and total > SEARCH_CAP:
        print(f"merged_since: {total} results exceed the search cap {SEARCH_CAP}; split the window", file=sys.stderr)
        return 3
    prs = sorted(seen.values(), key=lambda p: (p["merged_at"] or "", p["number"]))
    merged = [p["merged_at"] for p in prs if p["merged_at"]]
    print(json.dumps({"since": a.since, "window_end": max(merged) if merged else a.since,
                      "count": len(prs), "prs": prs}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
