"""Publication race regressions; every GitHub call is intercepted."""
import importlib.util
import json
import pytest
from pathlib import Path

spec = importlib.util.spec_from_file_location("publication_fixture", Path(__file__).with_name("tests_turn.py"))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
g = f.g


def test_inventory_overflow_refuses_before_object_reads(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({1: f.pr(1, f.H2563, ["hermes-sync"])})
    # Expired records still consume inventory; a late-page holder cannot hide.
    for n in range(2, 4099):
        f.seed_turn(fake, n, f.NOW - 3 * g._TURN_TTL)
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    result = g._turn_acquire(1, fake.prs[1], f.R, None, {})
    assert "inventory exceeds 4096" in result
    assert not any("graphql" in call for call in fake.calls)
    assert "refs/land-turn/pr-1" not in fake.refs


def test_full_inventory_does_not_publish_one_more_ref(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({1: f.pr(1, f.H2563, ["hermes-sync"])})
    for n in range(2, 4098):
        f.seed_turn(fake, n, f.NOW - 3 * g._TURN_TTL)
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    result = g._turn_acquire(1, fake.prs[1], f.R, None, {})
    assert "inventory is full" in result
    assert "refs/land-turn/pr-1" not in fake.refs


def test_inventory_boundary_keeps_late_page_holder(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    for total in (4095, 4096):
        fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 99999)})
        for n in range(2, total + 1):
            f.seed_turn(fake, n, f.NOW - 3 * g._TURN_TTL)
        f.seed_turn(fake, 99999, f.NOW - 60)
        monkeypatch.setattr(g.subprocess, "run", fake.run)
        result = g._turn_acquire(1, fake.prs[1], f.R, None, {})
        assert "PR #99999 holds" in result
        assert "refs/land-turn/pr-1" not in fake.refs


def test_create_conflict_never_overwrites_concurrent_admission(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({1: f.pr(1, f.H2563, ["hermes-sync"])})
    winner = []

    def run(argv, **kw):
        if argv[2] == f"repos/{f.R}/git/refs" and not winner:
            # The contender saw absence. A different writer wins before POST.
            f.seed_turn(fake, 1, f.NOW - 120)
            winner.append(fake.refs["refs/land-turn/pr-1"])
        return fake.run(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", run)
    result = g._turn_acquire(1, fake.prs[1], f.R, None, {})
    assert fake.refs["refs/land-turn/pr-1"] == winner[0], result
    assert not any("PATCH" in call for call in fake.calls)
    assert fake.turn(1)["acquired"] == f.NOW - 120


def test_reader_does_not_delete_rearmed_ref_from_stale_snapshot(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({2: f.pr(2, f.H2563, ["hermes-sync"])})
    f.seed_turn(fake, 1, f.NOW - 3 * g._TURN_TTL)
    winner = []

    def run(argv, **kw):
        result = fake.run(argv, **kw)
        if argv[2] == "graphql" and not winner:
            # Reader already captured the expired OID. Owner rearms before cleanup.
            f.seed_turn(fake, 1, f.NOW)
            winner.append(fake.refs["refs/land-turn/pr-1"])
        return result

    monkeypatch.setattr(g.subprocess, "run", run)
    g._turn_hold_refusal(2, fake.prs[2], f.R, None, {})
    assert fake.refs.get("refs/land-turn/pr-1") == winner[0]
    assert not any("DELETE" in call for call in fake.calls)


def test_accepted_write_lost_response_preserves_original_age(monkeypatch):
    from types import SimpleNamespace
    clock = [f.NOW]
    monkeypatch.setattr(g, "_now", lambda: clock[0])
    fake = f.FakeGitHub({1: f.pr(1, f.H2563, ["hermes-sync"])})

    def run(argv, **kw):
        result = fake.run(argv, **kw)
        if argv[2] == f"repos/{f.R}/git/refs":
            return SimpleNamespace(returncode=1, stdout="", stderr="lost response")
        return result

    monkeypatch.setattr(g.subprocess, "run", run)
    result = g._turn_acquire(1, fake.prs[1], f.R, None, {})
    assert "write may have succeeded" in result
    original = fake.refs["refs/land-turn/pr-1"]
    clock[0] += 10
    assert "UTC" in g._turn_acquire(1, fake.prs[1], f.R, None, {})
    assert fake.refs["refs/land-turn/pr-1"] == original
    assert fake.turn(1)["acquired"] == f.NOW


def test_real_git_siblings_are_not_fast_forward(tmp_path):
    """Actual object ancestry oracle, not a fake PATCH's chosen status."""
    import os
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
           "GIT_AUTHOR_EMAIL": "fixture@example.com", "GIT_COMMITTER_EMAIL": "fixture@example.com"}

    def git(*args, text=None, check=True):
        return subprocess.run(["git", *args], cwd=tmp_path, env=env, input=text,
                              text=True, capture_output=True, check=check)

    git("init", "--bare", ".")
    tree = git("mktree", text="").stdout.strip()
    root = git("commit-tree", tree, text="root").stdout.strip()
    a = git("commit-tree", tree, "-p", root, text="first admission").stdout.strip()
    b = git("commit-tree", tree, "-p", root, text="racing admission").stdout.strip()
    assert git("merge-base", "--is-ancestor", root, a, check=False).returncode == 0
    assert git("merge-base", "--is-ancestor", a, b, check=False).returncode == 1
    assert git("merge-base", "--is-ancestor", b, a, check=False).returncode == 1
    blob = git("hash-object", "-w", "--stdin", text="blob").stdout.strip()
    for noncommit in (tree, blob, "f" * 40):
        assert git("commit-tree", tree, "-p", noncommit, text="invalid", check=False).returncode != 0


@pytest.mark.parametrize("prechecked", [False, True])
def test_rearm_conflict_uses_old_ticket_parent_without_force(monkeypatch, prechecked):
    from types import SimpleNamespace
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({1: f.pr(1, f.H2563, ["hermes-sync"])})
    f.seed_turn(fake, 1, f.NOW - 3 * g._TURN_TTL)
    old = fake.refs["refs/land-turn/pr-1"]
    winner = []
    attempted = []

    def run(argv, **kw):
        if "PATCH" in argv:
            body = json.loads(kw["input"])
            attempted.append(body)
            proposed = fake.commits[body["sha"]]
            assert proposed["parents"] == [old]
            # A sibling admission wins while this process is creating its commit.
            f.seed_turn(fake, 1, f.NOW - 1)
            winner.append(fake.refs["refs/land-turn/pr-1"])
            # Model GitHub's non-fast-forward rejection (no live API writes).
            if body.get("force") is False:
                return SimpleNamespace(returncode=1, stdout='{"message":"Update is not a fast forward"}', stderr="")
        return fake.run(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", run)
    with g._scope():
        if prechecked:
            assert g._turn_hold_refusal(1, fake.prs[1], f.R, None, {}) is None
            assert g._cache()[("own", 1)][1] == old
        result = g._turn_acquire(1, fake.prs[1], f.R, None, {}, prechecked=prechecked)
    assert attempted and attempted[0]["force"] is False
    assert fake.refs["refs/land-turn/pr-1"] == winner[0]
    assert "publication conflicted" in result
