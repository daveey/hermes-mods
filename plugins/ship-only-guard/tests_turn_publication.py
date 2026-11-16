"""Publication race regressions; every GitHub call is intercepted."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("publication_fixture", Path(__file__).with_name("tests_turn.py"))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
g = f.g


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


def test_rearm_conflict_uses_old_ticket_parent_without_force(monkeypatch):
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
    result = g._turn_acquire(1, fake.prs[1], f.R, None, {})
    assert attempted and attempted[0]["force"] is False
    assert fake.refs["refs/land-turn/pr-1"] == winner[0]
    assert "publication conflicted" in result
