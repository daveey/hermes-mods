#!/usr/bin/env python3
"""Fail if any core/patches/NNNN-*.patch has no row in core/README.md, or two patches share a source.

Every patch must be listed in a README table row: a feature row
`| [doc.md](doc.md) | 0040 0041 ... |` (ranges like `0064–0070` count) or a
fix-up table row `| 0034 | ... |`. Every feature row's doc must exist.
No two patches may carry the same first-line `From <sha> ...` header: the
mirror holds exactly one patch per source commit. (Omitted commits need the
source repository; `core/verify_mirror.py` checks those.)
Run by .githooks/pre-commit whenever core/ is staged; it checks the staged
versions, so an unstaged fix doesn't hide a gap. Exit 0 = covered.
"""

from __future__ import annotations

import re
import subprocess
import sys

NUM = re.compile(r"\b(\d{4})(?:\s*[–-]\s*(\d{4}))?\b")


def staged(path: str) -> str | None:
    out = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def staged_headers() -> list[tuple[str, bytes]]:
    """(ordinal, first line) of every staged core/patches/NNNN-*.patch, in ordinal order."""
    listing = subprocess.run(["git", "ls-files", "-s", "--", "core/patches/"], capture_output=True, text=True,
                             check=True).stdout.splitlines()
    entries = []
    for line in listing:
        meta, path = line.split("\t", 1)
        if re.fullmatch(r"core/patches/\d{4}-.*\.patch", path):
            entries.append((path.split("/")[-1][:4], meta.split()[1]))
    entries.sort()
    if not entries:
        return []
    out = subprocess.run(["git", "cat-file", "--batch"], input="".join(f"{oid}\n" for _, oid in entries).encode(),
                         capture_output=True, check=True).stdout
    headers, pos = [], 0
    for ordinal, _ in entries:
        end = out.index(b"\n", pos)
        size = int(out[pos:end].split()[2])
        body = out[end + 1:end + 1 + size]
        headers.append((ordinal, body.split(b"\n", 1)[0]))
        pos = end + 1 + size + 1
    return headers


def duplicate_headers(headers: list[tuple[str, bytes]]) -> list[str]:
    """One message per patch whose first-line `From <sha> ...` header repeats an earlier patch's."""
    first: dict[bytes, str] = {}
    problems = []
    for ordinal, line in headers:
        match = re.fullmatch(rb"From ([0-9a-f]{40}) Mon Sep 17 00:00:00 2001", line)
        if not match:
            continue
        sha = match.group(1)
        if sha in first:
            problems.append(f"{first[sha]} and {ordinal} share source header {sha.decode()}")
        else:
            first[sha] = ordinal
    return problems


def main() -> int:
    files = subprocess.run(["git", "ls-files", "--cached", "core/"], capture_output=True, text=True,
                           check=True).stdout.split()
    patches = sorted({f.split("/")[-1][:4] for f in files if re.fullmatch(r"core/patches/\d{4}-.*\.patch", f)})
    duplicates = duplicate_headers(staged_headers())
    readme = staged("core/README.md") or ""
    covered: set[str] = set()
    missing_docs = []
    for line in readme.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4:
            continue
        doc = re.match(r"\[[^\]]+\]\(([^)]+\.md)\)", cells[1])
        if doc:
            if f"core/{doc.group(1)}" not in files:
                missing_docs.append(doc.group(1))
            text = cells[2]
        elif re.fullmatch(r"\d{4}", cells[1]):
            text = cells[1]
        else:
            continue
        for a, b in NUM.findall(text):
            covered |= {f"{i:04d}" for i in range(int(a), int(b) + 1)} if b else {a}
    uncovered = [p for p in patches if p not in covered]
    if uncovered or missing_docs or duplicates:
        if duplicates:
            print("core coverage: duplicate patch source headers (each source commit gets exactly one patch):",
                  file=sys.stderr)
            for problem in duplicates:
                print(f"  ordinals {problem}", file=sys.stderr)
            print("  Replace the later copy with the unpublished source commit it displaced "
                  "(python3 -B core/verify_mirror.py names omissions).", file=sys.stderr)
        if uncovered:
            print(f"core coverage: patches with no core/README.md row: {' '.join(uncovered)}", file=sys.stderr)
            print("  Write or extend the feature doc and add its number to the README row in this commit.",
                  file=sys.stderr)
        if missing_docs:
            print(f"core coverage: README rows point at missing docs: {' '.join(missing_docs)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
