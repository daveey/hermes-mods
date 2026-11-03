#!/usr/bin/env python3
"""List skills whose SKILL.md body is over a size budget.

  inventory.py [--lines 500] [--chars 30000] [--core ~/.hermes/hermes-agent] DIR [DIR ...]

One line per skill: lines, chars, owner, path. Owner is ``upstream`` when a skill of that name ships in core's
skills/ or optional-skills/ now and our body is core's text (at most FORK_LINES lines / FORK_CHARS chars added),
``owned-fork`` when the name ships in core but our copy adds more than that (our additions are ours to split),
``upstream-orphan`` when the name only appears in core's git history (upstream retired, merged or moved it and our
copy is upstream text), else ``owned``. ``owned-fork`` rows also print the added lines/chars. Duplicate real paths
are listed once.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("skill_size_budget_budget", Path(__file__).with_name("budget.py"))
budget = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = budget
_spec.loader.exec_module(budget)


FORK_LINES = 20
FORK_CHARS = 2_000


def upstream_paths(core: Path) -> dict[str, Path]:
    """name -> core's SKILL.md for every skill shipping in core's skills/ or optional-skills/ now."""
    out: dict[str, Path] = {}
    for sub in ("skills", "optional-skills"):
        for p in sorted((core / sub).glob("**/SKILL.md")):
            out.setdefault(p.parent.name, p)
    return out


def upstream_names(core: Path) -> set[str]:
    return set(upstream_paths(core))


def added_vs(ours: str, theirs: str) -> tuple[int, int]:
    """(lines, chars) of non-blank body lines in ``ours`` that core's ``theirs`` does not have."""
    a = budget.body(theirs).splitlines()
    b = budget.body(ours).splitlines()
    lines = chars = 0
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            for line in b[j1:j2]:
                if line.strip():
                    lines += 1
                    chars += len(line) + 1
    return lines, chars


def is_fork(ours: str, theirs: str, max_lines: int = FORK_LINES, max_chars: int = FORK_CHARS) -> bool:
    lines, chars = added_vs(ours, theirs)
    return lines > max_lines or chars > max_chars


def upstream_history_names(core: Path) -> set[str]:
    """Names of every SKILL.md dir that ever existed under core's skills/ or optional-skills/ (all refs)."""
    try:
        out = subprocess.run(["git", "-C", str(core), "log", "--all", "--format=", "--name-only", "--",
                              "skills", "optional-skills"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {Path(line).parent.name for line in out.splitlines() if line.endswith("/SKILL.md")}


def owner(name: str, current, history: set[str], ours: str | None = None) -> str:
    """``current`` is a name set or a name -> core SKILL.md map; with a map and ``ours``, a diverged copy is a fork."""
    if name in current:
        core_p = current.get(name) if isinstance(current, dict) else None
        if ours is not None and core_p is not None:
            try:
                theirs = Path(core_p).read_text(encoding="utf-8", errors="replace")
            except OSError:
                theirs = None
            if theirs is not None and is_fork(ours, theirs):
                return "owned-fork"
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
    up = upstream_paths(core)
    hist = upstream_history_names(core)
    seen, rows = set(), []
    for d in a.dirs:
        for p in Path(os.path.expanduser(d)).glob("**/SKILL.md"):
            rp = p.resolve()
            if rp in seen or core in rp.parents:
                continue
            seen.add(rp)
            text = p.read_text(encoding="utf-8", errors="replace")
            lines, chars = budget.measure(text)
            if lines > a.lines or chars > a.chars:
                who = owner(p.parent.name, up, hist, text)
                extra = ""
                if who == "owned-fork":
                    al, ac = added_vs(text, up[p.parent.name].read_text(encoding="utf-8", errors="replace"))
                    extra = f"  (+{al} lines / +{ac:,} chars vs core)"
                rows.append((lines, chars, who, str(p) + extra))
    for r in sorted(rows, key=lambda r: (r[2], -r[1])):
        print(f"{r[0]:5d} {r[1]:7,d} {r[2]:15s} {r[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
