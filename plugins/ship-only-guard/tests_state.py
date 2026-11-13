"""State-branch classifications only: never execute a merge."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("state_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
SHA = "a" * 40


@pytest.fixture(autouse=True)
def _no_land_turn(monkeypatch):
    # These cases pin the preflight's other gh calls; the land turn has its own fakes in tests_turn.py.
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")
CMD = ["gh", "pr", "merge", "12", "--repo", "example/protected", "--rebase", "--match-head-commit", SHA]


APPROVAL = {"id": "PRR_x", "author": {"login": "you"}, "authorAssociation": "MEMBER", "state": "COMMENTED",
            "submittedAt": "2026-11-13T15:00:00Z", "commit": {"oid": SHA},
            "body": f"Independent exact-head review @ {SHA}: approve"}


BASE = "3" * 40


def payload(**updates):
    return {"number": 12, "state": "OPEN", "baseRefName": "state", "headRefOid": SHA, "baseRefOid": BASE,
            "url": "https://github.com/example/protected/pull/12", "reviews": [APPROVAL], "comments": [],
            "files": [{"path": "hosts/mini-1/bb-tuner/tests/test_bb_tuner_feed.py"}],
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}], **updates}


IDENTICAL = {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0, "commits": [], "files": []}


def check(argv=CMD):
    return g.check_argv(argv, repos={"example/protected"}, env={"GH_REPO": "example/protected"})


def probe(monkeypatch, data, compare=IDENTICAL, commits=None):
    """gh pr view answers `data`; gh api compare answers `compare` (a dict, or a (returncode, stdout) pair);
    a per-file commits lookup answers `commits[path]` (a list, or a (returncode, list) pair), else exit 1."""
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:2] == ["gh", "api"] and "/commits?" in argv[2]:
            path = argv[2].split("&path=", 1)[1].split("&", 1)[0]
            listed = (commits or {}).get(path)
            rc, listed = listed if isinstance(listed, tuple) else (0 if listed is not None else 1, listed)
            return SimpleNamespace(returncode=rc, stdout=json.dumps(listed))
        if argv[:2] == ["gh", "api"]:
            rc, out = compare if isinstance(compare, tuple) else (0, json.dumps(compare))
            return SimpleNamespace(returncode=rc, stdout=out)
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr(g.subprocess, "run", run)
    return calls


def test_state_exact_head_allowed(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check() is None
    assert len(calls) == 2
    argv, kw = calls[0]
    assert argv[:6] == ["gh", "pr", "view", "12", "--repo", "example/protected"]
    assert {"baseRefOid", "files"} <= set(argv[-1].split(","))
    assert kw["timeout"] <= 20 and not kw.get("shell")
    argv, kw = calls[1]
    assert argv == ["gh", "api", f"repos/example/protected/compare/{BASE}...state"]
    assert kw["timeout"] <= 20 and not kw.get("shell")


# Base drift under CI (t_01a1d6201140c966): #2491's CI tested base 3a55357793; adc4b89b37 (#2487) then changed
# bb_tuner_feed.py on state, #2491 was rebase-merged without a check on the combination, and state went red.
def commit(sha, subject):
    return {"sha": sha, "commit": {"message": subject + "\n\nbody"}}


def delta(files, commits):
    return {"status": "ahead", "ahead_by": len(commits), "behind_by": 0, "total_commits": len(commits),
            "commits": commits, "files": [{"filename": f, "status": "modified", "previous_filename": None}
                                          for f in files]}


ADC = "adc4b89b37" + "0" * 30
SYNC_MBP = commit("09e03625ba" + "0" * 30, "sync: laptop-1 2026-11-13 09:56")
MERGE = commit("39166c2038" + "0" * 30, "Merge remote-tracking branch 'origin/state' into state")
PR2491 = payload(baseRefOid="3a5535779373cac2a8f154abe1ed0157ff16c27e")


def test_replay_pr2491_base_moved_on_its_paths_refused(monkeypatch):
    calls = probe(monkeypatch, PR2491, delta(
        ["hosts/mini-1/bb-tuner/bb_tuner_feed.py", "hosts/laptop-1/cron/jobs.json"],
        [SYNC_MBP, MERGE, commit(ADC, "bb-tuner feed: nous-1 token push off unless config nous_push is true")]))
    why = check()
    assert why and "adc4b89b37" in why and "update-branch" in why
    assert f"repos/example/protected/pulls/12/update-branch -f expected_head_sha={SHA}" in why
    assert "hosts/mini-1/bb-tuner/bb_tuner_feed.py" in why and "jobs.json" not in why
    assert calls[1][0][-1] == "repos/example/protected/compare/3a5535779373cac2a8f154abe1ed0157ff16c27e...state"


def test_unrelated_sync_churn_allowed(monkeypatch):
    probe(monkeypatch, payload(), delta(
        ["hosts/laptop-1/cron/jobs.json", "hosts/laptop-1/mods-loaded.json",
         "home/skills/devops/ci-runner-disk-recovery/references/org-runner-pools.md",
         "hosts/mini-1/mods-loaded.json"],
        [SYNC_MBP, MERGE, commit("125d853553" + "0" * 30, "skills: edits from hermes-worker-7")]))
    assert check() is None


def test_base_is_state_tip_allowed(monkeypatch):
    probe(monkeypatch, payload(), IDENTICAL)
    assert check() is None


def test_workflow_change_on_base_refused(monkeypatch):
    probe(monkeypatch, payload(), delta([".github/workflows/x.yml"], [commit("b" * 40, "ci: tweak x")]))
    why = check()
    assert why and ".github/workflows/x.yml" in why and "bbbbbbbbbb" in why


def test_root_and_same_area_overlap_refused(monkeypatch):
    # Same top-3 directory, different file; the same flat-dir file on both sides.
    probe(monkeypatch, payload(files=[{"path": "hosts/mini-1/bb-tuner/x.py"}]),
          delta(["hosts/mini-1/bb-tuner/tests/deep/y.py"], [commit("c" * 40, "bb-tuner: y")]))
    assert "cccccccccc" in check()
    probe(monkeypatch, payload(files=[{"path": "home/bin/tool"}]), delta(["home/bin/other.json"], [commit("c" * 40, "x")]))
    assert check()
    probe(monkeypatch, payload(files=[{"path": "tests/test_a.py"}]), delta(["tests/test_a.py"], [commit("c" * 40, "a")]))
    assert check()
    probe(monkeypatch, payload(files=[{"path": "hermes-sync"}]), delta(["hermes-sync"], [commit("c" * 40, "s")]))
    assert check()
    # A parent directory is a different area: hosts/mini-1/ churn does not hold a bb-tuner PR.
    probe(monkeypatch, payload(), delta(["hosts/mini-1/cron/jobs.json"], [commit("c" * 40, "sync: mini-1")]))
    assert check() is None
    # Flat top-level dirs and the repo root compare by file: other test modules / root files do not hold it
    # (live, 2026-11-13: open PRs 2507 and 2508 were held only by other tests/test_*.py modules).
    probe(monkeypatch, payload(files=[{"path": "tests/test_a.py"}, {"path": "README.md"}]),
          delta(["tests/test_b.py", "docs/x.md", "hermes-sync"], [commit("c" * 40, "b")]))
    assert check() is None


def test_renamed_from_pr_area_refused(monkeypatch):
    moved = delta(["elsewhere/z.py"], [commit("d" * 40, "move")])
    moved["files"][0]["previous_filename"] = "hosts/mini-1/bb-tuner/z.py"
    probe(monkeypatch, payload(), moved)
    assert check()


# t_01a1d62f8c197f3d: gaps found reviewing ce5447f7. Each rule has a refusing case and a non-refusing control.
def drift(monkeypatch, pr_files, base_files, commits=None):
    calls = probe(monkeypatch, payload(files=[{"path": p} for p in pr_files]),
                  delta(base_files, [commit("c" * 40, "base change")]), commits)
    return check(), calls


@pytest.mark.parametrize("support", ["tests/sync_test_support.py", "tests/placement_support.py",
                                     "tests/gateway_control_fixture.py", "tests/__init__.py"])
def test_flat_dir_shared_support_file_refused(monkeypatch, support):
    # A base change to a shared non-test file in tests/ holds a PR on any tests/test_*.py module ...
    why, _ = drift(monkeypatch, ["tests/test_a.py"], [support])
    assert why and f"{support} (shared ~ tests/test_a.py)" in why
    # ... and a PR changing that support file is held by a base change to any module beside it.
    why, _ = drift(monkeypatch, [support], ["tests/test_b.py"])
    assert why and "tests/test_b.py (shared ~ " in why


def test_flat_dir_shared_rule_controls_allowed(monkeypatch):
    # Other test modules, markdown beside them, other top-level dirs and nested dirs stay independent.
    assert drift(monkeypatch, ["tests/test_a.py"], ["tests/test_b.py", "tests/ci_durations.md"])[0] is None
    assert drift(monkeypatch, ["docs/a.md"], ["docs/b.md"])[0] is None
    assert drift(monkeypatch, ["tests/test_a.py"], ["scripts/check", "tests/fixtures/x/support.py"])[0] is None


@pytest.mark.parametrize("harness", ["tests/run_ci.py", "tests/requirements-ci.txt", "tests/requirements-ci-py39.txt",
                                     "requirements.txt"])
def test_ci_harness_file_holds_any_pr(monkeypatch, harness):
    why, _ = drift(monkeypatch, ["hosts/mini-1/bb-tuner/x.py"], [harness])
    assert why and harness in why


def test_nested_requirements_is_not_global(monkeypatch):
    assert drift(monkeypatch, ["hosts/mini-1/bb-tuner/x.py"], ["tests/fixtures/askeve/requirements.txt"])[0] is None


@pytest.mark.parametrize("pr_file,base_file", [
    ("tests/test_bb_tuner_feed.py", "home/scripts/bb_tuner_feed.py"),       # #2491's shape, dominant layout
    ("tests/test_hub_read_examples.py", "home/scripts/hub-read.py"),        # suffix, `-` normalized
    ("tests/test_hermes_sync_trash.py", "hermes-sync"),                     # root script, no extension
    ("tests/test_x_reply.py", "home/bin/x-reply-gate"),                     # code stem extends the key
    ("home/scripts/tests/test_mail_gate.py", "home/scripts/mail_gate.py"),  # nested tests dir
    ("home/bin/hermes-core-wt", "tests/test_hermes_core_wt_ship.py"),       # reverse: code PR, test on base
])
def test_test_code_coupling_refused(monkeypatch, pr_file, base_file):
    why, _ = drift(monkeypatch, [pr_file], [base_file])
    assert why and f"{base_file} (test-code ~ {pr_file})" in why


@pytest.mark.parametrize("pr_file,base_file", [
    ("tests/test_foo.py", "home/scripts/bar.py"),          # different module
    ("tests/test_foo.py", "home/scripts/foobar.py"),       # prefix without a `_` boundary
    ("tests/test_jobs_cron.py", "hosts/laptop-1/cron/jobs.json"),  # sync churn data, not code
    ("tests/test_utils_x.py", "home/scripts/utils.py"),    # generic stem
    ("tests/test_abc_x.py", "home/scripts/abc.py"),        # stem under 4 characters
    ("tests/test_foo.py", "home/scripts/tests/test_foo_data.py"),  # two test modules are not test<->code
    ("tests/test_feedback.py", "home/scripts/feed.py"),   # key extends the stem without a `_` boundary
    ("home/scripts/feed.py", "tests/test_feedback.py"),   # ... and the reverse direction
])
def test_test_code_coupling_controls_allowed(monkeypatch, pr_file, base_file):
    assert drift(monkeypatch, [pr_file], [base_file])[0] is None


def test_refusal_attributes_each_hit_to_its_base_commit(monkeypatch):
    # The culprit is past the first 8 content commits; the per-file lookup still names it.
    work = [commit(f"{i:x}" * 40, f"unrelated {i}") for i in range(1, 11)]
    culprit = commit("f" * 40, "bb-tuner feed: change the push token")
    compare = delta(["hosts/mini-1/bb-tuner/bb_tuner_feed.py"], work + [culprit])
    outside = {"sha": "e" * 40}   # older than the window: never named
    calls = probe(monkeypatch, payload(), compare,
                  {"hosts/mini-1/bb-tuner/bb_tuner_feed.py": [{"sha": culprit["sha"]}, outside]})
    why = check()
    assert "Last base change per file: hosts/mini-1/bb-tuner/bb_tuner_feed.py: ffffffffff bb-tuner feed" in why
    assert "+3 more" in why   # the oldest-first list still caps at 8, and the culprit is past it
    argv, kw = calls[2]
    assert argv == ["gh", "api", "repos/example/protected/commits?sha=state&path=hosts/mini-1/bb-tuner/"
                    "bb_tuner_feed.py&per_page=5"]
    assert kw["timeout"] <= 10 and not kw.get("shell")
    # A listing with no commit in the drift window attributes nothing (and never names an older commit).
    probe(monkeypatch, payload(), compare, {"hosts/mini-1/bb-tuner/bb_tuner_feed.py": [outside]})
    why = check()
    assert why and "Last base change per file" not in why and "eeeeeeeeee" not in why
    # A failed lookup is never trusted, even when it printed a listing naming a window commit.
    probe(monkeypatch, payload(), compare, {"hosts/mini-1/bb-tuner/bb_tuner_feed.py": (1, [{"sha": culprit["sha"]}])})
    why = check()
    assert why and "Last base change per file" not in why and "+3 more" in why


def test_attribution_is_bounded_and_best_effort(monkeypatch):
    files = [f"hosts/mini-1/bb-tuner/f{i}.py" for i in range(5)]
    calls = probe(monkeypatch, payload(), delta(files, [commit("c" * 40, "x")]))   # every lookup exits 1
    why = check()
    assert why and "Last base change per file" not in why and "cccccccccc x" in why
    assert len(calls) == 2 + 3


@pytest.mark.parametrize("compare", [
    (1, ""), (0, "not json"), (0, "[]"),
    (1, json.dumps(IDENTICAL)),   # a failed compare is never trusted, even when it printed a clean answer
    {k: v for k, v in delta([], []).items() if k != "files"},
    {k: v for k, v in delta([], []).items() if k != "commits"},
    {k: v for k, v in delta([], []).items() if k != "total_commits"},
    dict(delta([], []), total_commits=250),
    delta([], [commit(f"{i:040x}", "x") for i in range(250)]),   # 250 listed: the page cap, possibly more
    dict(delta([], [commit("e" * 40, "x")]), total_commits=2),
    dict(delta(["home/x/y/%d.md" % i for i in range(300)], [commit("e" * 40, "x")])),
    dict(delta([], []), status="diverged", behind_by=3),
    dict(delta([], []), behind_by=2), dict(IDENTICAL, behind_by=1),   # behind_by alone marks a non-ancestor base
])
def test_unreadable_or_truncated_compare_refused(monkeypatch, compare):
    probe(monkeypatch, payload(), compare)
    why = check()
    assert why and "cannot be ruled out" in why


@pytest.mark.parametrize("updates", [
    {"baseRefOid": None}, {"baseRefOid": "3a55357793"}, {"files": None}, {"files": []},
    {"files": [{"path": "a/%d" % i} for i in range(100)]}, {"files": [{"additions": 1}]},
])
def test_missing_base_or_files_refused_without_compare(monkeypatch, updates):
    data = payload(**updates)
    for key, value in updates.items():
        if value is None:
            del data[key]
    calls = probe(monkeypatch, data)
    why = check()
    assert why and "cannot be ruled out" in why
    assert len(calls) == 1


def test_state_without_exact_head_approval_refused(monkeypatch):
    # #2413: green rollup, right base and head, zero reviews and comments. The preflight used to admit it.
    probe(monkeypatch, payload(reviews=[]))
    assert "no approval of its exact head" in check()
    probe(monkeypatch, payload(reviews=[dict(APPROVAL, commit={"oid": "b" * 40})]))
    assert "no approval of its exact head" in check()


@pytest.mark.parametrize("updates", [
    {"baseRefName": "main"}, {"baseRefName": "other"}, {"baseRefName": None},
    {"headRefOid": "b" * 40}, {"state": "CLOSED"}, {"state": "MERGED"},
    {"number": 13}, {"url": "https://github.com/example/other/pull/12"},
    {"statusCheckRollup": []}, {"statusCheckRollup": None},
    {"statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": "SUCCESS"}]},
    {"statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]},
    {"statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"}]},
])
def test_uncertain_or_wrong_target_refused(monkeypatch, updates):
    probe(monkeypatch, payload(**updates))
    assert check()


@pytest.mark.parametrize("data", [None, [], {}, "bad", {"number": 12}])
def test_bad_response_refused(monkeypatch, data):
    probe(monkeypatch, data)
    assert check()


@pytest.mark.parametrize("extra", [["--auto"], ["--admin"], ["--squash"], ["--delete-branch"], ["--unknown"], ["--repo", "example/protected"]])
def test_noncanonical_merge_refused_without_probe(monkeypatch, extra):
    calls = probe(monkeypatch, payload())
    assert check(CMD + extra)
    assert not calls


def test_missing_pin_or_explicit_repo_refused(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check(CMD[:-2])
    assert check(CMD[:-1] + ["a" * 12])
    assert check(CMD[:4] + CMD[6:])
    assert not calls


def test_transport_failure_refused(monkeypatch):
    for error in (OSError("offline"), g.subprocess.TimeoutExpired("gh", 15)):
        def run(*args, **kwargs):
            raise error
        monkeypatch.setattr(g.subprocess, "run", run)
        assert check()
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert check()
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="not json"))
    assert check()


def test_legacy_status_and_bounded_rollup(monkeypatch):
    probe(monkeypatch, payload(statusCheckRollup=[{"__typename": "StatusContext", "state": "SUCCESS"}]))
    assert check() is None
    probe(monkeypatch, payload(statusCheckRollup=payload()["statusCheckRollup"] * 100))
    assert check()


def test_shell_python_and_tool_state_route(monkeypatch):
    import shlex
    probe(monkeypatch, payload())
    command = shlex.join(CMD)
    assert g.check_shell(command, repos={"example/protected"}, env={}) is None
    assert g.check_python(f"subprocess.run({CMD!r})", repos={"example/protected"}, env={}) is None
    assert g.check_tool("terminal", {"command": command}, repos={"example/protected"}, env={}) is None
    probe(monkeypatch, payload(baseRefName="main"))
    assert g.check_tool("terminal", {"command": command}, repos={"example/protected"}, env={})


def test_merge_api_stays_refused(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert check(["gh", "api", "repos/example/protected/pulls/12/merge"])
    assert not calls


# t_01a1d625bbebeaa8: `cd $HERMES_KANBAN_WORKSPACE; gh pr merge ...` for green, approved state PR #2511 was
# refused with the generic text: the literal `<cwd>/$VAR` became the gh probe's cwd, subprocess raised
# OSError and the preflight returned False.
def strict_probe(monkeypatch, data):
    """Like probe(), but a nonexistent cwd raises as real subprocess.run does."""
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if kwargs.get("cwd") is not None and not g.os.path.isdir(kwargs["cwd"]):
            raise FileNotFoundError(2, "No such file or directory", kwargs["cwd"])
        out = IDENTICAL if argv[:2] == ["gh", "api"] else data
        return SimpleNamespace(returncode=0, stdout=json.dumps(out))
    monkeypatch.setattr(g.subprocess, "run", run)
    return calls


def test_cd_unset_var_prefix_still_probes(monkeypatch, tmp_path):
    import shlex
    calls = strict_probe(monkeypatch, payload())
    command = "cd $HERMES_KANBAN_WORKSPACE; " + shlex.join(CMD)
    assert g.check_shell(command, cwd=str(tmp_path), repos={"example/protected"}, env={}) is None
    assert len(calls) == 2 and all(kw["cwd"] is None for _, kw in calls)
    assert g.check_tool("terminal", {"command": command, "workdir": str(tmp_path)},
                        repos={"example/protected"}, env={}) is None


def test_cd_set_var_prefix_expands(monkeypatch, tmp_path):
    import shlex
    calls = strict_probe(monkeypatch, payload())
    for target in ("$WS", "${WS}", '"$WS"'):
        calls.clear()
        command = f"cd {target} && " + shlex.join(CMD)
        assert g.check_shell(command, cwd="/", repos={"example/protected"}, env={"WS": str(tmp_path)}) is None
        assert calls[0][1]["cwd"] == str(tmp_path)


@pytest.mark.parametrize("updates,named", [
    ({"baseRefName": "main"}, "base is 'main', not 'state'"),
    ({"state": "MERGED"}, "state is 'MERGED', not OPEN"),
    ({"headRefOid": "b" * 40}, "not --match-head-commit aaaaaaaaaaaa"),
    ({"statusCheckRollup": []}, "statusCheckRollup has 0 entries"),
    ({"statusCheckRollup": [{"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
                            {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": "FAILURE"},
                            {"__typename": "CheckRun", "name": "e2e", "status": "IN_PROGRESS", "conclusion": None}]},
     "2 of 3 checks not green: tests=FAILURE, e2e=IN_PROGRESS"),
])
def test_failed_preflight_names_the_step(monkeypatch, updates, named):
    probe(monkeypatch, payload(**updates))
    why = check()
    assert why and "state preflight failed" in why and named in why
    assert "Refused direct gh pr merge" not in why


def test_gh_view_failure_named(monkeypatch):
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="HTTP 401: Bad credentials"))
    why = check()
    assert "gh pr view 12 --repo example/protected` exited 1 (HTTP 401: Bad credentials)" in why
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(g.subprocess, "run", boom)
    assert "could not run (OSError: offline)" in check()
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="not json"))
    assert "unreadable JSON" in check()


def test_non_state_form_keeps_generic_deny(monkeypatch):
    calls = probe(monkeypatch, payload())
    assert "Refused direct gh pr merge" in check(CMD + ["--auto"])
    assert not calls
