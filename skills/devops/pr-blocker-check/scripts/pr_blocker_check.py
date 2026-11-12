#!/usr/bin/env python3
"""One-shot, blocker-aware GitHub PR check. Never loops, never merges with admin override.

Usage: pr_blocker_check.py OWNER/REPO NUMBER [--json-file FILE]
Reads `gh pr view --json ...` (or FILE) and prints one JSON verdict.
Exit codes: 0 merged, 10 ready_to_merge, 20 wait, 30 repair/fix, 40 diagnose, 50 closed.

A merge queue (e.g. Graphite) lands a PR as a squash `... (#N)` pushed to the base branch and then
closes it (state CLOSED, mergedAt null). For such a PR the checker looks for that commit on the base
branch near closedAt; a failed lookup is unknown (diagnose), never `closed`.
"""
import datetime
import json
import subprocess
import sys

FIELDS = ("state,mergeable,mergeStateStatus,reviewDecision,headRefOid,statusCheckRollup,reviewRequests,"
          "mergedAt,closedAt,baseRefName")
WINDOW_BEFORE = datetime.timedelta(hours=2)
WINDOW_AFTER = datetime.timedelta(minutes=15)
PER_PAGE = 100


class LookupFailed(Exception):
    pass


def _iso(ts):
    return ts.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def base_commits(repo, pr):
    """Commits on the PR's base branch from closedAt-2h to closedAt+15m. Raises LookupFailed."""
    try:
        closed = datetime.datetime.fromisoformat(pr["closedAt"].replace("Z", "+00:00"))
        base = pr["baseRefName"]
        if not base:
            raise ValueError("no baseRefName")
    except (KeyError, AttributeError, TypeError, ValueError) as e:
        raise LookupFailed("closedAt/baseRefName missing: %s" % e)
    url = "repos/%s/commits?sha=%s&since=%s&until=%s&per_page=%d" % (
        repo, base, _iso(closed - WINDOW_BEFORE), _iso(closed + WINDOW_AFTER), PER_PAGE)
    try:
        res = subprocess.run(["gh", "api", url], capture_output=True, text=True)
    except OSError as e:
        raise LookupFailed("gh api failed: %s" % e)
    if res.returncode != 0:
        raise LookupFailed("gh api exit %d: %s" % (res.returncode, (res.stderr or res.stdout).strip()[:200]))
    try:
        commits = json.loads(res.stdout)
    except ValueError:
        raise LookupFailed("unparseable gh api output: %r" % res.stdout[:200])
    if not isinstance(commits, list):
        raise LookupFailed("unexpected gh api output: %r" % res.stdout[:200])
    return commits


def landed_commit(commits, number):
    """The sha of the commit whose first message line ends with `(#number)`, else None."""
    tag = "(#%s)" % number
    for c in commits:
        msg = ((c or {}).get("commit") or {}).get("message") or ""
        lines = msg.splitlines()
        if lines and lines[0].rstrip().endswith(tag):
            return c.get("sha")
    return None
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


def classify(pr, lookup=None):
    """Return {verdict, action, detail}. `action` names the next step; 'ask_user' is never emitted
    for a bare BLOCKED: unknown/policy blocks route to 'diagnose'. `lookup(pr)` returns the base-branch
    commits near closedAt (or raises) for a CLOSED, unmerged PR; without it such a PR is `closed`."""
    state = (pr.get("state") or "").upper()
    head = pr.get("headRefOid")
    if state == "MERGED":
        return dict(verdict="merged", action="verify_deployment", head=head, detail="merged; verify deploy")
    if state == "CLOSED":
        if lookup is not None and not pr.get("mergedAt"):
            try:
                commits = lookup(pr)
            except Exception as e:
                return dict(verdict="closed_unknown", action="diagnose", head=head,
                            detail="closed with mergedAt null; base-branch commit lookup failed (%s): "
                                   "landing unknown, retry; never read this as closed" % e)
            sha = landed_commit(commits, pr.get("number"))
            if sha:
                return dict(verdict="merged", action="verify_deployment", head=head, sha=sha,
                            detail="landed as %s by a merge queue (PR closed, not merged)" % sha[:10])
            if len(commits) >= PER_PAGE:
                return dict(verdict="closed_unknown", action="diagnose", head=head,
                            detail="closed with mergedAt null; %d+ base commits near closedAt, none ends "
                                   "(#%s): window truncated, check by hand" % (len(commits), pr.get("number")))
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
        "closed": 50, "closed_unknown": 40}


def main(argv):
    lookup = None
    if "--json-file" in argv:
        pr = json.load(open(argv[argv.index("--json-file") + 1]))
    else:
        repo, num = argv[1], argv[2]
        out = subprocess.run(["gh", "pr", "view", num, "-R", repo, "--json", FIELDS],
                             capture_output=True, text=True, check=True).stdout
        pr = json.loads(out)
        pr.setdefault("number", num)
        lookup = lambda p: base_commits(repo, p)  # noqa: E731
    v = classify(pr, lookup=lookup)
    print(json.dumps(v))
    return EXIT[v["verdict"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
