#!/usr/bin/env python3
"""Cancel one GitHub Actions run, only if it is still active AND still on the attempt you diagnosed.

Usage:
  ci_run_cancel.py --run-id ID --gh-repo OWNER/REPO --expect-attempt N [--reason TEXT] [--force]
                   [--settle-seconds S]

This is the only sanctioned cancel path (push-guard refuses a bare `gh run cancel` / `.../runs/<id>/cancel`).
In one call it reads the run's `status` and `run_attempt` and cancels only when the status is still active
(queued, in_progress, waiting, pending, requested) and `run_attempt == N`. A higher attempt means someone (usually
the run's owner card) re-queued it after your read: leave it. A superseded push run is attempt 1.

Exit codes:
  0  cancel requested and read back completed/cancelled (or still settling under --settle-seconds 0 with --force)
  2  usage error or a failed GitHub read/POST (nothing assumed)
  3  refused: the run is no longer active, or its attempt is not N; prints the live attempt and status
  4  cancel requested but the run did not reach `completed` within --settle-seconds (a queued gate job can hold
     it; re-run with --force), or the attempt moved during the cancel (a newer attempt may have been hit)
"""
import argparse
import json
import subprocess
import sys
import time

ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


class GhError(Exception):
    pass


def gh(*args):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise GhError(f"gh {' '.join(args[:4])}: exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def read_run(repo, rid):
    out = gh("api", f"repos/{repo}/actions/runs/{rid}")
    try:
        run = json.loads(out)
        return {"status": run["status"], "conclusion": run.get("conclusion"),
                "attempt": int(run["run_attempt"]), "url": run.get("html_url", "")}
    except (ValueError, KeyError, TypeError) as exc:
        raise GhError(f"unreadable run JSON: {exc}")


def show(run):
    return f"attempt={run['attempt']} status={run['status']} conclusion={run['conclusion']} {run['url']}".rstrip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--run-id", required=True, type=int)
    ap.add_argument("--gh-repo", "-R", required=True)
    ap.add_argument("--expect-attempt", required=True, type=int,
                    help="the run_attempt you diagnosed; any other live attempt refuses")
    ap.add_argument("--reason", default="", help="why (printed for the record)")
    ap.add_argument("--force", action="store_true", help="use force-cancel (a cancelled run stuck queued)")
    ap.add_argument("--settle-seconds", type=float, default=30.0, help="how long to wait for `completed`")
    a = ap.parse_args(argv)
    repo, rid = a.gh_repo, a.run_id
    try:
        before = read_run(repo, rid)
    except GhError as exc:
        print(f"ci_run_cancel: read failed, nothing cancelled: {exc}", file=sys.stderr)
        return 2
    print(f"before: {show(before)}")
    if before["status"] not in ACTIVE:
        print(f"refused: run {rid} is not active ({show(before)}); nothing to cancel")
        return 3
    if before["attempt"] != a.expect_attempt:
        print(f"refused: run {rid} is on attempt={before['attempt']} status={before['status']}, not the attempt "
              f"{a.expect_attempt} you diagnosed. Someone re-queued it after your read (often its owner card): "
              f"leave it, or re-diagnose this attempt and pass --expect-attempt {before['attempt']}.")
        return 3
    endpoint = "force-cancel" if a.force else "cancel"
    print(f"cancel: run {rid} attempt {before['attempt']} via {endpoint}" + (f" ({a.reason})" if a.reason else ""))
    try:
        gh("api", "-X", "POST", f"repos/{repo}/actions/runs/{rid}/{endpoint}")
    except GhError as exc:
        print(f"ci_run_cancel: cancel request failed: {exc}", file=sys.stderr)
        return 2
    deadline = time.monotonic() + max(a.settle_seconds, 0)
    while True:
        try:
            after = read_run(repo, rid)
        except GhError as exc:
            print(f"ci_run_cancel: cancel requested; read-back failed: {exc}", file=sys.stderr)
            return 2
        if after["attempt"] != before["attempt"] or after["status"] == "completed" or time.monotonic() >= deadline:
            break
        time.sleep(3)
    print(f"after: {show(after)}")
    if after["attempt"] != before["attempt"]:
        print(f"WARNING: the attempt moved from {before['attempt']} to {after['attempt']} during the cancel; check "
              f"whether the new attempt was hit and tell its owner.")
        return 4
    if after["status"] != "completed":
        print(f"not settled: cancel requested but run {rid} is still {after['status']}; if a queued gate job holds "
              f"it, re-run with --force --expect-attempt {after['attempt']}.")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
