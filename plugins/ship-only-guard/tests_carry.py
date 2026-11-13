"""Patch-identical update-branch carry of an exact-head approval (t_01a1d666f7d34164). Never executes a merge.

#1812 (bc33e1eb82 -> 21cbd60ac5), #1837 (20bf041a47 -> d49e9bcfa5) and #2438 (1fd079c68f -> b1e5d5ef46) were
approved, then `update-branch` merged state into the head without changing the PR's own patch; the exact-head
gate refused all three. gh reads are faked here; `git patch-id --stable` runs for real on the faked diffs.
"""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("carry_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
REAL_RUN = subprocess.run

R = "example/protected"
H0 = "1fd079c68f8cc4aa5f887df5e6d3668945b68149"     # #2438's approved head
H = "b1e5d5ef46" + "0" * 30                        # its update-branch merge
BASE_TIP = "941597d6ff" + "0" * 30                 # the state commit merged in
STATE_OLD = "ded1e96f69" + "0" * 30
WORK = "5bf7cbdfb8" + "0" * 30                     # a non-merge commit pushed after the approval
CMD = ["gh", "pr", "merge", "2438", "--repo", R, "--rebase", "--match-head-commit", H]

PATCH = """diff --git a/home/bin/hermes-sync b/home/bin/hermes-sync
index 1111111..2222222 100755
--- a/home/bin/hermes-sync
+++ b/home/bin/hermes-sync
@@ -%d,6 +%d,7 @@ def peer_copy(src, dst):
     proc = spawn(src)
     try:
         return proc.wait()
+    except KeyboardInterrupt: kill_both(proc)
     finally:
         proc.close()
     return 1
"""
OLD_DIFF = PATCH % (40, 40)
# The same patch after state moved under it: other line numbers and blob ids, so the raw text differs.
NEW_DIFF = PATCH.replace("1111111..2222222", "3333333..4444444") % (52, 52)
CHANGED_DIFF = NEW_DIFF.replace("kill_both(proc)", "kill_both(proc, wait=True)")


def review(body, commit=H0, at="2026-11-13T17:08:49Z", state="COMMENTED", login="you"):
    return {"id": "PRR_x", "author": {"login": login}, "authorAssociation": "MEMBER", "state": state,
            "submittedAt": at, "body": body, "commit": {"oid": commit}}


def comment(body, at="2026-11-13T17:20:00Z", login="you"):
    return {"id": "IC_x", "author": {"login": login}, "authorAssociation": "MEMBER", "createdAt": at, "body": body}


APPROVAL = review(f"Independent exact-head review at {H0} (card t_x). **Verdict: approve**")


def payload(reviews=(APPROVAL,), comments=()):
    return {"number": 2438, "state": "OPEN", "baseRefName": "state", "headRefOid": H, "baseRefOid": STATE_OLD,
            "url": f"https://github.com/{R}/pull/2438", "reviews": list(reviews), "comments": list(comments),
            "files": [{"path": "home/bin/hermes-sync"}],
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}


def node(sha, *parents):
    return {"sha": sha, "parents": [{"sha": p} for p in parents]}


UPDATE = {"status": "ahead", "ahead_by": 2, "behind_by": 0, "total_commits": 2,
          "commits": [node(BASE_TIP, STATE_OLD), node(H, H0, BASE_TIP)]}
ON_STATE = {"status": "ahead", "ahead_by": 3, "behind_by": 0, "total_commits": 3, "commits": [], "files": []}
IDENTICAL = {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0, "commits": [], "files": []}


def fake(monkeypatch, data=None, answers=None):
    """gh pr view answers `data`; `gh api <path>` answers answers[path] (dict -> JSON, str -> text, int -> that
    exit code, missing -> exit 1); the base-drift compare answers IDENTICAL. Real `git patch-id` runs."""
    answers = {f"repos/{R}/compare/{H0}...{H}": UPDATE, f"repos/{R}/compare/{BASE_TIP}...state": ON_STATE,
               f"repos/{R}/compare/state...{H0}": OLD_DIFF, f"repos/{R}/compare/state...{H}": NEW_DIFF,
               **(answers or {})}
    calls = []

    def run(argv, **kwargs):
        if argv[:2] == ["git", "patch-id"]:
            return REAL_RUN(argv, **kwargs)
        calls.append(argv)
        assert not kwargs.get("shell") and kwargs.get("timeout", 99) <= 20
        if argv[:3] == ["gh", "pr", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(data or payload()))
        assert argv[:2] == ["gh", "api"] and "-X" not in argv and "--method" not in argv, argv   # reads only
        path = argv[-1]
        if argv[2:4] == ["-H", "Accept: application/vnd.github.diff"]:
            assert path.startswith(f"repos/{R}/compare/state...")
        answer = answers.get(path)
        if path.endswith("...state") and path not in answers:
            answer = IDENTICAL   # the base-drift compare of baseRefOid...state
        if answer is None or isinstance(answer, int):
            return SimpleNamespace(returncode=answer or 1, stdout="", stderr="HTTP 502")
        return SimpleNamespace(returncode=0, stdout=answer if isinstance(answer, str) else json.dumps(answer))
    monkeypatch.setattr(g.subprocess, "run", run)
    return calls


def check():
    return g.check_argv(CMD, repos={R}, env={"GH_REPO": R})


def test_real_patch_id_ignores_line_numbers_and_blob_ids():
    assert OLD_DIFF != NEW_DIFF
    assert g._patch_id(OLD_DIFF, None) == g._patch_id(NEW_DIFF, None) != g._patch_id(CHANGED_DIFF, None)


def test_patch_identical_update_branch_head_admitted(monkeypatch):
    calls = fake(monkeypatch)
    assert check() is None
    paths = [c[-1] for c in calls[1:]]
    assert paths[:4] == [f"repos/{R}/compare/{H0}...{H}", f"repos/{R}/compare/{BASE_TIP}...state",
                         f"repos/{R}/compare/state...{H0}", f"repos/{R}/compare/state...{H}"]
    assert paths[4:] == [f"repos/{R}/compare/{STATE_OLD}...state"]   # then the base-drift gate still runs


def test_base_drift_still_applies_after_a_carry(monkeypatch):
    drift = {"status": "ahead", "ahead_by": 1, "behind_by": 0, "total_commits": 1,
             "commits": [{"sha": "adc4b89b37" + "0" * 30, "commit": {"message": "hermes-sync: rework"}}],
             "files": [{"filename": "home/bin/hermes-sync", "status": "modified"}]}
    fake(monkeypatch, payload(), {f"repos/{R}/compare/{STATE_OLD}...state": drift})
    why = check()
    assert why and "adc4b89b37" in why and "update-branch" in why


def test_changed_patch_refused(monkeypatch):
    fake(monkeypatch, answers={f"repos/{R}/compare/state...{H}": CHANGED_DIFF})
    why = check()
    assert why and "no approval of its exact head" in why and H in why
    assert f"approval of earlier head {H0[:10]} does not carry" in why and "patch changed" in why


def test_non_merge_commit_after_approved_head_refused(monkeypatch):
    # H0 -> WORK (a plain commit) -> H (merge of state): the PR changed after the review even if the
    # patch-id were to match, so no diff is read at all.
    cmp = {"status": "ahead", "ahead_by": 3, "behind_by": 0, "total_commits": 3,
           "commits": [node(WORK, H0), node(BASE_TIP, STATE_OLD), node(H, WORK, BASE_TIP)]}
    calls = fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": cmp})
    why = check()
    assert why and f"{WORK[:10]} after it is not a merge of the base" in why
    assert not any("state..." in c[-1] and "Accept" in " ".join(c) for c in calls)


def test_head_directly_after_a_non_merge_refused(monkeypatch):
    cmp = {"status": "ahead", "ahead_by": 1, "behind_by": 0, "total_commits": 1, "commits": [node(H, H0)]}
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": cmp})
    assert "is not a merge of the base" in check()


def test_merge_of_something_other_than_the_base_refused(monkeypatch):
    fake(monkeypatch, answers={f"repos/{R}/compare/{BASE_TIP}...state": {"status": "diverged", "ahead_by": 2,
                                                                          "behind_by": 4}})
    assert f"merge parent {BASE_TIP[:10]} is not on state" in check()


@pytest.mark.parametrize("status", ["diverged", "behind", "identical"])
def test_rebased_or_force_pushed_head_refused(monkeypatch, status):
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": {"status": status, "behind_by": 3, "commits": []}})
    assert f"{H0[:10]} is not an ancestor of the head" in check()


@pytest.mark.parametrize("path,answer", [
    (f"repos/{R}/compare/{H0}...{H}", None),           # compare read fails
    (f"repos/{R}/compare/{H0}...{H}", 502),
    (f"repos/{R}/compare/{H0}...{H}", "not json"),
    (f"repos/{R}/compare/{H0}...{H}", "[]"),
    (f"repos/{R}/compare/{BASE_TIP}...state", None),   # ancestry read fails
    (f"repos/{R}/compare/state...{H0}", None),         # a diff read fails
    (f"repos/{R}/compare/state...{H}", ""),            # an empty diff
    (f"repos/{R}/compare/state...{H}", "garbage that is no diff"),
])
def test_unreadable_compare_refused(monkeypatch, path, answer):
    fake(monkeypatch, answers={path: answer if answer is not None else 0})
    why = check()
    assert why and "no approval of its exact head" in why and "cannot be shown" in why


def test_missing_compare_commit_list_falls_back_to_a_git_commit_read(monkeypatch):
    big = {"status": "ahead", "ahead_by": 900, "behind_by": 0, "total_commits": 900, "commits": []}
    calls = fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": big,
                                       f"repos/{R}/git/commits/{H}": node(H, H0, BASE_TIP)})
    assert check() is None
    assert [f"repos/{R}/git/commits/{H}"] == [c[-1] for c in calls if "/git/commits/" in c[-1]]
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": big})   # and that read failing refuses
    assert "cannot be shown" in check()


def test_more_merges_than_the_cap_refused(monkeypatch):
    prev, commits = H0, []
    for i in range(g._CARRY_MERGES):
        sha = f"{i + 1}" * 40
        commits.append(node(sha, prev, BASE_TIP))
        prev = sha
    commits.append(node(H, prev, BASE_TIP))
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": {"status": "ahead", "behind_by": 0,
                                                                  "commits": commits}})
    assert f"more than {g._CARRY_MERGES} commits" in check()


def test_two_base_merges_admitted(monkeypatch):
    # #1812: approved head bc33e1eb82 was itself a merge of state; 21cbd60ac5 merged state again.
    mid, tip2 = "c" * 40, "d" * 40
    cmp = {"status": "ahead", "behind_by": 0, "commits": [node(mid, H0, BASE_TIP), node(H, mid, tip2)]}
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": cmp, f"repos/{R}/compare/{tip2}...state": IDENTICAL})
    assert check() is None


@pytest.mark.parametrize("later", [
    review("Request changes: B1.", commit=H0, at="2026-11-13T17:30:00Z"),
    review("lgtm", commit=H0, state="CHANGES_REQUESTED", at="2026-11-13T17:30:00Z"),
    review("VERDICT: REQUEST_CHANGES", commit="e" * 40, at="2026-11-13T17:30:00Z"),
    review("Approve.", commit=H0, at=None),
    comment("VERDICT: REQUEST_CHANGES (blocking: the interrupt path leaks the child)"),
    comment(f"Independent review @ {H0}: I cannot approve this yet."),
])
def test_blocking_verdict_after_the_approval_refused(monkeypatch, later):
    calls = fake(monkeypatch, payload([APPROVAL, later] if "submittedAt" in later else [APPROVAL],
                                      [] if "submittedAt" in later else [later]))
    why = check()
    assert why and "does not carry" in why and "blocking verdict came at or after" in why
    assert len(calls) == 1   # refused before any compare read


def test_block_before_the_approval_does_not_stop_the_carry(monkeypatch):
    early = review("Request changes: B1.", commit="e" * 40, at="2026-11-13T16:00:00Z")
    fake(monkeypatch, payload([early, APPROVAL]))
    assert check() is None


def test_reply_restating_a_verdict_is_no_block(monkeypatch):
    reply = comment(f"<!-- hermes-review-reply -->\nReply to review 5895102446 (exact head {H0[:10]}): 1. fixed.")
    fake(monkeypatch, payload([APPROVAL], [reply]))
    assert check() is None


def test_comment_posted_earlier_approval_carries(monkeypatch):
    fake(monkeypatch, payload([], [comment(f"**Independent exact-head review: approve** @ `{H0}` (card t_x)")]))
    assert check() is None


def test_comment_naming_head_and_base_tries_the_approved_head(monkeypatch):
    # The newest-first candidates are the base sha and H0 (same comment); the base sha fails, H0 carries.
    text = f"VERDICT: APPROVE at {H0} (CI base {BASE_TIP})"
    fake(monkeypatch, payload([], [comment(text)]), {f"repos/{R}/compare/{BASE_TIP}...{H}": {
        "status": "ahead", "behind_by": 0, "commits": [node(H, H0, BASE_TIP)]}})
    assert check() is None


def test_bot_approval_never_carries(monkeypatch):
    calls = fake(monkeypatch, payload([review("Approve.", login="ci-review[bot]")]))
    assert "no approval of its exact head" in check() and len(calls) == 1


def test_exact_head_block_is_not_overridden_by_a_carry(monkeypatch):
    for at in ("2026-11-13T17:40:00Z", "2026-11-13T16:40:00Z"):   # after, and even before, the H0 approval
        block = review("Request changes: B2.", commit=H, at=at)
        calls = fake(monkeypatch, payload([APPROVAL, block]))
        assert "latest exact-head verdict requests changes" in check() and len(calls) == 1


def test_empty_pr_diff_refused_by_name(monkeypatch):
    fake(monkeypatch, answers={f"repos/{R}/compare/state...{H}": "\n"})
    assert "a PR diff is empty" in check()
