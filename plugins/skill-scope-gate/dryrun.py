#!/usr/bin/env python3
"""Dry-run skill-scope-gate over past automatic skill commits.

  python3 dryrun.py <repo> [<repo> ...] [--since "14 days ago"] [--author hermes-hub] [--json]

For every commit by --author since --since, each changed skill markdown file is diffed (parent vs commit) and the
sentences the commit added are classified with gate.find_hits. Prints one line per hit: repo, commit, date, file,
labels, sentence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

_spec = importlib.util.spec_from_file_location("skill_scope_gate_gate", Path(__file__).with_name("gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def show(repo: str, rev: str, path: str) -> str:
    r = subprocess.run(["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def scan(repo: str, since: str, author: str) -> list[dict]:
    out = []
    log = git(repo, "log", f"--since={since}", f"--author={author}", "--format=%H %cI", "--no-merges")
    for line in log.splitlines():
        sha, date = line.split(" ", 1)
        files = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        for f in files:
            if not f.endswith(".md") or "skills/" not in f:
                continue
            for h in gate.find_hits(show(repo, f"{sha}^", f), show(repo, sha, f)):
                out.append({"repo": Path(repo).name, "commit": sha[:7], "date": date, "file": f, **h})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--since", default="14 days ago")
    ap.add_argument("--author", default="hermes-hub")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    hits = [h for r in a.repos for h in scan(r, a.since, a.author)]
    if a.json:
        print(json.dumps(hits, indent=1, ensure_ascii=False))
        return
    for h in hits:
        print(f"{h['repo']} {h['commit']} {h['date'][:16]} {h['file']} [{', '.join(h['labels'])}]\n    {h['sentence']}")
    print(f"{len(hits)} hit(s)")


if __name__ == "__main__":
    main()
