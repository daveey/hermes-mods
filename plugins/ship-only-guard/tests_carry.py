"""Patch-identical update-branch carry of an exact-head approval (t_01a1d666f7d34164). Never executes a merge.

#1812 (bc33e1eb82 -> 21cbd60ac5), #1837 (20bf041a47 -> d49e9bcfa5) and #2438 (1fd079c68f -> b1e5d5ef46) were
approved, then `update-branch` merged state into the head without changing the PR's own patch; the exact-head
gate refused all three. gh reads are faked here; `git patch-id --verbatim` and `git merge-file` run for real on
the faked diffs and blobs.
"""
import base64
import hashlib
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


@pytest.fixture(autouse=True)
def _no_land_turn(monkeypatch):
    # These cases pin the carry's gh calls; the land turn has its own fakes in tests_turn.py.
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")
    # Existing API/policy fixtures do not own the network Git verifier. Its real-Git
    # fixtures below explicitly restore the helper; never contact GitHub in tests.
    if hasattr(g, "_git_merge_tree_refusal"):
        monkeypatch.setattr(g, "_git_merge_tree_refusal", lambda *a: "")

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


def merge_base(sha):
    """compare/<first parent>...<second parent>, as GitHub answers it: the merge base is all the carry reads."""
    return {"status": "diverged", "ahead_by": 1, "behind_by": 1, "merge_base_commit": {"sha": sha},
            "commits": [], "files": []}


def tree(files, sha="0" * 40):
    """`git/trees/<sha>?recursive=1` for {path: blob sha or (mode, blob sha[, size])}, with its implied dirs."""
    entries, dirs = [], set()
    for path, e in sorted(files.items()):
        mode, blob, size = ("100644", e, 1) if isinstance(e, str) else (*e, 1)[:3]
        parts = path.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
        entries.append({"path": path, "mode": mode, "type": "commit" if mode == "160000" else "blob",
                        "sha": blob, "size": size})
    entries += [{"path": d, "mode": "040000", "type": "tree", "sha": "7" * 40} for d in sorted(dirs)]
    return {"sha": sha, "truncated": False, "tree": entries}


# The clean update-branch merge H of H0 and BASE_TIP over their merge base STATE_OLD: the PR changed
# home/bin/hermes-sync, state changed docs/notes.md, H holds both.
SYNC_OLD, SYNC_PR, NOTES_OLD, NOTES_STATE = "a1" * 20, "a2" * 20, "b1" * 20, "b2" * 20
FILES = {STATE_OLD: {"home/bin/hermes-sync": SYNC_OLD, "docs/notes.md": NOTES_OLD},
         H0: {"home/bin/hermes-sync": SYNC_PR, "docs/notes.md": NOTES_OLD},
         BASE_TIP: {"home/bin/hermes-sync": SYNC_OLD, "docs/notes.md": NOTES_STATE},
         H: {"home/bin/hermes-sync": SYNC_PR, "docs/notes.md": NOTES_STATE}}


def tree_path(sha):
    return f"repos/{R}/git/trees/{sha}?recursive=1"


def fake(monkeypatch, data=None, answers=None):
    """gh pr view answers `data`; `gh api <path>` answers answers[path] (dict -> JSON, str -> text, int -> that
    exit code, (code, answer) -> that answer with that exit code, missing -> exit 1); the base-drift compare
    answers IDENTICAL. Like GitHub, `compare/state...X` without the diff Accept header answers the compare
    JSON, not the diff. Real `git patch-id` and `git merge-file` run. The merge-tree reads default to FILES."""
    answers = {f"repos/{R}/compare/{H0}...{H}": UPDATE, f"repos/{R}/compare/{BASE_TIP}...state": ON_STATE,
               f"repos/{R}/compare/state...{H0}": OLD_DIFF, f"repos/{R}/compare/state...{H}": NEW_DIFF,
               f"repos/{R}/compare/{H0}...{BASE_TIP}": merge_base(STATE_OLD),
               **{tree_path(sha): tree(files, sha) for sha, files in FILES.items()},
               **(answers or {})}
    calls = []

    def run(argv, **kwargs):
        if argv[:2] in (["git", "patch-id"], ["git", "merge-file"]):
            return REAL_RUN(argv, **kwargs)
        calls.append(argv)
        assert not kwargs.get("shell") and kwargs.get("timeout", 99) <= 20
        if argv[:3] == ["gh", "pr", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(data or payload()))
        assert argv[:2] == ["gh", "api"] and "-X" not in argv and "--method" not in argv, argv   # reads only
        path = argv[-1]
        as_diff = argv[2:4] == ["-H", "Accept: application/vnd.github.diff"]
        if as_diff:
            assert path.startswith(f"repos/{R}/compare/state...")
        answer = answers.get(path)
        if path.endswith("...state") and path not in answers:
            answer = IDENTICAL   # the base-drift compare of baseRefOid...state
        code = 0
        if isinstance(answer, tuple):
            code, answer = answer
        if answer is None or isinstance(answer, int):
            return SimpleNamespace(returncode=answer or 1, stdout="", stderr="HTTP 502")
        if isinstance(answer, str) and path.startswith(f"repos/{R}/compare/state...") and not as_diff:
            answer = {"status": "ahead", "ahead_by": 1, "behind_by": 0, "commits": [], "files": []}
        return SimpleNamespace(returncode=code, stdout=answer if isinstance(answer, str) else json.dumps(answer),
                               stderr="HTTP 502" if code else "")
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
    # then the merge's tree against the clean merge of its parents (trees read in parallel)
    assert paths[4] == f"repos/{R}/compare/{H0}...{BASE_TIP}"
    assert sorted(paths[5:9]) == sorted(tree_path(s) for s in (STATE_OLD, H0, BASE_TIP, H))
    assert paths[9:] == [f"repos/{R}/compare/{STATE_OLD}...state"]   # then the base-drift gate still runs


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
    notes3 = "b3" * 20
    fake(monkeypatch, answers={
        f"repos/{R}/compare/{H0}...{H}": cmp, f"repos/{R}/compare/{tip2}...state": IDENTICAL,
        f"repos/{R}/compare/{mid}...{tip2}": merge_base(BASE_TIP),
        tree_path(mid): tree(FILES[H]), tree_path(tip2): tree({**FILES[BASE_TIP], "docs/notes.md": notes3}),
        tree_path(H): tree({**FILES[H], "docs/notes.md": notes3})})
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


# Whitespace-only merge edits (t_01a1d6b8642d50ff). A merge whose conflict resolution touches only whitespace in
# the PR's own lines kept the same `git patch-id --stable` and carried the approval to an unreviewed head:
# a2 re-indents `audit(cmd)` into the `if force:` block (a Python semantic change), a3 turns "/tmp/build" into
# "/ tmp/build" inside a string literal. The diffs come from a real repository (merge-base..sha, as
# `compare/state...X` returns them) and real `git patch-id` compares them.
BUILD_STATE = "def run(cmd, force=False):\n    prepare(cmd)\n    return execute(cmd)\n"
BUILD_PR = ("def run(cmd, force=False):\n    prepare(cmd)\n    if force:\n        wipe(\"/tmp/build\")\n"
            "    audit(cmd)\n    return execute(cmd)\n")
MERGE_EDITS = {
    "ctrl": BUILD_PR,                                                       # a clean update-branch merge
    "a2": BUILD_PR.replace("    audit(cmd)", "        audit(cmd)"),
    "a3": BUILD_PR.replace('"/tmp/build"', '"/ tmp/build"'),
}


@pytest.fixture(scope="module")
def merge_diffs(tmp_path_factory):
    """{name: PR diff} for the approved head 'h0' and one update-branch merge per MERGE_EDITS entry."""
    repo = tmp_path_factory.mktemp("carry-ws")
    env = {"PATH": __import__("os").environ.get("PATH", "/usr/bin:/bin"), "HOME": str(repo),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_DATE": "2026-11-13T17:00:00Z",
           "GIT_COMMITTER_DATE": "2026-11-13T17:00:00Z"}

    def git(*args):
        return REAL_RUN(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.hooksPath=/dev/null",
                         *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()

    def commit(path, text, msg):
        (repo / path).write_text(text)
        git("add", path)
        git("commit", "-q", "-m", msg)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "state")
    (repo / "notes.txt").write_text("one\n")
    git("add", "notes.txt")
    commit("build.py", BUILD_STATE, "base")
    git("checkout", "-q", "-b", "pr")
    h0 = commit("build.py", BUILD_PR, "pr: force wipe + audit")
    git("checkout", "-q", "state")
    commit("notes.txt", "one\ntwo\n", "state moves on")

    def pr_diff(sha):
        return git("diff", git("merge-base", "state", sha), sha) + "\n"

    out = {"h0": pr_diff(h0)}
    for name, text in MERGE_EDITS.items():
        git("checkout", "-q", "--detach", h0)
        git("merge", "-q", "--no-ff", "--no-commit", "state")
        (repo / "build.py").write_text(text)
        git("add", "build.py")
        git("commit", "-q", "-m", f"Merge state ({name})")
        out[name] = pr_diff(git("rev-parse", "HEAD"))
    return out


def test_real_patch_id_sees_whitespace(merge_diffs):
    h0 = g._patch_id(merge_diffs["h0"], None)
    assert g._patch_id(merge_diffs["ctrl"], None) == h0
    assert g._patch_id(merge_diffs["a2"], None) != h0 != g._patch_id(merge_diffs["a3"], None)


def test_clean_update_branch_merge_from_a_real_repo_carries(monkeypatch, merge_diffs):
    fake(monkeypatch, answers={f"repos/{R}/compare/state...{H0}": merge_diffs["h0"],
                               f"repos/{R}/compare/state...{H}": merge_diffs["ctrl"]})
    assert check() is None


@pytest.mark.parametrize("name", ["a2", "a3"])
def test_whitespace_only_merge_edit_refused(monkeypatch, merge_diffs, name):
    assert merge_diffs[name] != merge_diffs["h0"]
    fake(monkeypatch, answers={f"repos/{R}/compare/state...{H0}": merge_diffs["h0"],
                               f"repos/{R}/compare/state...{H}": merge_diffs[name]})
    why = check()
    assert why and f"approval of earlier head {H0[:10]} does not carry" in why and "patch changed" in why


# Pins for carry mutants the suite let survive (t_01a1d6b8642d50ff).
def test_block_in_the_same_second_as_the_approval_stops_the_carry(monkeypatch):
    same = review("Request changes: B1.", commit="e" * 40, at=APPROVAL["submittedAt"])
    calls = fake(monkeypatch, payload([APPROVAL, same]))
    assert "blocking verdict came at or after" in check() and len(calls) == 1


def test_untimed_comment_approval_of_another_head_stops_the_carry(monkeypatch):
    # A comment approval with no readable time cannot be ordered against the H0 approval: refuse.
    untimed = comment(f"VERDICT: APPROVE at {'e' * 40}", at=None)
    calls = fake(monkeypatch, payload([APPROVAL], [untimed]))
    assert "blocking verdict came at or after" in check() and len(calls) == 1


def test_diff_reads_ask_for_the_diff_media_type(monkeypatch):
    calls = fake(monkeypatch)
    assert check() is None
    diffs = [c for c in calls if c[-1].startswith(f"repos/{R}/compare/state...")]
    assert len(diffs) == 2 and all(c[2:4] == ["-H", "Accept: application/vnd.github.diff"] for c in diffs)


def test_octopus_merge_after_the_approved_head_refused(monkeypatch):
    # Even when the first two parents look like a base merge and the patch-id matches, a third parent is
    # something the review never saw.
    third = "f" * 40
    cmp = {"status": "ahead", "behind_by": 0, "commits": [node(H, H0, BASE_TIP, third)]}
    calls = fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": cmp})
    why = check()
    assert why and f"{H[:10]} after it is not a merge of the base" in why
    assert not any(c[-1].startswith(f"repos/{R}/compare/state...") for c in calls)


@pytest.mark.parametrize("path,answer", [
    (f"repos/{R}/compare/{H0}...{H}", UPDATE),
    (f"repos/{R}/compare/{BASE_TIP}...state", ON_STATE),
    (f"repos/{R}/compare/state...{H}", NEW_DIFF),
])
def test_failed_gh_read_refuses_even_with_a_plausible_answer(monkeypatch, path, answer):
    fake(monkeypatch, answers={path: (1, answer)})
    why = check()
    assert why and f"`gh api {path}` exited 1" in why and "cannot be shown" in why


@pytest.mark.parametrize("parents", [[H0, BASE_TIP[:12]], [H0[:12], BASE_TIP], [H0, BASE_TIP.upper()]])
def test_malformed_merge_parents_refused(monkeypatch, parents):
    cmp = {"status": "ahead", "behind_by": 0, "commits": [node(H, *parents)]}
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": cmp})
    assert f"the parents of {H[:10]} are unreadable" in check()


@pytest.mark.parametrize("head", [H[:12], H.upper(), H + "0"])
def test_malformed_head_sha_refused_before_any_read(monkeypatch, head):
    calls = fake(monkeypatch)
    ok, why = g._carried_approval(head, payload(), R, None, {})
    assert not ok and why.endswith(": the head sha is unreadable") and calls == []


# Tree identity of each update-branch merge (t_01a1d6de4e9ec063). `git patch-id --verbatim` hashes hunks without
# their line numbers, so a merge that moved the PR's hunk, context included, to an identical spot elsewhere in
# the same file kept the id and carried. Each merge on the head's first-parent line must now have exactly the
# tree of a clean three-way merge of its parents (paths both parents changed are merged by real `git merge-file`).
def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def blob_answer(data):
    return {"sha": blob_sha(data), "size": len(data), "encoding": "base64",
            "content": base64.encodebytes(data).decode()}


def test_clean_merge_tree_carries_and_reads_no_blob(monkeypatch):
    calls = fake(monkeypatch)
    assert check() is None
    assert not any("/git/blobs/" in c[-1] for c in calls)   # no path changed on both sides


@pytest.mark.parametrize("edit,where", [
    ({"docs/notes.md": "b9" * 20}, "docs/notes.md"),                       # a state file edited in the merge
    ({"docs/notes.md": NOTES_OLD}, "docs/notes.md"),                       # state's change dropped
    ({"home/bin/evil": "e" * 40}, "home/bin/evil"),                        # a file added in the merge
    ({"home/bin/hermes-sync": ("100755", SYNC_PR)}, "home/bin/hermes-sync"),   # a mode flipped
])
def test_edited_merge_refused_with_the_reason(monkeypatch, edit, where):
    fake(monkeypatch, answers={tree_path(H): tree({**FILES[H], **edit})})
    why = check()
    assert why and "no approval of its exact head" in why and f"approval of earlier head {H0[:10]} does not carry" in why
    assert (f"merge {H[:10]} is not a clean, unedited merge of the base: its tree differs from the clean merge "
            f"of {H0[:10]} and {BASE_TIP[:10]} at {where}") in why


def test_merge_that_deletes_a_file_refused(monkeypatch):
    fake(monkeypatch, answers={tree_path(H): tree({"home/bin/hermes-sync": SYNC_PR})})
    assert "its tree differs from the clean merge" in check() and "at docs/notes.md" in check()


def test_edited_earlier_merge_in_a_chain_refused(monkeypatch):
    # H0 -> mid (merge of BASE_TIP, edited) -> H (clean merge of mid and tip2): each merge is checked against its
    # own first parent, so the edit in mid refuses although H itself is a clean merge of mid.
    mid, tip2, evil = "c" * 40, "d" * 40, "e" * 40
    cmp = {"status": "ahead", "behind_by": 0, "commits": [node(mid, H0, BASE_TIP), node(H, mid, tip2)]}
    mid_files = {**FILES[H], "home/bin/evil": evil}
    fake(monkeypatch, answers={
        f"repos/{R}/compare/{H0}...{H}": cmp, f"repos/{R}/compare/{tip2}...state": IDENTICAL,
        f"repos/{R}/compare/{mid}...{tip2}": merge_base(BASE_TIP),
        tree_path(mid): tree(mid_files), tree_path(tip2): tree(FILES[BASE_TIP]), tree_path(H): tree(mid_files)})
    why = check()
    assert f"merge {mid[:10]} is not a clean, unedited merge" in why and "at home/bin/evil" in why


# A path both parents changed: merged by real `git merge-file` on the blobs read through gh.
LINES = b"".join(b"%d\n" % i for i in range(1, 11))
SHARED = {"base": LINES, "ours": LINES.replace(b"1\n", b"one\n", 1), "theirs": LINES.replace(b"10\n", b"ten\n"),
          "merged": LINES.replace(b"1\n", b"one\n", 1).replace(b"10\n", b"ten\n")}


def shared(monkeypatch, ours=SHARED["ours"], theirs=SHARED["theirs"], merged=SHARED["merged"], answers=None,
           base=SHARED["base"]):
    """FILES plus home/shared.txt changed by both parents; every SHARED blob and each bytes content is served."""
    blobs = {blob_sha(d): blob_answer(d)
             for d in (base, ours, theirs, merged, *SHARED.values()) if isinstance(d, bytes)}

    def files(sha, data):
        out = dict(FILES[sha])
        if isinstance(data, bytes):
            out["home/shared.txt"] = ("100644", blob_sha(data), len(data))
        elif data is not None:
            out["home/shared.txt"] = data
        return tree(out)
    return fake(monkeypatch, answers={
        tree_path(STATE_OLD): files(STATE_OLD, base), tree_path(H0): files(H0, ours),
        tree_path(BASE_TIP): files(BASE_TIP, theirs), tree_path(H): files(H, merged),
        **{f"repos/{R}/git/blobs/{s}": a for s, a in blobs.items()}, **(answers or {})})


def test_path_changed_on_both_sides_merges_cleanly_and_carries(monkeypatch):
    calls = shared(monkeypatch)
    assert check() is None
    assert sorted(c[-1] for c in calls if "/git/blobs/" in c[-1]) == sorted(
        f"repos/{R}/git/blobs/{blob_sha(SHARED[k])}" for k in ("base", "ours", "theirs"))


def test_large_path_changed_on_both_sides_merges_cleanly_and_carries(monkeypatch):
    # A large text file must take the content-merge path, not the unchanged-blob shortcut.
    base = b"start\n" + b"unchanged line\n" * 90000 + b"end\n"
    ours = base.replace(b"start\n", b"pr change\n", 1)
    theirs = base.replace(b"end\n", b"base change\n")
    merged = ours.replace(b"end\n", b"base change\n")
    assert all(1_200_000 < len(d) < 8 << 20 for d in (base, ours, theirs, merged))
    calls = shared(monkeypatch, base=base, ours=ours, theirs=theirs, merged=merged)
    assert check() is None
    assert sorted(c[-1] for c in calls if "/git/blobs/" in c[-1]) == sorted(
        f"repos/{R}/git/blobs/{blob_sha(d)}" for d in (base, ours, theirs))


@pytest.mark.parametrize("size", [8 * 1024 * 1024 + 1, None])
def test_over_eight_mib_or_missing_size_refuses_before_blob_read(monkeypatch, size):
    sha = blob_sha(SHARED["ours"])
    calls = shared(monkeypatch, ours=("100644", sha, size))
    why = check()
    assert f"blob {sha[:10]} is over {g._MERGE_BLOB_BYTES} bytes or has no size" in why
    assert "a clean merge cannot be shown" in why
    assert not any(f"/git/blobs/{sha}" in c[-1] for c in calls)


def test_merge_result_other_than_the_clean_content_merge_refused(monkeypatch):
    shared(monkeypatch, merged=SHARED["merged"] + b"extra\n")
    assert "its tree differs from the clean merge" in check() and "at home/shared.txt" in check()


def test_conflicting_content_merge_refused(monkeypatch):
    # state changed line 1 too: real git merge-file reports a conflict, whatever H's resolution was.
    shared(monkeypatch, theirs=LINES.replace(b"1\n", b"uno\n", 1), merged=SHARED["ours"])
    assert "home/shared.txt does not merge cleanly" in check()


@pytest.mark.parametrize("kind,kw,reason", [
    ("modify/delete", {"theirs": None, "merged": SHARED["ours"]}, "add/add or modify/delete"),
    ("add/add", {"base": None, "theirs": b"other\n", "merged": SHARED["ours"]}, "add/add or modify/delete"),
    ("symlink", {"ours": ("120000", "c1" * 20, 5), "theirs": ("120000", "c2" * 20, 5),
                 "merged": ("120000", "c1" * 20, 5), "base": ("120000", "c0" * 20, 5)}, "not a regular file"),
    ("submodule", {"ours": ("160000", "c1" * 20), "theirs": ("160000", "c2" * 20),
                   "merged": ("160000", "c1" * 20), "base": ("160000", "c0" * 20)}, "not a regular file"),
])
def test_both_sides_changed_a_path_git_would_not_auto_merge(monkeypatch, kind, kw, reason):
    shared(monkeypatch, **kw)
    why = check()
    assert why and f"both parents changed home/shared.txt" in why and reason in why, kind


def test_more_content_merges_than_the_cap_refused_before_any_blob_read(monkeypatch):
    extra = {f"home/f{i}.txt": "c" * 40 for i in range(g._MERGE_FILES + 1)}
    trees = {sha: {**files, **{p: f"{n}{i % 10}" * 20 for i, p in enumerate(extra)}}
             for sha, files, n in ((STATE_OLD, FILES[STATE_OLD], "d"), (H0, FILES[H0], "e"),
                                   (BASE_TIP, FILES[BASE_TIP], "f"), (H, FILES[H], "a"))}
    calls = fake(monkeypatch, answers={tree_path(s): tree(f) for s, f in trees.items()})
    why = check()
    assert why and f"{g._MERGE_FILES + 1} paths need a content merge (more than {g._MERGE_FILES})" in why
    assert not any("/git/blobs/" in c[-1] for c in calls)


def test_mode_change_on_one_side_wins_in_a_content_merge(monkeypatch):
    m = SHARED["merged"]
    shared(monkeypatch, ours=("100755", blob_sha(SHARED["ours"]), len(SHARED["ours"])),
           merged=("100755", blob_sha(m), len(m)))
    assert check() is None
    shared(monkeypatch, ours=("100755", blob_sha(SHARED["ours"]), len(SHARED["ours"])))   # merged kept 100644
    assert "its tree differs from the clean merge" in check()


@pytest.mark.parametrize("path,answer,reason", [
    (f"repos/{R}/compare/{H0}...{BASE_TIP}", None, f"`gh api repos/{R}/compare/{H0}...{BASE_TIP}` exited 1"),
    (f"repos/{R}/compare/{H0}...{BASE_TIP}", {"status": "diverged"}, "merge base of"),
    (f"repos/{R}/compare/{H0}...{BASE_TIP}", merge_base(STATE_OLD[:12]), "merge base of"),
    (tree_path(H), None, f"`gh api {tree_path(H)}` exited 1"),
    (tree_path(STATE_OLD), (1, tree(FILES[STATE_OLD])), f"`gh api {tree_path(STATE_OLD)}` exited 1"),
    (tree_path(BASE_TIP), "not json", "unreadable JSON"),
    (tree_path(H0), {**tree(FILES[H0]), "truncated": True}, f"the tree of {H0[:10]} is truncated"),
    (tree_path(H0), {"sha": H0, "tree": tree(FILES[H0])["tree"]}, f"the tree of {H0[:10]} is truncated"),
    (tree_path(H), {**tree(FILES[H]), "tree": [{"path": "x", "mode": "100644", "type": "blob", "sha": "zz"}]},
     f"the tree of {H[:10]} has an unreadable entry"),
    (tree_path(H), {**tree(FILES[H]), "tree": tree(FILES[H])["tree"] * 2}, "has an unreadable entry"),
    (tree_path(H), {**tree(FILES[H]), "tree": [{"path": "x", "mode": "100644", "type": "tag", "sha": "a" * 40}]},
     "has an unreadable entry"),
])
def test_unreadable_merge_tree_read_refused(monkeypatch, path, answer, reason):
    fake(monkeypatch, answers={path: answer if answer is not None else 0})
    why = check()
    assert why and "does not carry" in why and reason in why and "a clean merge cannot be shown" in why


@pytest.mark.parametrize("which,answer,reason", [
    ("ours", None, "exited 1"),
    ("ours", (1, blob_answer(SHARED["ours"])), "exited 1"),
    ("theirs", blob_answer(b"tampered\n"), "does not hash to its id"),
    ("base", {**blob_answer(SHARED["base"]), "encoding": "utf-8"}, "is unreadable"),
    ("base", {**blob_answer(SHARED["base"]), "content": "!!!"}, "does not hash to its id"),
])
def test_unreadable_blob_refused(monkeypatch, which, answer, reason):
    shared(monkeypatch, answers={f"repos/{R}/git/blobs/{blob_sha(SHARED[which])}": answer
                                 if answer is not None else 0})
    why = check()
    assert why and reason in why and "a clean merge cannot be shown" in why


def test_oversized_blob_is_never_read(monkeypatch):
    big = g._MERGE_BLOB_BYTES + 1
    calls = shared(monkeypatch, ours=("100644", blob_sha(SHARED["ours"]), big))
    assert f"over {g._MERGE_BLOB_BYTES} bytes" in check()
    assert not any(blob_sha(SHARED["ours"]) in c[-1] for c in calls)


def test_merge_file_that_cannot_run_refused(monkeypatch):
    shared(monkeypatch)
    run = g.subprocess.run

    def broken(argv, **kwargs):
        if argv[:2] == ["git", "merge-file"]:
            raise OSError("no git")
        return run(argv, **kwargs)
    monkeypatch.setattr(g.subprocess, "run", broken)
    assert "`git merge-file` could not run (OSError)" in check()


# The same checks on diffs and trees from a real repository, served through the faked gh reads.
BLOCK = "    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    f = 6\n    return a\n"
RELOC_STATE = f"def first():\n{BLOCK}\n\ndef second():\n{BLOCK}"
LOG = "    log(a)\n"
RELOC_PR = RELOC_STATE.replace("    c = 3\n", "    c = 3\n" + LOG, 1)
RELOC_MOVED = RELOC_STATE[:RELOC_STATE.rindex("    c = 3\n")] + "    c = 3\n" + LOG + RELOC_STATE[
    RELOC_STATE.rindex("    c = 3\n") + len("    c = 3\n"):]


@pytest.fixture(scope="module")
def real_merges(tmp_path_factory):
    """(h0, {name: merge head}, answers) from a real repository: the PR adds `log(a)` to first() and changes line 1
    of shared.txt; state changes notes.txt and line 10 of shared.txt; each named head merges state into h0."""
    import os
    repo = tmp_path_factory.mktemp("carry-tree")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(repo), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_DATE": "2026-11-13T17:00:00Z",
           "GIT_COMMITTER_DATE": "2026-11-13T17:00:00Z"}

    def git(*args, raw=False):
        out = REAL_RUN(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.hooksPath=/dev/null",
                        *args], cwd=repo, env=env, capture_output=True, check=True).stdout
        return out if raw else out.decode().strip()

    def write(**files):
        for name, text in files.items():
            (repo / name.replace("_", ".")).write_bytes(text if isinstance(text, bytes) else text.encode())
        git("add", "-A")

    git("init", "-q", "-b", "state")
    write(build_py=RELOC_STATE, notes_txt="one\n", shared_txt=SHARED["base"])
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "pr")
    write(build_py=RELOC_PR, shared_txt=SHARED["ours"])
    git("commit", "-q", "-m", "pr: log a")
    h0 = git("rev-parse", "HEAD")
    git("checkout", "-q", "state")
    write(notes_txt="one\ntwo\n", shared_txt=SHARED["theirs"])
    git("commit", "-q", "-m", "state moves on")
    heads = {}
    for name, build in (("clean", None), ("relocated", RELOC_MOVED),
                        ("edited", RELOC_PR.replace("return a\n\n\ndef second", "return a\n\n# hi\ndef second"))):
        git("checkout", "-q", "--detach", h0)
        git("merge", "-q", "--no-ff", "--no-commit", "state")
        if build is not None:
            write(build_py=build)
        git("commit", "-q", "-m", f"Merge state ({name})")
        heads[name] = git("rev-parse", "HEAD")

    answers = {}
    for line in git("cat-file", "--batch-all-objects", "--batch-check").splitlines():
        sha, kind, _ = line.split()
        if kind == "blob":
            answers[f"repos/{R}/git/blobs/{sha}"] = blob_answer(git("cat-file", "blob", sha, raw=True))
        elif kind == "commit":
            entries = []
            for row in git("ls-tree", "-r", "-t", "-l", sha).splitlines():
                meta, path = row.split("\t", 1)
                mode, typ, obj, size = meta.split()
                entries.append({"path": path, "mode": mode, "type": typ, "sha": obj,
                                **({"size": int(size)} if size != "-" else {})})
            answers[tree_path(sha)] = {"sha": sha, "truncated": False, "tree": entries}
    for head in (h0, *heads.values()):
        answers[f"repos/{R}/compare/state...{head}"] = git("diff", git("merge-base", "state", head), head) + "\n"
    for head in heads.values():
        rows = [r.split() for r in git("rev-list", "--parents", f"{h0}..{head}").splitlines()]
        answers[f"repos/{R}/compare/{h0}...{head}"] = {"status": "ahead", "behind_by": 0,
                                                     "commits": [node(*r) for r in reversed(rows)]}
        for r in rows:
            if len(r) == 3:
                answers[f"repos/{R}/compare/{r[1]}...{r[2]}"] = merge_base(git("merge-base", r[1], r[2]))
    return h0, heads, answers


def carry_real(monkeypatch, real_merges, name):
    h0, heads, answers = real_merges
    calls = fake(monkeypatch, answers=answers)
    data = payload([review(f"Independent exact-head review at {h0}. **Verdict: approve**", commit=h0)])
    return g._carried_approval(heads[name], data, R, None, {}), calls


def test_real_clean_merge_with_a_content_merge_carries(monkeypatch, real_merges):
    (ok, h0), calls = carry_real(monkeypatch, real_merges, "clean")
    assert ok and h0 == real_merges[0]
    assert sum("/git/blobs/" in c[-1] for c in calls) == 3   # shared.txt: base, ours, theirs


def test_real_hunk_relocated_inside_a_merge_refused(monkeypatch, real_merges):
    h0, heads, answers = real_merges
    # The gap: patch-id cannot tell the relocated hunk from the reviewed one.
    old, new = (g._patch_id(answers[f"repos/{R}/compare/state...{s}"], None) for s in (h0, heads["relocated"]))
    assert old == new and answers[f"repos/{R}/compare/state...{heads['relocated']}"] != answers[
        f"repos/{R}/compare/state...{heads['clean']}"]
    (ok, why), _ = carry_real(monkeypatch, real_merges, "relocated")
    assert not ok and f"merge {heads['relocated'][:10]} is not a clean, unedited merge" in why
    assert "its tree differs from the clean merge" in why and "at build.py" in why


def test_real_edited_merge_refused(monkeypatch, real_merges):
    (ok, why), _ = carry_real(monkeypatch, real_merges, "edited")
    assert not ok and ("patch changed" in why or "at build.py" in why)


# Authoritative Git verifier: real fetch/merge-tree, only the remote URL is local.
GIT_VERIFY = g._git_merge_tree_refusal


@pytest.fixture
def git_history(tmp_path):
    import os
    repo = tmp_path / "source"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}

    def git(*args):
        return REAL_RUN(["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
                         "-c", "core.hooksPath=/dev/null", *args], cwd=repo, env=env,
                        capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q")
    (repo / "f").write_text(RELOC_STATE)
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "f").write_text(RELOC_PR)
    git("commit", "-qam", "approved hunk")
    ours = git("rev-parse", "HEAD")
    git("checkout", "-q", "--detach", base)
    (repo / "notes").write_text("state\n")
    git("add", ".")
    git("commit", "-qm", "state")
    theirs = git("rev-parse", "HEAD")
    clean_tree = git("merge-tree", "--write-tree", ours, theirs)
    clean = git("commit-tree", clean_tree, "-p", ours, "-p", theirs, "-m", "clean")
    git("checkout", "-q", "--detach", clean)
    (repo / "f").write_text(RELOC_MOVED)
    git("add", "f")
    moved = git("commit-tree", git("write-tree"), "-p", ours, "-p", theirs, "-m", "relocated")
    assert g._patch_id(git("diff", base, ours), None) == g._patch_id(git("diff", theirs, moved), None)
    git("checkout", "-q", "--detach", moved)
    git("checkout", "-q", "--detach", base)
    git("mv", "f", "renamed")
    git("commit", "-qm", "rename")
    rename = git("rev-parse", "HEAD")
    git("checkout", "-q", "--detach", base)
    git("rm", "f")
    git("commit", "-qm", "delete")
    delete = git("rev-parse", "HEAD")
    conflict = git("commit-tree", git("rev-parse", rename + "^{tree}"), "-p", rename, "-p", delete,
                   "-m", "resolve rename/delete")
    return repo, {"clean": (clean, ours, theirs), "moved": (moved, ours, theirs),
                  "conflict": (conflict, rename, delete)}


def local_git_verifier(monkeypatch, tmp_path, repo, failure=None):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        assert not kw.get("shell") and 0 < kw["timeout"] <= 30
        if "fetch" in argv:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, kw["timeout"])
            if failure == "failed":
                return SimpleNamespace(returncode=128, stdout="", stderr="unreadable remote")
            assert "https://github.com/example/protected.git" in argv
            argv = [str(repo) if a == "https://github.com/example/protected.git" else a for a in argv]
        return REAL_RUN(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", run)
    assert GIT_VERIFY is not None, "real merge-tree verifier missing"
    return calls


def test_git_verifier_rejects_rename_delete_conflict(monkeypatch, tmp_path, git_history):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    why = GIT_VERIFY(*heads["conflict"], R, None, {"HERMES_HOME": str(tmp_path / "home")})
    assert "merge-tree" in why and "exited 1" in why


def test_git_verifier_clean_merge_and_relocated_hunk(monkeypatch, tmp_path, git_history):
    repo, heads = git_history
    calls = local_git_verifier(monkeypatch, tmp_path, repo)
    env = {"HERMES_HOME": str(tmp_path / "home")}
    assert GIT_VERIFY(*heads["clean"], R, None, env) == ""
    assert "tree differs from the clean merge" in GIT_VERIFY(*heads["moved"], R, None, env)
    assert all("--depth=128" in a for a, _ in calls if "fetch" in a)
    assert not list((tmp_path / "home" / "cache").iterdir())


@pytest.mark.parametrize("failure", ["failed", "timeout"])
def test_git_verifier_unreadable_fetch_refuses(monkeypatch, tmp_path, git_history, failure):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo, failure)
    why = GIT_VERIFY(*heads["clean"], R, None, {"HERMES_HOME": str(tmp_path / "home")})
    assert "fetch" in why and ("exited 128" in why or "TimeoutExpired" in why)


def test_git_verifier_refusal_surfaces_in_carry(monkeypatch):
    fake(monkeypatch)
    monkeypatch.setattr(g, "_git_merge_tree_refusal", lambda *a: "git fetch exited 128")
    assert "git fetch exited 128" in check()


def test_git_verifier_checks_each_merge_in_chain(monkeypatch):
    mid, tip2 = "c" * 40, "d" * 40
    cmp = {"status": "ahead", "behind_by": 0, "commits": [node(mid, H0, BASE_TIP), node(H, mid, tip2)]}
    fake(monkeypatch, answers={f"repos/{R}/compare/{H0}...{H}": cmp,
                               f"repos/{R}/compare/{tip2}...state": IDENTICAL})
    monkeypatch.setattr(g, "_merge_tree_refusal", lambda *a: "")
    calls = []
    monkeypatch.setattr(g, "_git_merge_tree_refusal", lambda *a: calls.append(a[:3]) or "")
    assert check() is None
    assert calls == [(mid, H0, BASE_TIP), (H, mid, tip2)]


@pytest.mark.parametrize("step,output,reason", [
    ("merge-tree", "garbage", "unreadable tree id"),
    ("merge-tree", "a" * 40 + "\nextra", "unreadable tree id"),
    ("merge-base", "a" * 40 + "\n" + "b" * 40, "multiple"),
    ("show", "a" * 40, "parents differ"),
])
def test_git_verifier_odd_reads_refuse(monkeypatch, tmp_path, git_history, step, output, reason):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    run = g.subprocess.run

    def odd(argv, **kw):
        if step in argv:
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        return run(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", odd)
    assert reason in GIT_VERIFY(*heads["clean"], R, None, {"HERMES_HOME": str(tmp_path / "home")})
