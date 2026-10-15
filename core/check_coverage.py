#!/usr/bin/env python3
"""Fail if any core/patches/NNNN-*.patch has no row in core/README.md.

Every patch must be listed in a README table row: a feature row
`| [doc.md](doc.md) | 0040 0041 ... |` (ranges like `0064–0070` count) or a
fix-up table row `| 0034 | ... |`. Every feature row's doc must exist.
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


def main() -> int:
    files = subprocess.run(["git", "ls-files", "--cached", "core/"], capture_output=True, text=True,
                           check=True).stdout.split()
    patches = sorted({f.split("/")[-1][:4] for f in files if re.fullmatch(r"core/patches/\d{4}-.*\.patch", f)})
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
    if uncovered or missing_docs:
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
