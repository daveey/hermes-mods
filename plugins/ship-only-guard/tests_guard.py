"""All mutations below are classifications; Git fixtures have no network access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

g = load("ship_guard_test", HERE / "guard.py")
plugin = load("ship_plugin_test", HERE / "__init__.py")


@pytest.fixture(autouse=True)
def _no_land_turn(monkeypatch):
    # These cases pin the preflight's other gh calls; the land turn has its own fakes in tests_turn.py.
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "ship-only-guard.json").write_text(json.dumps({"repos": ["example/protected"]}))
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in (g.MARKER, "SHIP_ONLY_GUARD_REPOS", "GH_REPO", "TERMINAL_CWD"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/example/protected.git"], check=True)
    return root


def test_profile_policy_inheritance(repo, monkeypatch):
    root = Path(os.environ["HERMES_HOME"])
    profile = root / "profiles" / "reviewer"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    assert g.check_shell("gh pr merge 12", str(repo))


def test_python_terminal_positional(repo):
    assert g.check_python('terminal("gh pr merge 12 -R example/protected")', str(repo))
    assert g.check_python('terminal("gh pr merge 12 -R example/other")', str(repo)) is None


@pytest.mark.parametrize("command", [
    "gh pr merge 12", "gh pr merge --admin --squash 12", "gh --repo example/protected pr merge 12",
    "gh pr --repo=example/protected merge --auto 12", "gh pr merge -Rexample/protected 12",
    "gh pr merge https://github.com/example/protected/pull/12 --admin",
    "GH_REPO=example/protected gh pr merge 12", "env GH_REPO=example/protected gh pr merge 12",
    "gh api -X PUT repos/example/protected/pulls/12/merge",
    "gh api /repos/example/protected/merges -f head=topic",
    "gh api --method=POST https://api.github.com/repos/example/protected/merges",
    "git push origin HEAD:main", "git push origin +HEAD:refs/heads/main", "git push origin main",
    "git push --force origin topic:main", "git push origin --all", "git push --mirror origin",
    "git push", "git push origin", "git push origin HEAD", "git -c push.default=matching push origin", "git push origin :main",
    "gh pr merge --repo github.com/example/protected 12",
    "git push --repo=origin HEAD:main", "git push origin refs/heads/*:refs/heads/*",
    "bash -lc 'gh pr merge 12'", "command gh pr merge 12", "env -u GH_REPO gh pr merge 12",
])
def test_blocked_shapes(repo, command):
    assert g.check_shell(command, str(repo)), command


@pytest.mark.parametrize("command", [
    "gh pr view 12", "gh pr merge -R example/unrelated 12", "GH_REPO=example/unrelated gh pr merge 12",
    "gh pr merge https://github.com/example/unrelated/pull/12",
    "gh api repos/example/unrelated/merges -f head=topic", "gh api repos/example/protected/pulls/12",
    "git push origin HEAD:topic", "git push origin --tags", "git push --dry-run origin HEAD:main", "git -c push.default=nothing push",
    "git push https://github.com/example/unrelated.git HEAD:main",
    "echo 'gh pr merge 12'", "echo HERMES_CORE_WT_LAND=1", "git status",
])
def test_allowed_shapes(repo, command):
    assert g.check_shell(command, str(repo)) is None


@pytest.mark.parametrize("command", [
    "HERMES_CORE_WT_LAND=1 gh pr merge 12", "env HERMES_CORE_WT_LAND=1 gh api repos/example/protected/merges",
    "HERMES_CORE_WT_LAND=1 git push origin HEAD:main",
    "HERMES_CORE_WT_LAND=1 bash -c 'gh pr merge 12; git push origin main'",
])
def test_land_child(repo, command):
    assert g.check_shell(command, str(repo)) is None


@pytest.mark.parametrize("command", [
    "echo HERMES_CORE_WT_LAND=1; gh pr merge 12", "gh pr merge 12 # HERMES_CORE_WT_LAND=1",
    "HERMES_CORE_WT_LAND=1 true && gh pr merge 12",
    "HERMES_CORE_WT_LAND=1 gh pr merge 12; git push origin main",
    "HERMES_CORE_WT_LAND=1; gh pr merge 12", "HERMES_CORE_WT_LAND=10 gh pr merge 12",
    "env HERMES_CORE_WT_LAND=1 true | gh pr merge 12",
])
def test_marker_is_command_scoped(repo, command):
    assert g.check_shell(command, str(repo))


def test_context(repo, tmp_path, monkeypatch):
    assert g.check_shell(f"cd '{repo}' && gh pr merge 12", str(tmp_path))
    assert g.check_shell(f"git -C '{repo}' push origin main", str(tmp_path))
    assert g.check_shell(f"env -C '{repo}' git push origin main", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(repo))
    assert g.check_shell("gh pr merge 12")
    monkeypatch.setenv(g.MARKER, "1")
    assert g.check_shell("gh pr merge 12") is None
    assert g.check_shell("env -u HERMES_CORE_WT_LAND gh pr merge 12")


@pytest.mark.parametrize("command", [
    "git -C $WS push origin main", "git -C ${WS} push origin main", "git -C$WS push origin main",
    "env -C $WS git push origin main", "env --chdir ${WS} git push origin main",
])
def test_context_var_target_expands(repo, tmp_path, command):
    # A -C/--chdir $VAR target resolves from the command's env like `cd $VAR`; unset, it names no repository.
    repos = {"example/protected"}
    assert g.check_shell(command, str(tmp_path), repos=repos, env={"WS": str(repo)}), command
    assert g.check_shell(command, str(tmp_path), repos=repos, env={}) is None, command


def test_configured_push_default(repo):
    subprocess.run(["git", "-C", str(repo), "config", "remote.origin.push", "HEAD:main"], check=True)
    assert g.check_shell("git push", str(repo))


def test_missing_and_override_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SHIP_ONLY_GUARD_REPOS", raising=False)
    monkeypatch.delenv(g.MARKER, raising=False)
    assert g.check_shell("gh pr merge -R example/protected 12") is None
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", '["example/protected"]')
    assert g.check_shell("gh pr merge -R example/protected 12")
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", '["bad value"]')
    assert not g.load_repos()


@pytest.mark.parametrize("code", [
    'subprocess.run(["gh", "pr", "merge", "12"])',
    'subprocess.run("gh pr merge 12", shell=True)',
    'os.system("git push origin main")',
    'argv = ["gh", "pr", "merge", "12"]\nsubprocess.check_call(argv)',
    'terminal(command="gh pr merge 12")',
    'terminal({"command": "git push origin main"})',
    'tool_call(calls=[{"name":"terminal", "arguments":{"command":"gh pr merge 12"}}])',
])
def test_python_literals(repo, code):
    assert g.check_python(code, str(repo))


@pytest.mark.parametrize("code", [
    '# HERMES_CORE_WT_LAND=1\nsubprocess.run(["gh", "pr", "merge", "12"])',
    'note = "HERMES_CORE_WT_LAND=1"\nterminal(command="gh pr merge 12")',
])
def test_python_comment_does_not_authorize(repo, code):
    assert g.check_python(code, str(repo))


@pytest.mark.parametrize("code", [
    'print("gh pr merge 12")', 'note = "git push origin main"',
    'subprocess.run(["echo", "gh pr merge 12"])',
    'terminal(command="HERMES_CORE_WT_LAND=1 gh pr merge 12")',
    'subprocess.run(["gh", "pr", "merge", "12"], env={"HERMES_CORE_WT_LAND":"1"})',
    'subprocess.run(["gh", "pr", "merge", "12", "-R", "example/unrelated"])',
])
def test_python_allow_direction(repo, code):
    assert g.check_python(code, str(repo)) is None


def test_python_cwd(repo, tmp_path):
    assert g.check_python(f'subprocess.run(["git", "push", "origin", "main"], cwd={str(repo)!r})', str(tmp_path))


def test_registered_hooks(repo, monkeypatch):
    hooks = {}
    class Context:
        def register_hook(self, name, fn):
            hooks[name] = fn
    plugin.register(Context())
    hook = hooks["pre_tool_call"]
    for name, args in [
        ("terminal", {"command": "gh pr merge 12", "workdir": str(repo)}),
        ("execute_code", {"code": f'subprocess.run(["git","push","origin","main"], cwd={str(repo)!r})'}),
        ("tool_call", {"calls": [{"name": "functions.terminal", "arguments": {"command": "gh pr merge 12", "workdir": str(repo)}}]}),
        ("tool_call", {"calls": [{"name": "execute_code", "arguments": {"code": 'terminal(command="gh pr merge -R example/protected 12")'}}]}),
    ]:
        assert hook(tool_name=name, args=args)["action"] == "block"
    assert hook(tool_name="terminal", args={"command": "gh pr merge -R example/unrelated 12"}) is None


def test_direct_argv(repo):
    assert g.check_argv(["git", "push", "origin", "+HEAD:main"], cwd=str(repo))
    assert g.check_argv(["gh", "pr", "merge", "12"], cwd=str(repo))
    assert g.check_argv(["gh", "pr", "merge", "12"], cwd=str(repo), env={g.MARKER: "1"}, repos={"example/protected"}) is None


def test_other_local_repository(repo, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-b", "main", str(other)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "remote", "add", "origin", "git@github.com:example/unrelated.git"], check=True)
    assert g.check_shell("gh pr merge 12; git push origin main", str(other)) is None
    assert g.check_shell(f"cd {other} && gh pr merge 12", str(repo)) is None
    assert g.check_shell(f"git -C {other} push origin main", str(repo)) is None
    assert g.check_shell(f"git -C {repo} push origin main", str(other))


def test_python_assignment_order(repo):
    code = 'argv = ["gh", "pr", "merge", "12"]\nsubprocess.run(argv)\nargv = ["echo", "safe"]'
    assert g.check_python(code, str(repo))


def test_standalone_cli(repo):
    env = dict(os.environ, SHIP_ONLY_GUARD_REPOS='["example/protected"]')
    blocked = subprocess.run([sys.executable, str(HERE / "guard.py"), "--gh-args", "pr", "merge", "-R", "example/protected", "12"], env=env, cwd=repo, capture_output=True, text=True)
    assert blocked.returncode == 1
    assert "ship-only-guard" in blocked.stderr
    env[g.MARKER] = "1"
    allowed = subprocess.run([sys.executable, str(HERE / "guard.py"), "--gh-args", "pr", "merge", "12"], env=env, cwd=repo, capture_output=True, text=True)
    assert allowed.returncode == 0


# --- state merge review-reply gate (t_01a1d598345b4f7b) -------------------------------------------------------
# Every review point is answered in the PR thread before a state merge: 53 of 56 nit-bearing reviews on state PRs
# merged 11-12/11-13 had no reply before the merge (#2315 merged 2 min after review 5893609888 listed 2 nits).
from types import SimpleNamespace

STATE_SHA = "a" * 40
STATE_CMD = ["gh", "pr", "merge", "12", "--repo", "example/protected", "--rebase", "--match-head-commit", STATE_SHA]
MARK = "<!-- hermes-review-reply -->"
# A real-format PRR_ node id (msgpack [0, repo id, database id]) whose database id is 5893609888.
NIT_ID, NIT_NUMBER = "PRR_kwDOVDjH788AAAABX0lZoA", "5893609888"


def state_pr(reviews=(), comments=(), **updates):
    data = {"number": 12, "state": "OPEN", "baseRefName": "state", "headRefOid": STATE_SHA,
            "url": "https://github.com/example/protected/pull/12",
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}],
            "reviews": list(reviews), "comments": list(comments)}
    data.update(updates)
    return data


def review(body, at="2026-11-13T15:21:04Z", node=NIT_ID, commit=STATE_SHA, state="COMMENTED", **author):
    return {"id": node, "author": {"login": "you", **author}, "authorAssociation": "MEMBER", "state": state,
            "submittedAt": at, "body": body, "commit": {"oid": commit}}


APPROVAL = review("**Independent exact-head review @ " + STATE_SHA + ": approve**", node="PRR_approval")


def comment(body, at="2026-11-13T15:22:00Z", login="you", association="MEMBER", **author):
    return {"id": "IC_x", "author": {"login": login, **author}, "authorAssociation": association,
            "createdAt": at, "body": body}


def state_check(monkeypatch, data, argv=STATE_CMD):
    calls = []
    # The base-drift compare (tests_state.py) sees the base the PR's CI tested is the state tip.
    data = {"baseRefOid": "3" * 40, "files": [{"path": "tests/test_x.py"}], **data}
    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["gh", "api"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"status": "identical", "behind_by": 0, "total_commits": 0, "commits": [], "files": []}))
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr(g.subprocess, "run", run)
    why = g.check_argv(argv, repos={"example/protected"}, env={"GH_REPO": "example/protected"})
    assert len(calls) >= 1 and "reviews" in calls[0][-1].split(",") and "comments" in calls[0][-1].split(",")
    # Later calls are gh api reads only: the patch-identical carry probes of an earlier approved head
    # (tests_carry.py), then at most the base-drift compare of the PR's baseRefOid against state.
    drift = ["gh", "api", f"repos/example/protected/compare/{data['baseRefOid']}...state"]
    assert all(c[:2] == ["gh", "api"] and "-X" not in c for c in calls[1:])
    assert calls[1:].count(drift) <= 1 and (drift not in calls or calls[-1] == drift)
    assert all("/compare/" in c[-1] for c in calls[1:])
    return why


NIT = "**Approve.**\n- Nit (non-blocking): the log line says `retry` where it means `requeue`."


def test_state_merge_refuses_unanswered_nit(monkeypatch):
    why = state_check(monkeypatch, state_pr([review(NIT)]))
    assert why and NIT_NUMBER in why and "Nit (non-blocking): the log line" in why
    assert MARK in why and "fixed in <sha>" in why and "declined: <reason>" in why and "rerun the same merge" in why


def test_state_merge_allows_marked_reply_after_review(monkeypatch):
    reply = comment(f"{MARK}\n1. declined: wording only.")
    assert state_check(monkeypatch, state_pr([review(NIT)], [reply])) is None


@pytest.mark.parametrize("reply", [
    comment(f"{MARK}\nanswered early", at="2026-11-13T15:20:00Z"),                    # before the review
    comment(f"{MARK}\nfixed", at="2026-11-13T15:21:04Z"),                              # not after it
    comment("1. declined: wording only."),                                             # no marker
    comment(f"{MARK}\nbot", login="github-actions", association="NONE"),               # gh's GraphQL bot shape
    comment(f"{MARK}\nbot", login="ci-review[bot]"),
    comment(f"{MARK}\nbot", type="Bot"),
    comment(f"{MARK}\nbot", is_bot=True),
    comment(f"{MARK}\napp", login="some-app", association="NONE"),
])
def test_state_merge_refuses_early_bot_or_unmarked_reply(monkeypatch, reply):
    why = state_check(monkeypatch, state_pr([review(NIT)], [reply]))
    assert why and NIT_NUMBER in why


def test_reply_must_follow_each_nit_review(monkeypatch):
    later = review("Second pass. Nits: rename `x`.", at="2026-11-13T15:30:00Z",
                   node="PRR_kwDOVDjH788AAAABX0maWQ")
    why = state_check(monkeypatch, state_pr([review(NIT), later], [comment(f"{MARK}\nok")]))
    assert why and NIT_NUMBER not in why and "5893626457" in why and "Nits: rename" in why


@pytest.mark.parametrize("body", [
    "Approve. No nits.", "**Nits:** none", "Nits: none.", "Approve; no non-blocking points.",
    "No blocking or non-blocking findings.", "Remaining shapes (none blocking): fine.", "Approve, LGTM.", "", None,
    "Nit (non-blocking): none",
])
def test_state_merge_allows_negated_or_absent_nits(monkeypatch, body):
    assert state_check(monkeypatch, state_pr([APPROVAL, review(body)])) is None


@pytest.mark.parametrize("body", [
    "No test for the 404 path (non-blocking).", "Findings (non-blocking)\n1. Rename it.", "nit: typo",
    "Looks good.\n\nNitpick: trailing space",
])
def test_state_merge_counts_real_points(monkeypatch, body):
    assert state_check(monkeypatch, state_pr([review(body)]))


@pytest.mark.parametrize("drop", ["reviews", "comments"])
def test_state_merge_unreadable_reviews_or_comments_refused(monkeypatch, drop):
    data = state_pr([review("Approve.")])
    data.pop(drop)
    why = state_check(monkeypatch, data)
    assert why and "reviews/comments could not be read" in why
    data[drop] = "garbled"
    assert "could not be read" in state_check(monkeypatch, data)
    data[drop] = [None]
    assert "could not be read" in state_check(monkeypatch, data)


def test_unreadable_review_time_refuses(monkeypatch):
    why = state_check(monkeypatch, state_pr([review(NIT, at=None)], [comment(f"{MARK}\nok")]))
    assert why and NIT_NUMBER in why


def test_review_gate_surfaces_through_shell_and_cli(monkeypatch, tmp_path):
    import shlex
    data = state_pr([review(NIT)])
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    assert NIT_NUMBER in g.check_shell(shlex.join(STATE_CMD), repos={"example/protected"}, env={})
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", '["example/protected"]')
    assert g.main(["--gh-args", *STATE_CMD[1:]]) == 1


def _fixture(name):
    data = json.loads((HERE / "fixtures" / name).read_text())
    # As of the merge: the recorded thread holds only what existed before mergedAt.
    assert all(r["submittedAt"] < data["mergedAt"] for r in data["reviews"])
    assert all(c["createdAt"] < data["mergedAt"] for c in data["comments"])
    return data


def _replay(monkeypatch, data):
    argv = ["gh", "pr", "merge", str(data["number"]), "--repo", "example/protected", "--rebase",
            "--match-head-commit", data["headRefOid"]]
    return state_check(monkeypatch, data, argv)


def test_replay_pr2315_at_merge_refused(monkeypatch):
    why = _replay(monkeypatch, _fixture("pr2315-at-merge.json"))
    assert why and "review 5893609888" in why and "Nit, non-blocking: a third" in why


def test_replay_pr2348_at_merge_allowed(monkeypatch):
    assert _replay(monkeypatch, _fixture("pr2348-at-merge.json")) is None


def test_review_number_decoding():
    assert g._review_number("PRR_kwDOVDjH788AAAABXz5FqA") == "5892883880"
    assert g._review_number("PRR_kwDOVDjH788AAAABX0maWQ") == "5893626457"
    assert g._review_number("MDE3OlB1bGxSZXF1ZXN0UmV2aWV3MQ==") == "MDE3OlB1bGxSZXF1ZXN0UmV2aWV3MQ=="
    assert g._review_number(None) == "None"


def test_refusal_quotes_identifiers_intact(monkeypatch):
    # Live #1613 quoted "testhubreadexamples.py": markdown stripping must not eat underscores.
    why = state_check(monkeypatch, state_pr([review("Nit (non-blocking). In `test_hub_read_examples.py` line 99.")]))
    assert why and "test_hub_read_examples.py" in why


# --- state merge exact-head approval gate (t_01a1d5e5c3a82d64) ------------------------------------------------
# #2413 merged with zero reviews and zero comments: the preflight checked review *replies* but never that an
# approval of the exact head existed. 19 state PRs merged 2026-11-13 had no exact-head approval record.
OLD_SHA = "b" * 40


def test_replay_pr2413_at_merge_refused(monkeypatch):
    why = _replay(monkeypatch, _fixture("pr2413-at-merge.json"))
    assert why and "PR #2413" in why and "no approval of its exact head" in why
    assert "0775f3761df95fd34d44e400d977489fffda4c24" in why and "rerun the same merge command" in why


@pytest.mark.parametrize("reviews,comments", [
    ([], []),                                                                     # the #2413 shape
    ([review("**Verdict: approve**", commit=OLD_SHA)], []),                       # an earlier head
    ([review("**Verdict: approve**", commit=None)], []),                          # commit unreadable
    ([{k: v for k, v in review("Approve.").items() if k != "commit"}], []),
    ([review("Looks good overall.")], []),                                        # no verdict at all
    ([review("Approve.", login="ci-review[bot]")], []),                           # a bot is no reviewer
    ([review("Approve.", is_bot=True)], []),
    ([], [comment(f"VERDICT: APPROVE at {OLD_SHA}")]),                           # comment names another head
    ([], [comment(f"Reviewed {STATE_SHA}: looks good, approve.")]),              # comment without a VERDICT line
    ([], [comment(f"VERDICT: APPROVE at {STATE_SHA}", login="github-actions", association="NONE")]),
    ([], [comment(f"VERDICT: APPROVE at e{STATE_SHA[:10]}")]),                   # not the sha's start
])
def test_state_merge_refuses_without_exact_head_approval(monkeypatch, reviews, comments):
    why = state_check(monkeypatch, state_pr(reviews, comments))
    assert why and "no approval of its exact head" in why and STATE_SHA in why


@pytest.mark.parametrize("body", [
    "**Request changes** at exact head: the retry loop never terminates.",
    "Independent exact-head review: I cannot approve this yet; the test is missing.",
    "**VERDICT: REQUEST_CHANGES**", "Approve the idea.\n\nVERDICT: REQUEST CHANGES (blocking: B1)",
    "Rejected: wrong base.",
    # Conditional or withheld approvals put "approve" in the first line of a blocking review (t_01a1d61358568212).
    "Unable to approve: required CI is red.", "Needs changes before I can approve: B1.",
    "I would approve once the missing test lands.", "No approval yet: one blocker (B1).",
    "Round 1 VERDICT: APPROVE.\n\nRe-run found B1. VERDICT: REQUEST_CHANGES",
])
def test_state_merge_refuses_a_blocking_exact_head_verdict(monkeypatch, body):
    why = state_check(monkeypatch, state_pr([review(body)]))
    assert why and "latest exact-head verdict requests changes" in why


@pytest.mark.parametrize("body", [
    "**Independent exact-head review @ " + STATE_SHA + ": approve** (posted as COMMENT because the author's "
    "account cannot approve its own PR)",
    "**Verdict: APPROVE.** Nothing blocks.", "APPROVE @aaaaaaaaaa", "Approve at " + STATE_SHA,
    "## Independent exact-head review — verdict: APPROVE (posted as COMMENT; own-PR)",
    "Round 1 requested changes; all fixed.\n\nVERDICT: APPROVE",
    "Round 1 VERDICT: REQUEST_CHANGES (B1, fixed in abc).\n\nVERDICT: APPROVE",  # the last VERDICT line decides
])
def test_state_merge_allows_an_exact_head_approval_review(monkeypatch, body):
    assert state_check(monkeypatch, state_pr([review(body)])) is None


def test_state_merge_approval_forms(monkeypatch):
    assert state_check(monkeypatch, state_pr([review("", state="APPROVED")])) is None
    assert state_check(monkeypatch, state_pr([review("lgtm", state="CHANGES_REQUESTED")]))
    # A VERDICT issue comment that names the head (full sha or its first 10+ hex) counts.
    for text in (f"Independent review at exact head {STATE_SHA}: VERDICT: APPROVE",
                 f"**VERDICT: APPROVE** @ {STATE_SHA[:10]}"):
        assert state_check(monkeypatch, state_pr([], [comment(text)])) is None


# The review lane also posts its verdict as a PR comment whose first line names the head and approves (live #1872,
# #1919, #2449). Those were refused; author replies that restate an approval must still not count.
@pytest.mark.parametrize("text", [
    f"Agent-posted (Hermes, card t_x@macbook). Round 3 independent review @ {STATE_SHA} — **approve**, no findings.",
    f"Independent review (card t_x, review lane) at exact head {STATE_SHA[:10]}. **Approved.**",
    f"**Independent exact-head review: approve** @ `{STATE_SHA}` (card t_x)",
])
def test_state_merge_allows_a_comment_posted_exact_head_review(monkeypatch, text):
    assert state_check(monkeypatch, state_pr([], [comment(text)])) is None


@pytest.mark.parametrize("text", [
    f"Re review 5879931822 (approve @{STATE_SHA[:10]}): all three nits are tracked.",
    f"Landing owner reply to review t_x (approve @{STATE_SHA[:10]}, 0 blocking, 6 nits).",
    f"Accepted independent exact-head approval 5880924255 at {STATE_SHA}.",
    f"Independent review of an earlier head: approve.\n\nNow at {STATE_SHA}.",  # head not on the first line
    f"Independent review @ {STATE_SHA}: I cannot approve this yet.",
])
def test_state_merge_comment_first_line_needs_a_review_of_the_head(monkeypatch, text):
    assert state_check(monkeypatch, state_pr([], [comment(text)]))


def test_latest_exact_head_verdict_wins(monkeypatch):
    block = review("Request changes: B1.", at="2026-11-13T15:00:00Z", node="PRR_a")
    approve = review("Re-review: approve.", at="2026-11-13T15:30:00Z", node="PRR_b")
    assert state_check(monkeypatch, state_pr([block, approve])) is None
    late_block = review("Request changes: B2.", at="2026-11-13T15:40:00Z", node="PRR_c")
    assert "requests changes" in state_check(monkeypatch, state_pr([block, approve, late_block]))
    late_comment = comment(f"VERDICT: REQUEST_CHANGES at {STATE_SHA}", at="2026-11-13T15:45:00Z")
    assert "requests changes" in state_check(monkeypatch, state_pr([approve], [late_comment]))


def test_unreadable_verdict_time_refuses(monkeypatch):
    why = state_check(monkeypatch, state_pr([review("Approve.", at=None)]))
    assert why and "no readable time" in why


def test_approval_gate_surfaces_through_shell_and_cli(monkeypatch):
    import shlex
    data = state_pr([])
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    assert "no approval of its exact head" in g.check_shell(shlex.join(STATE_CMD), repos={"example/protected"}, env={})
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", '["example/protected"]')
    assert g.main(["--gh-args", *STATE_CMD[1:]]) == 1
