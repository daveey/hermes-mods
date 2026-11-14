#!/usr/bin/env python3
"""Immediate, exact-head PR landing. Never changes direct-push or server policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

REPO = "daveey/hermes-mods"
BRANCH = "main"


class Refused(RuntimeError):
    pass


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise Refused(f"GitHub read/write failed: {result.stderr.strip()[:300]}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise Refused("GitHub returned invalid JSON") from exc


def pages(read, endpoint, key=None):
    rows = []
    for page in range(1, 31):
        data = read("api", f"{endpoint}{'&' if '?' in endpoint else '?'}per_page=100&page={page}")
        batch = data.get(key) if key and isinstance(data, dict) else data if not key else None
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise Refused("Unreadable or malformed paginated GitHub inventory")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise Refused("GitHub inventory exceeds bounded pagination")


def current_pr(read, number, head):
    pr = read("api", f"repos/{REPO}/pulls/{number}")
    if (not isinstance(pr, dict) or pr.get("number") != number or pr.get("state") != "open"
            or pr.get("draft") is not False or pr.get("merged") is not False
            or pr.get("head", {}).get("sha") != head
            or pr.get("base", {}).get("ref") != BRANCH
            or pr.get("base", {}).get("repo", {}).get("full_name", "").lower() != REPO):
        raise Refused("PR is not an open, non-draft main PR at the requested exact head")
    return pr


def requirements(read):
    # Branch summary includes classic protection; branch rules includes applicable rulesets.
    branch = read("api", f"repos/{REPO}/branches/{BRANCH}")
    protection = branch.get("protection") if isinstance(branch, dict) else None
    checks = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(checks, dict) or not isinstance(checks.get("contexts"), list):
        raise Refused("Cannot establish classic required-check inventory")
    required = [(name, None) for name in checks["contexts"]]
    for check in checks.get("checks", []):
        required.append((check["context"], check.get("app_id")))
    for rule in pages(read, f"repos/{REPO}/rules/branches/{BRANCH}"):
        if rule.get("type") == "required_status_checks":
            for check in rule["parameters"]["required_status_checks"]:
                required.append((check["context"], check.get("integration_id")))
    if any(not isinstance(name, str) or not name for name, _ in required):
        raise Refused("Malformed required-check identity")
    return set(required)


def ci(read, head):
    required = requirements(read)
    runs = pages(read, f"repos/{REPO}/commits/{head}/check-runs?filter=latest", "check_runs")
    statuses = pages(read, f"repos/{REPO}/commits/{head}/statuses")
    latest = {}
    for run in runs:
        if run.get("head_sha") != head or not run.get("name") or not isinstance(run.get("id"), int):
            raise Refused("Check run does not identify the exact head")
        key = (run["name"], run.get("app", {}).get("id"))
        value = (run["id"], run.get("status") == "completed" and run.get("conclusion") == "success")
        if key not in latest or value[0] > latest[key][0]:
            latest[key] = value
    for status in statuses:
        if not status.get("context") or not isinstance(status.get("id"), int):
            raise Refused("Malformed commit status")
        key = (status["context"], None)
        value = (status["id"], status.get("state") == "success")
        if key not in latest or value[0] > latest[key][0]:
            latest[key] = value
    if not latest:
        raise Refused("No exact-head CI evidence")
    if required:
        for name, app in required:
            matches = [ok for (n, a), (_, ok) in latest.items() if n == name and (app in (None, -1) or a == app)]
            if not matches or not all(matches):
                raise Refused(f"Required check {name!r} is missing or not successful")
    elif not all(ok for _, ok in latest.values()):
        raise Refused("No required checks configured: ALL exact-head checks/statuses must succeed")
    return {"mode": "required" if required else "all", "observed": len(latest)}


def reviews(read, number, head, pr):
    effective = {}
    for review in pages(read, f"repos/{REPO}/pulls/{number}/reviews"):
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            user = review.get("user") or {}
            login = user.get("login")
            if not login or not isinstance(review.get("id"), int):
                raise Refused("Malformed review identity")
            previous = effective.get(login.lower(), {})
            key = (str(review.get("submitted_at") or ""), review["id"])
            if key > (str(previous.get("submitted_at") or ""), previous.get("id", -1)):
                effective[login.lower()] = review
    if any(r["state"] == "CHANGES_REQUESTED" for r in effective.values()):
        raise Refused("Effective changes-requested review must be resolved")
    return effective


def approval(read, number, head, pr, review_task, implementation_task):
    effective = reviews(read, number, head, pr)
    if review_task:
        # The established validator checks run/session independence and authenticated remote
        # execution custody. No caller-supplied receipt or COMMENT prose is an approval.
        candidates = [Path.home() / ".hermes/lib/core-tools/hermes-core-wt-land",
                      Path.home() / ".hermes/bin/hermes-core-wt-land",
                      Path.home() / "code/hermes/home/bin/hermes-core-wt-land"]
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise Refused("Installed Kanban review validator unavailable; use independent GitHub approval")
        validator = runpy.run_path(str(path), run_name="push_guard_review_validator")

        class Reader:
            def json(self, *args, **kwargs):
                try:
                    return read(*args), None
                except Exception as exc:
                    return None, str(exc)

        receipt = validator["review_gate"](Reader(), REPO, {"number": number}, head,
                                           pr["head"]["ref"], BRANCH, review_task,
                                           impl_task=implementation_task,
                                           approved_sha="", pr_reuse_s=0.0)
        if (receipt.get("verdict") != "approve" or receipt.get("reviewed_sha") != head
                or receipt.get("reviewed_pr") != f"https://github.com/{REPO}/pull/{number}"
                or receipt.get("non_blocking")):
            raise Refused("Invalid review receipt or unanswered review points")
        return receipt
    author = pr.get("user", {}).get("login")
    user = read("api", "user")
    lander = user.get("login") if isinstance(user, dict) else None
    if not author or not lander:
        raise Refused("Cannot establish author/lander independence")
    excluded = {author.lower(), lander.lower()}
    for login, review in effective.items():
        if (login not in excluded and review["state"] == "APPROVED"
                and review.get("commit_id") == head and review["user"].get("type") == "User"):
            return {"source": "github", "id": review["id"], "reviewed_sha": head}
    raise Refused("No independent review approves the current exact head")


def land(number, head, review_task="", implementation_task="", read=gh):
    if not re.fullmatch(r"[0-9a-f]{40}", head) or number <= 0:
        raise Refused("A positive PR number and full lowercase 40-hex head are required")
    if review_task and (not implementation_task or review_task == implementation_task):
        raise Refused("Kanban review requires a distinct implementation task identity")
    pr = current_pr(read, number, head)
    checks = ci(read, head)
    review = approval(read, number, head, pr, review_task, implementation_task)
    # Refuse base/head movement during preflight. The REST SHA is a server-side head CAS.
    fresh = current_pr(read, number, head)
    if fresh["base"].get("sha") != pr["base"].get("sha"):
        raise Refused("Base moved during preflight; retry against fresh evidence")
    result = read("api", f"repos/{REPO}/pulls/{number}/merge", "--method", "PUT",
                  "-f", f"sha={head}", "-f", "merge_method=rebase")
    if not isinstance(result, dict) or result.get("merged") is not True:
        raise Refused("Merge was not confirmed")
    after = read("api", f"repos/{REPO}/pulls/{number}")
    if after.get("merged") is not True or after.get("head", {}).get("sha") != head:
        raise Refused("Merge response succeeded but exact PR readback is unverified; do not retry blindly")
    return {"repo": REPO, "pr": number, "head": head, "merged": True,
            "merge_commit_sha": after.get("merge_commit_sha"), "checks": checks, "review": review}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head", required=True)
    parser.add_argument("--review-task", default="")
    parser.add_argument("--implementation-task", default="")
    args = parser.parse_args()
    try:
        result = land(args.pr, args.head, args.review_task, args.implementation_task)
    except Exception as exc:
        print(f"PR LANDING REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
