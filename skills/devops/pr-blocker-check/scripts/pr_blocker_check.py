#!/usr/bin/env python3
"""One-shot, blocker-aware GitHub PR check. Never loops, never merges with admin override.

Usage: pr_blocker_check.py OWNER/REPO NUMBER [--json-file FILE]
Reads `gh pr view --json ...` (or FILE) and prints one JSON verdict.
Exit codes: 0 merged, 10 ready_to_merge, 20 wait, 30 repair/fix, 40 diagnose, 50 closed.
"""
import json
import subprocess
import sys

FIELDS = "state,mergeable,mergeStateStatus,reviewDecision,headRefOid,statusCheckRollup,reviewRequests,mergedAt"
BAD = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING = {"PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED"}


def check_states(rollup):
    failed, pending = [], []
    for c in rollup or []:
        name = c.get("name") or c.get("context") or "?"
        status = c.get("status")
        verdict = (c.get("conclusion") or c.get("state") or "").upper()
        if status and status.upper() != "COMPLETED":
            pending.append(name)
        elif verdict in BAD:
            failed.append(name)
        elif verdict in PENDING or not verdict:
            pending.append(name)
    return failed, pending


def classify(pr):
    """Return {verdict, action, detail}. `action` names the next step; 'ask_user' is never emitted
    for a bare BLOCKED: unknown/policy blocks route to 'diagnose'."""
    state = (pr.get("state") or "").upper()
    head = pr.get("headRefOid")
    if state == "MERGED":
        return dict(verdict="merged", action="verify_deployment", head=head, detail="merged; verify deploy")
    if state == "CLOSED":
        return dict(verdict="closed", action="report_closed", head=head, detail="closed unmerged")
    mergeable = (pr.get("mergeable") or "").upper()
    mss = (pr.get("mergeStateStatus") or "").upper()
    if mergeable == "CONFLICTING" or mss == "DIRTY":
        return dict(verdict="conflicting", action="repair_conflicts", head=head,
                    detail="rebase/merge base and resolve conflicts")
    failed, pending = check_states(pr.get("statusCheckRollup"))
    if failed:
        return dict(verdict="checks_failed", action="fix_failing_checks", head=head,
                    detail="failed: " + ", ".join(failed))
    if pending:
        return dict(verdict="checks_pending", action="schedule_retry", head=head,
                    detail="pending: " + ", ".join(pending))
    review = (pr.get("reviewDecision") or "").upper()
    if review == "CHANGES_REQUESTED":
        return dict(verdict="changes_requested", action="address_review", head=head,
                    detail="address review comments")
    if review == "REVIEW_REQUIRED":
        requested = [r.get("login") or r.get("name") for r in pr.get("reviewRequests") or []]
        return dict(verdict="review_required", action="request_or_await_review", head=head,
                    detail="fresh approval needed; requested=%s; no self-approve/admin override" % requested)
    if mss in ("CLEAN", "HAS_HOOKS", "UNSTABLE") or (mergeable == "MERGEABLE" and mss not in ("BLOCKED", "BEHIND")):
        return dict(verdict="ready", action="attempt_normal_merge", head=head,
                    detail="checks green and review satisfied; use normal merge, not --admin")
    if mss == "BEHIND":
        return dict(verdict="behind", action="update_branch", head=head, detail="update branch from base")
    return dict(verdict="blocked_unknown", action="diagnose", head=head,
                detail="BLOCKED with checks green and no review gate: inspect rulesets, required "
                       "checks, merge queue; do not guess or ask the user until proven")


EXIT = {"merged": 0, "ready": 10, "checks_pending": 20, "conflicting": 30, "checks_failed": 30,
        "changes_requested": 30, "behind": 30, "review_required": 40, "blocked_unknown": 40,
        "closed": 50}


def main(argv):
    if "--json-file" in argv:
        pr = json.load(open(argv[argv.index("--json-file") + 1]))
    else:
        repo, num = argv[1], argv[2]
        out = subprocess.run(["gh", "pr", "view", num, "-R", repo, "--json", FIELDS],
                             capture_output=True, text=True, check=True).stdout
        pr = json.loads(out)
    v = classify(pr)
    print(json.dumps(v))
    return EXIT[v["verdict"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
