#!/usr/bin/env python3
"""List skills whose SKILL.md body is over a size budget.

  inventory.py [--lines 500] [--chars 30000] [--core ~/.hermes/hermes-agent] DIR [DIR ...]

One line per skill: lines, chars, owner, path. Owner is ``upstream`` when a skill of that name ships in core's
skills/ or optional-skills/ now, ``upstream-orphan`` when it only appears there in core's git history (upstream
retired, merged or moved it and our copy is upstream text), else ``owned``. Duplicate real paths are listed once.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("skill_size_budget_budget", Path(__file__).with_name("budget.py"))
budget = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = budget
_spec.loader.exec_module(budget)


def upstream_names(core: Path) -> set[str]:
    names = set()
    for sub in ("skills", "optional-skills"):
        for p in (core / sub).glob("**/SKILL.md"):
            names.add(p.parent.name)
    return names


def upstream_history_names(core: Path) -> set[str]:
    """Names of every SKILL.md dir that ever existed under core's skills/ or optional-skills/ (all refs)."""
    try:
        out = subprocess.run(["git", "-C", str(core), "log", "--all", "--format=", "--name-only", "--",
                              "skills", "optional-skills"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {Path(line).parent.name for line in out.splitlines() if line.endswith("/SKILL.md")}


def owner(name: str, current: set[str], history: set[str]) -> str:
    if name in current:
        return "upstream"
    return "upstream-orphan" if name in history else "owned"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--lines", type=int, default=500)
    ap.add_argument("--chars", type=int, default=30_000)
    ap.add_argument("--core", default=os.path.expanduser("~/.hermes/hermes-agent"))
    a = ap.parse_args()
    core = Path(a.core)
    up = upstream_names(core)
    hist = upstream_history_names(core)
    seen, rows = set(), []
    for d in a.dirs:
        for p in Path(os.path.expanduser(d)).glob("**/SKILL.md"):
            rp = p.resolve()
            if rp in seen or core in rp.parents:
                continue
            seen.add(rp)
            lines, chars = budget.measure(p.read_text(encoding="utf-8", errors="replace"))
            if lines > a.lines or chars > a.chars:
                rows.append((lines, chars, owner(p.parent.name, up, hist), str(p)))
    for r in sorted(rows, key=lambda r: (r[2], -r[1])):
        print(f"{r[0]:5d} {r[1]:7,d} {r[2]:15s} {r[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
