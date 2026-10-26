#!/usr/bin/env python3
"""Force-cancel superseded GitHub Actions runs latched on gate/report jobs.

Usage: ci_latch_sweep.py OWNER/REPO [--apply] [--gate REGEX]   (dry run without --apply)

A run is cancelled only if ALL hold, re-checked on a fresh read right before the POST:
- it is not completed, and run_attempt == 1 (a re-run is an explicit operator choice: never touched);
- its head_sha is not the current head (the open PR head for pull_request runs, the branch HEAD for push
  runs); an unknown or ambiguous head means keep;
- it has at least one job, and every non-completed job is a gate/report job (aggregator, timing report,
  summary). Any open real job means keep.
Listings are fully paginated; a failed read means no action. Exit 1 if any read or cancel failed, so a
caller never mistakes a failure for success. Prints created / first-job-start / last-real-job-done so
timing evidence survives the cancel. Needs a token with actions:write (gh auth or GH_TOKEN).
"""
import json
import re
import subprocess
import sys

DEFAULT_GATE = r"All required checks pass|CI timing report|ci-timings|aggregate"


class ReadError(Exception):
    pass


def gh_pages(path, key=None):
    """Every page of a GET via `gh api --paginate --slurp`; ReadError on any failure."""
    p = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True)
    if p.returncode != 0:
        raise ReadError(f"{path}: {p.stderr.strip()[:200]}")
    pages = json.loads(p.stdout or "[]")
    if key is None:
        return [item for page in pages for item in page]
    return [item for page in pages for item in page.get(key, [])]


def gh_one(path):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if p.returncode != 0:
        raise ReadError(f"{path}: {p.stderr.strip()[:200]}")
    return json.loads(p.stdout)


def current_head(run, pr_heads, branch_heads):
    if run["event"] == "pull_request":
        heads = pr_heads.get(run["head_branch"], set())
        return next(iter(heads)) if len(heads) == 1 else None
    if run["event"] == "push":
        return branch_heads.get(run["head_branch"])
    return None


def latched(run, jobs, head, gate):
    """The selection rule, kept pure so it can be unit-tested."""
    if run["status"] == "completed" or run.get("run_attempt", 1) != 1:
        return False
    if head is None or run["head_sha"] == head or not jobs:
        return False
    open_jobs = [j["name"] for j in jobs if j["status"] != "completed"]
    return bool(open_jobs) and all(gate.search(n) for n in open_jobs)


def main(repo, apply, gate):
    try:
        pr_heads = {}
        for p in gh_pages(f"repos/{repo}/pulls?state=open&per_page=100"):
            pr_heads.setdefault(p["head"]["ref"], set()).add(p["head"]["sha"])
        runs = {}
        for st in ("queued", "in_progress", "pending", "waiting"):
            for r in gh_pages(f"repos/{repo}/actions/runs?status={st}&per_page=100", "workflow_runs"):
                runs[r["id"]] = r
        branch_heads = {b: gh_one(f"repos/{repo}/commits/{b}")["sha"]
                        for b in {r["head_branch"] for r in runs.values() if r["event"] == "push"}}
    except ReadError as e:
        print(f"read failed, no action: {e}", file=sys.stderr)
        return 1
    failed = False
    for rid, run in sorted(runs.items()):
        head = current_head(run, pr_heads, branch_heads)
        try:
            jobs = gh_pages(f"repos/{repo}/actions/runs/{rid}/jobs?per_page=100", "jobs")
            if not latched(run, jobs, head, gate):
                continue
            run = gh_one(f"repos/{repo}/actions/runs/{rid}")  # revalidate the exact run before acting
            jobs = gh_pages(f"repos/{repo}/actions/runs/{rid}/jobs?per_page=100", "jobs")
            if not latched(run, jobs, head, gate):
                continue
        except ReadError as e:
            print(f"skip {rid}: {e}", file=sys.stderr)
            failed = True
            continue
        real = [j for j in jobs if not gate.search(j["name"]) and j.get("started_at")]
        first = min((j["started_at"] for j in real), default=None)
        last = max((j["completed_at"] for j in real if j.get("completed_at")), default=None)
        open_jobs = [j["name"] for j in jobs if j["status"] != "completed"]
        print(f"{'CANCEL' if apply else 'would cancel'} {rid} {run['name']} {run['head_branch']} "
              f"{run['head_sha'][:8]} (current {(head or '')[:8]}) open={open_jobs} created={run['created_at']} "
              f"first_job={first} last_real_job_done={last}")
        if apply:
            p = subprocess.run(["gh", "api", "-X", "POST", f"repos/{repo}/actions/runs/{rid}/force-cancel"],
                               capture_output=True, text=True)
            if p.returncode != 0:
                print(f"cancel {rid} failed: {p.stderr.strip()[:200]}", file=sys.stderr)
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    gate = DEFAULT_GATE
    if "--gate" in argv:
        i = argv.index("--gate")
        gate = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1 or "/" not in args[0]:
        sys.exit(__doc__)
    sys.exit(main(args[0], "--apply" in argv, re.compile(gate, re.I)))
