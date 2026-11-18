"""Waiting-place reader contract; writers are not enabled by these tests."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("waiting_fixture", Path(__file__).with_name("tests_turn.py"))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
g = f.g


@pytest.fixture(autouse=True)
def isolate_owner(monkeypatch):
    monkeypatch.setattr(g, "_turn_owner", lambda env: {})


def record(**changes):
    return {"pr": 1, "head": f.H2563, "files": ["hermes-sync"],
            "acquired": f.NOW - 60, "expires": f.NOW - 60 + g._TURN_TTL,
            "waiting": True, **changes}


@pytest.mark.parametrize("waiting", ["true", 1, None, [], {}])
def test_waiting_marker_must_be_boolean(waiting):
    rec = record(waiting=waiting)
    assert g.parse_turn("land-turn pr=1\n\n" + json.dumps(rec)) is None


def test_waiting_marker_preserves_legacy_and_original_order():
    for marker in ({}, {"waiting": False}, {"waiting": True}):
        rec = record()
        rec.pop("waiting")
        rec.update(marker)
        assert g.parse_turn("land-turn pr=1\n\n" + json.dumps(rec)) == rec
        assert g._turn_order(rec) == (f.NOW - 60, 1)


@pytest.mark.parametrize("identical", [False, True])
def test_new_exact_approval_cannot_preserve_authored_waiting_patch(monkeypatch, identical):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    data = f.pr(1, f.H2600, ["hermes-sync"])
    calls = []

    def carry(head, original, blocked, repo, cwd, env):
        calls.append((head, original, blocked))
        return identical, "fixture patch identity"

    monkeypatch.setattr(g, "_carry_one", carry)
    assert g._turn_yielded(record(), data, f.R, None, {}) is (not identical)
    assert calls == [(f.H2600, f.H2563, False)]


def test_waiting_eligibility_cache_does_not_reuse_legacy_verdict(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_carry_one", lambda *args: (False, "authored changes"))
    data = f.pr(1, f.H2600, ["hermes-sync"])
    with g._scope():
        assert g._turn_yielded(record(waiting=False), data, f.R, None, {}) is False
        assert g._turn_yielded(record(), data, f.R, None, {}) is True


def test_refreshed_waiting_peer_is_undecided_even_with_exact_approval(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    data = f.pr(1, f.H2600, ["hermes-sync"])
    outcome = g.peer_eligibility(record(), data)
    assert isinstance(outcome, g._Undecided)
    assert "patch" in outcome.reason
    # A legacy record has no original-patch constraint.
    assert g.peer_eligibility(record(waiting=False), data) is True


@pytest.mark.parametrize("uncertain", [False, True])
def test_only_definite_hold_has_structured_admission_evidence(monkeypatch, uncertain):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    age = g._TURN_REFRESH_GRACE + 1 if uncertain else 60
    f.seed_turn(fake, 1, f.NOW - age)
    hold = g._turn_hold_refusal(2, fake.prs[2], f.R, None, {})
    assert isinstance(hold, str)
    assert isinstance(hold, g._DefiniteTurnHold) is (not uncertain)
    if not uncertain:
        assert hold.holder["pr"] == 1
        assert hold.ref == "refs/land-turn/pr-1"
        assert "PR #1 holds" in hold
    else:
        assert "may still hold" in hold


def test_changed_waiter_without_any_approval_yields_before_uncertainty(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    data = f.pr(1, f.H2600, ["hermes-sync"])
    data.update(reviews=[], comments=[])
    assert g.peer_eligibility(record(), data) is False


def test_changed_waiter_hold_does_not_offer_exact_approval_as_patch_proof(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    fake = f.FakeGitHub({n: f.pr(n, f.H2600, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    rec = record()
    oid = "e" * 40
    fake.refs["refs/land-turn/pr-1"] = oid
    fake.commits[oid] = {"message": "land-turn pr=1\n\n" + json.dumps(rec)}
    hold = g._turn_hold_refusal(2, fake.prs[2], f.R, None, {})
    assert "may still hold" in hold
    assert "gets an exact-head verdict" not in hold
    assert "original admitted patch" in hold


@pytest.mark.parametrize("enabled", [False, True])
def test_denied_green_entrant_records_waiting_place_and_cannot_be_overtaken(monkeypatch, enabled):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", enabled, raising=False)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2, 3)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    f.seed_turn(fake, 1, f.NOW - 60)
    result = f.merge(2, f.H2563)
    assert "PR #1 holds" in result
    if not enabled:
        assert fake.turn(2) is None
        return
    waiting = fake.turn(2)
    assert waiting is not None
    assert waiting["waiting"] is True
    assert "waiting place recorded" in result
    assert "PR #2 now holds" not in result
    original = dict(waiting)
    fake.pr_state[1] = "closed"
    assert "PR #2 holds" in f.merge(3, f.H2563)
    assert f.merge(2, f.H2563) is None  # public hook allows with no refusal
    assert fake.turn(2) == original


@pytest.mark.parametrize("holder_time", [f.NOW, f.NOW + 1])
def test_waiter_does_not_jump_lower_number_same_second_or_clock_skew(monkeypatch, holder_time):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True, raising=False)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    f.seed_turn(fake, 2, holder_time)
    assert "PR #2 holds" in f.merge(1, f.H2563)
    assert fake.turn(1) is None


def test_waiting_cli_reports_record_not_effective_owner(monkeypatch):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    f.seed_turn(fake, 1, f.NOW - 60)
    status, message = g.land_turn(2, f.R, f.H2563, env={})
    assert status == 1  # only an effective owner returns zero
    assert "waiting place recorded" in message
    assert "No land turn recorded" not in message
    assert "PR #2 holds" not in message


@pytest.mark.parametrize("failure", ["ci", "approval", "unknown", "unreadable"])
def test_waiting_admission_never_writes_without_complete_eligible_hold(monkeypatch, failure):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    age = g._TURN_REFRESH_GRACE + 1 if failure == "unknown" else 60
    f.seed_turn(fake, 1, f.NOW - age)
    if failure == "ci":
        fake.prs[2]["statusCheckRollup"][0]["conclusion"] = "FAILURE"
    if failure == "approval":
        fake.prs[2]["reviews"] = []
    if failure == "unreadable":
        fake.refs_fail = True
    assert isinstance(f.merge(2, f.H2563), str)
    assert fake.turn(2) is None


@pytest.mark.parametrize("age", [30, g._TURN_TTL + 30])
def test_waiting_admission_does_not_rewrite_live_or_cooling_generation(monkeypatch, age):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    f.seed_turn(fake, 1, f.NOW - 60)
    f.seed_turn(fake, 2, f.NOW - age)
    original = dict(fake.refs)
    assert isinstance(f.merge(2, f.H2563), str)
    assert fake.refs == original


@pytest.mark.parametrize("case", ["unreadable", "deadline", "existing"])
@pytest.mark.parametrize("entry", ["cli", "hook"])
def test_cli_retains_waiting_publication_evidence_on_retry_and_failure(monkeypatch, case, entry):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    f.seed_turn(fake, 1, f.NOW - 60)
    if case == "existing":
        g.land_turn(2, f.R, f.H2563, env={})
    if case == "unreadable":
        real = fake.run
        def run(argv, **kw):
            if "matching-refs" in " ".join(argv) and fake.turn(2):
                return f.SimpleNamespace(returncode=1, stdout="{}", stderr="")
            return real(argv, **kw)
        monkeypatch.setattr(g.subprocess, "run", run)
    if case == "deadline":
        real_stage = g._stage
        def stage(name, fn, *args):
            result = real_stage(name, fn, *args)
            if name == "waiting-place admission":
                raise g._PreflightExpired("deadline exceeded during " + name)
            return result
        monkeypatch.setattr(g, "_stage", stage)
    if entry == "cli":
        status, message = g.land_turn(2, f.R, f.H2563, env={})
        assert status == 1
    else:
        message = g.bounded_preflight(f.merge, 2, f.H2563)
        assert isinstance(message, str)
    assert fake.turn(2)
    assert "refs/land-turn/pr-2" in message
    assert "not merge permission" in message
    assert "No land turn recorded" not in message


@pytest.mark.parametrize("failure", ["malformed", "late", "older_waiter"])
def test_waiting_unknown_or_budget_or_existing_order_never_overwrites(monkeypatch, failure):
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True)
    fake = f.FakeGitHub({n: f.pr(n, f.H2563, ["hermes-sync"]) for n in (1, 2, 3)})
    monkeypatch.setattr(g.subprocess, "run", fake.run)
    f.seed_turn(fake, 1, f.NOW - 60)
    if failure == "malformed":
        fake.refs["refs/land-turn/pr-2"] = "e" * 40
        fake.commits["e" * 40] = {"message": "unreadable waiting generation"}
    elif failure == "late":
        monkeypatch.setattr(g, "_left", lambda: 0.1)
    else:
        f.seed_turn(fake, 3, f.NOW + 1)
    before = dict(fake.refs)
    result = f.merge(2, f.H2563)
    assert fake.refs == before
    if failure == "late":
        assert "--land-turn" in result


def test_waiting_promotion_keeps_original_record_and_nontransitive_lanes():
    a = record(pr=1, files=["x"], acquired=f.NOW - 90)
    b = record(pr=2, files=["x", "y"], acquired=f.NOW - 60)
    c = record(pr=3, files=["y"], acquired=f.NOW - 30)
    snapshot = [(f"refs/land-turn/pr-{r['pr']}", r) for r in (a, b, c)]
    before = json.dumps(snapshot, sort_keys=True)
    active = {1, 2, 3}
    select = lambda: [r["pr"] for _, r in g.effective_turns(
        snapshot, lambda ref, rec: rec["pr"] in active, lambda: None)]
    assert select() == [1, 3]  # waiting B does not reserve y against C
    active.remove(1)
    assert select() == [2]  # promotion is derived, never a ref rewrite
    assert json.dumps(snapshot, sort_keys=True) == before
