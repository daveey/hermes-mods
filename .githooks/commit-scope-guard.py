#!/usr/bin/env python3
"""Refuse a commit whose staged paths span more than one mod (or delete files in a second mod).

Why: hermes-mods is a shared working tree. Another session's staged edits/deletions ride into your commit
if you don't check `git diff --cached`. Commit 74e8c4e (session-panel) silently deleted
plugins/session-mentions/shepherd.py for a concurrent session, leaving main broken until its sibling landed.

Signal: a "mod" is plugins/<n>, desktop-plugins/<n> (same <n> = same mod, so a plugin and its Desktop half may
travel together) or skills/<cat>/<n>. Files elsewhere (tests/, core/, README.md, ...) are neutral. Two or more
distinct mods staged -> refuse. Override (you really mean a cross-mod commit): HERMES_MODS_ALLOW_MULTI_MOD=1.
Usage: commit-scope-guard.py [--staged-from-stdin]  (default reads `git diff --cached --name-status`).
"""
import os
import subprocess
import sys


def mod_of(path):
    p = path.split("/")
    if p[0] in ("plugins", "desktop-plugins") and len(p) > 2:
        return p[1].replace("_", "-")
    if p[0] == "skills" and len(p) > 3:
        return p[2].replace("_", "-")
    return None


def check(name_status_lines):
    """Return (mods, deleted_by_mod) from `git diff --name-status` lines."""
    mods, deleted = {}, {}
    for line in name_status_lines:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, paths = parts[0], parts[1:]
        for path in paths:
            m = mod_of(path)
            if m is None:
                continue
            mods.setdefault(m, []).append(path)
            if status.startswith("D"):
                deleted.setdefault(m, []).append(path)
    return mods, deleted


def main():
    if os.environ.get("HERMES_MODS_ALLOW_MULTI_MOD") == "1":
        return 0
    if "--staged-from-stdin" in sys.argv:
        lines = sys.stdin.read().splitlines()
    else:
        lines = subprocess.run(["git", "diff", "--cached", "--name-status"], capture_output=True, text=True,
                               check=True).stdout.splitlines()
    mods, deleted = check(lines)
    if len(mods) < 2:
        return 0
    err = sys.stderr.write
    err("pre-commit: staged changes span %d unrelated mods: %s\n" % (len(mods), ", ".join(sorted(mods))))
    for m in sorted(mods):
        err("  %s: %d file(s)%s\n" % (m, len(mods[m]), "  (%d DELETED: %s)" % (len(deleted[m]), ", ".join(deleted[m]))
                                    if m in deleted else ""))
    err("Another session's staged work may have ridden in. Unstage what is not yours (`git restore --staged <path>`),\n"
        "or `git commit -- <your paths>`. If this really is one change: HERMES_MODS_ALLOW_MULTI_MOD=1 git commit ...\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
