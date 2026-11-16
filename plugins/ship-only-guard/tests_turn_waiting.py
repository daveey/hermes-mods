"""Waiting-place reader contract; writers are not enabled by these tests."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("waiting_fixture", Path(__file__).with_name("tests_turn.py"))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
g = f.g


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
