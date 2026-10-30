#!/usr/bin/env python3
"""Wait until a pushed commit has passed (or been released by) GitHub Actions.

Waiting is based on what contains your commit, not on which run is newest. On a
busy branch, concurrency groups replace pending runs and most of them end
`cancelled`, so "watch the newest run" (or "follow run <id>") misses the run
that actually built your commit, and the watcher waits forever.

    wait_for_ci.py <sha> [<sha> ...] [--workflow FILE_OR_NAME ...] [--tag-glob GLOB ...]
                   [--branch main] [--timeout-min 120] [--interval 60] [--once] [--dispatch]

Done when:
  --tag-glob given  for every glob, some tag matching it contains every sha
                    (e.g. `v*`, `desktop-pod-*`): the commit is released
  otherwise         for every workflow (the --workflow ones, or else every workflow
                    with a run on a commit that contains the sha), some run on a
                    commit containing the sha concluded `success`

Exit codes: 0 done; 1 stalled (nothing queued/running will finish it: the newest
finished run containing the commit failed, or no run will ever pick it up), or
`gh run list` failed MAX_CALL_ERRORS times in a row (under --once, 1 means a finished
run containing the commit failed and nothing is queued/running); 2 timed out, or not yet done
with --once (a failed listing under --once is also 2); 3 a sha is not on origin/<branch>.
Every gh/git call is bounded by CALL_TIMEOUT_S, so a hung call can't outlive the deadline.
--dispatch: on a stall, `gh workflow run` each --workflow (at most twice) and
keep waiting instead of exiting 1.

Run it from inside the repo checkout (needs `git` and an authenticated `gh`).
Start it as a background process with completion notification and end the turn:
it prints only state changes and one final line.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}
STARTUP_GRACE_S = 180  # runs can take a minute or two to appear after a push
CALL_TIMEOUT_S = 120  # one gh/git call; an unbounded call that hangs holds the wait past its own deadline
MAX_CALL_ERRORS = 5  # consecutive failed listings before giving up (exit 1), so auth/repo errors still surface


def git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)}: timed out after {CALL_TIMEOUT_S}s") from None
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def make_contains(repo: Path):
    cache: dict[tuple[str, str], bool] = {}

    def contains(commit: str, sha: str) -> bool:
        """True if `commit` has `sha` in its history (commit == sha counts).

        Only a definite answer is cached: exit 0 (yes) or 1 (no). Exit 128 means `commit` isn't fetched here yet,
        as for a run listed between a push and the next fetch. Caching that as "no" hid the run for the rest of the
        wait, so its success never counted and the wait ran to its timeout."""
        key = (commit, sha)
        if key in cache:
            return cache[key]
        try:
            rc = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, commit],
                                capture_output=True, timeout=CALL_TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            return False
        if rc in (0, 1):
            cache[key] = rc == 0
        return rc == 0

    return contains


def tags_done(repo: Path, shas: list[str], globs: list[str]) -> dict[str, str | None]:
    """glob -> oldest matching tag (by creation) containing every sha, or None."""
    result: dict[str, str | None] = {}
    for glob in globs:
        sets = [set(git(repo, "tag", "--contains", sha, "-l", glob).split()) for sha in shas]
        common = set.intersection(*sets) if sets else set()
        if common:
            ordered = git(repo, "tag", "-l", glob, "--sort=creatordate").split()
            result[glob] = next((t for t in ordered if t in common), sorted(common)[0])
        else:
            result[glob] = None
    return result


def workflow_key(run: dict) -> str:
    return run.get("workflowName") or run.get("name") or "?"


def evaluate(runs: list[dict], shas: list[str], contains, workflows: list[str] | None) -> dict[str, dict]:
    """Per workflow: {'done': run|None, 'active': n, 'failed': run|None}.

    `runs` are newest first. A run counts for the commit when its headSha
    contains every sha. Cancelled runs are ignored (they were replaced).
    """
    relevant = [r for r in runs if all(contains(r["headSha"], s) for s in shas)]
    names = workflows if workflows else sorted({workflow_key(r) for r in relevant})
    state = {}
    for name in names:
        mine = [r for r in relevant if workflow_key(r) == name or r.get("_wf") == name]
        everyone = [r for r in runs if workflow_key(r) == name or r.get("_wf") == name]
        done = next((r for r in mine if r["status"] == "completed" and r["conclusion"] == "success"), None)
        finished = [r for r in mine if r["status"] == "completed" and r["conclusion"] not in ("cancelled", "skipped")]
        state[name] = {
            "done": done,
            # Any active run of the workflow may still end up building a newer commit containing ours.
            "active": sum(1 for r in everyone if r["status"] in ACTIVE),
            "failed": finished[0] if finished and finished[0]["conclusion"] != "success" else None,
        }
    return state


def run_ref(run: dict, shas: list[str]) -> str:
    """The run's URL, plus the commit it built when that is a later commit containing the sha(s).

    A run proves (or fails) a commit when its headSha contains it, so the run printed under the requested sha's
    label may have built a later commit. Its URL got quoted as "CI on <sha>" although that run never built <sha>,
    and a red run on a later commit may be failing on someone else's change. So name the commit the run built.
    """
    head = run.get("headSha") or ""
    if head and head not in shas:
        return f"{run['url']} (ran on {head[:10]}, a later commit containing it)"
    return run["url"]


def gh_repo(repo: Path) -> str | None:
    """OWNER/NAME of the checkout's `origin` on GitHub, passed to every `gh` call as `-R`.

    Without it `gh` picks its own default repo. In a fork checkout that also has an `upstream` remote (e.g. a
    Hermes core checkout: origin = your fork, upstream = NousResearch/hermes-agent) it listed the UPSTREAM repo's
    runs, never found the fork's commit in them, and every wait ended "stalled" with no failing run named.
    """
    out = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", out.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if out.returncode == 0 and m else None


def gh_cmd(slug: str | None, *args: str) -> list[str]:
    return ["gh", *args, *(["-R", slug] if slug else [])]


def list_runs(repo: Path, branch: str, workflows: list[str], slug: str | None = None) -> list[dict]:
    fields = "databaseId,headSha,status,conclusion,workflowName,url,event"
    runs: list[dict] = []
    for wf in workflows or [None]:
        cmd = gh_cmd(slug, "run", "list", "--branch", branch, "-L", "40" if wf else "100", "--json", fields)
        if wf:
            cmd += ["--workflow", wf]
        try:
            out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"gh run list: timed out after {CALL_TIMEOUT_S}s") from None
        if out.returncode != 0:
            raise RuntimeError(f"gh run list: {out.stderr.strip()}")
        for run in json.loads(out.stdout or "[]"):
            if wf:
                run["_wf"] = wf
            runs.append(run)
    return runs


def resolve_repo(repo: Path) -> Path:
    """--repo is a local checkout. An `owner/name` slug (a common mix-up) maps to ~/code/<name> when that checkout's
    origin is the same GitHub repo; otherwise fail with a clear message instead of a git traceback."""
    if repo.is_dir():
        return repo
    parts = str(repo).strip("/").split("/")
    if len(parts) == 2 and all(parts):
        guess = Path.home() / "code" / parts[1]
        if guess.is_dir():
            out = subprocess.run(["git", "-C", str(guess), "remote", "get-url", "origin"], capture_output=True, text=True)
            url = out.stdout.strip().removesuffix(".git").lower()
            if out.returncode == 0 and url.endswith("/".join(parts).lower()):
                return guess
    raise SystemExit(f"--repo {repo}: not a local checkout. Pass the checkout path (e.g. ~/code/<name>), not owner/name.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("shas", nargs="+")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--branch", default="main")
    p.add_argument("--workflow", action="append", default=[], help="workflow file or name (repeatable)")
    p.add_argument("--tag-glob", action="append", default=[], help="release tag glob (repeatable)")
    p.add_argument("--timeout-min", type=float, default=120)
    p.add_argument("--interval", type=float, default=60)
    p.add_argument("--once", action="store_true")
    p.add_argument("--dispatch", action="store_true")
    p.add_argument("--gh-repo", help="OWNER/NAME for gh (default: the checkout's origin remote)")
    a = p.parse_args(argv)
    repo = resolve_repo(a.repo)
    slug = a.gh_repo or gh_repo(repo)

    def fetch():
        git(repo, "fetch", "-q", "--tags", "--force", "origin", a.branch)

    fetch()
    shas = [git(repo, "rev-parse", "--verify", f"{s}^{{commit}}") for s in a.shas]
    contains = make_contains(repo)
    for sha in shas:
        if not contains(f"origin/{a.branch}", sha):
            print(f"{sha[:9]} is not on origin/{a.branch}; push it first")
            return 3

    label = ",".join(s[:9] for s in shas)
    start = time.monotonic()
    deadline = start + a.timeout_min * 60
    dispatches, idle, last, errors = 0, 0, "", 0
    while True:
        try:
            runs = list_runs(repo, a.branch, a.workflow, slug)
        except RuntimeError as e:
            # A failed or timed-out listing is "no data this round", not a verdict: retry until the deadline.
            errors += 1
            if a.once:
                print(f"not yet: {e}")
                return 2
            if errors >= MAX_CALL_ERRORS:
                print(f"gh kept failing ({errors} times in a row): {e}")
                return 1
            if time.monotonic() >= deadline:
                print(f"timed out after {a.timeout_min:g} min; last gh error: {e}")
                return 2
            print(f"{e}; retrying", flush=True)
            time.sleep(a.interval)
            continue
        errors = 0
        if a.tag_glob:
            tags = tags_done(repo, shas, a.tag_glob)
            if all(tags.values()):
                print(f"released {label}: {', '.join(t for t in tags.values() if t)}")
                return 0
            missing = [g for g, t in tags.items() if not t]
            active = sum(1 for r in runs if r["status"] in ACTIVE)
            wf_state = evaluate(runs, shas, contains, a.workflow or None)
            failed = [s["failed"] for s in wf_state.values() if s["failed"]]
            note = f"waiting for tag {', '.join(missing)}; {active} run(s) queued/running"
        else:
            wf_state = evaluate(runs, shas, contains, a.workflow or None)
            if wf_state and all(s["done"] for s in wf_state.values()):
                print(f"passed {label}: " + ", ".join(f"{n} {run_ref(s['done'], shas)}" for n, s in wf_state.items()))
                return 0
            pend = {n: s for n, s in wf_state.items() if not s["done"]}
            active = sum(s["active"] for s in pend.values())
            failed = [s["failed"] for s in pend.values() if s["failed"]]
            missing = list(pend) or ["(no run yet)"]
            note = f"waiting for {', '.join(missing)}; {active} run(s) queued/running"

        if a.once:
            # A finished red run with nothing queued/running is final: a wake probe must see it
            # (exit 1) instead of "not yet" forever.
            if failed and not active:
                print(f"stalled: newest finished run containing the commit ended "
                      f"{failed[0]['conclusion']}: {run_ref(failed[0], shas)}")
                return 1
            print(f"not yet: {note}")
            return 2
        if note != last:
            print(note, flush=True)
            last = note
        idle = 0 if active else idle + 1
        if idle >= 2 and time.monotonic() - start >= STARTUP_GRACE_S:
            reason = (f"newest finished run containing the commit ended {failed[0]['conclusion']}: {run_ref(failed[0], shas)}"
                      if failed else "no run containing the commit succeeded and none is queued or running")
            if a.dispatch and a.workflow and dispatches < 2:
                for wf in a.workflow:
                    subprocess.run(gh_cmd(slug, "workflow", "run", wf, "--ref", a.branch), cwd=repo, check=True)
                dispatches += 1
                idle = 0
                print(f"stalled ({reason}); dispatched {', '.join(a.workflow)} #{dispatches}", flush=True)
            else:
                print(f"stalled: {reason}")
                return 1
        if time.monotonic() >= deadline:
            print(f"timed out after {a.timeout_min:g} min; still {note}")
            return 2
        time.sleep(a.interval)
        try:
            fetch()
        except RuntimeError as e:  # stale refs only delay a verdict: contains() doesn't cache an unknown commit
            print(f"{e}; using the last fetch", flush=True)


if __name__ == "__main__":
    sys.exit(main())
