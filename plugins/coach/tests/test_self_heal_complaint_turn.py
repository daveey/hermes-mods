"""Cross-detector dedupe is about the user turn, not model-generated symptom words."""
from test_self_heal_lane import HUB, NOW, OPEN, Runner, core, defect, ev, sh, store
import pytest


def pair():
    # Incident replay: backstop and hook disagree on component and symptom.
    return (defect("bot", "fix not actually applied despite claiming done", source="backstop",
                   quote="so, can you fix it please"),
            defect("agent", "reported remaining approval problem without fixing it",
                   quote="so, can you fix it please", at=NOW + 17 * 3600))


def file(d, store, runner, now=NOW):
    return sh.file_defect(d, store=store, m="live", runner=runner, now=now,
                          cfg=HUB, budget_fn=OPEN, owner_fn=lambda *a: [], sig_owner_fn=lambda *a: [])


def test_backstop_then_complaint_same_quote_files_once(store):
    a, b = pair()
    runner = Runner()
    first = file(a, store, runner)
    second = file(b, store, runner, NOW + 17 * 3600)
    assert len(runner.creates()) == 1
    assert second["adopted"] and second["card"] == first["card"]
    state = sh.LaneState(store.root).get(a["fingerprint"])
    assert state["complaint_at"] == a["at"] and state["complaint_quote_hash"]
    assert ev(store, "selfheal_adopted_owner")[-1]["via"] == "complaint_turn"


def test_different_turns_in_same_session_file_twice(store):
    a, b = pair()
    b["quote"] = "the panel is now frozen and cannot close"
    runner = Runner()
    assert file(a, store, runner)["action"] == "filed"
    assert file(b, store, runner, NOW + 17 * 3600)["action"] == "filed"
    assert len(runner.creates()) == 2


def test_legacy_lane_without_turn_metadata_does_not_dedupe(store):
    a, b = pair()
    sh.LaneState(store.root).update(a["fingerprint"], status="filed", card="t_abcdef01",
                                  session_id=a["session_id"], component=a["component"], symptom=a["symptom"])
    runner = Runner()
    assert file(b, store, runner)["action"] == "filed"
    assert len(runner.creates()) == 1


@pytest.mark.parametrize("delta,expected", [(120, 1), (121, 2), (-120, 1), (-121, 2)])
def test_timestamp_tolerance_when_quote_differs(store, delta, expected):
    a, b = pair()
    b.update(complaint_at=a["at"] + delta, quote="quote captured differently")
    runner = Runner()
    file(a, store, runner)
    file(b, store, runner)
    assert len(runner.creates()) == expected


def test_same_quote_other_session_is_not_adopted(store):
    a, b = pair()
    b["session_id"] = "other-session"
    runner = Runner()
    file(a, store, runner)
    file(b, store, runner)
    assert len(runner.creates()) == 2


@pytest.mark.parametrize("status,age,expected", [
    ("fixed", 30 * 86400, 1), ("stale", 30 * 86400, 1),
    ("declined", 30 * 86400, 2), ("not_defect", 1, 1),
    ("not_defect", (sh.NOT_DEFECT_DAYS + 1) * 86400, 2)])
def test_closed_lane_policy(store, status, age, expected):
    a, b = pair()
    runner = Runner()
    file(a, store, runner)
    sh.LaneState(store.root).update(a["fingerprint"], status=status, closed_at=NOW - age, fixed_at=NOW - age)
    file(b, store, runner)
    assert len(runner.creates()) == expected


def test_quote_hash_normalizes_case_whitespace_and_capture_length(store):
    a, b = pair()
    a["quote"] = " A repeated   complaint " + "x" * 500
    b["quote"] = "a repeated complaint " + "x" * 280
    runner = Runner()
    file(a, store, runner)
    file(b, store, runner)
    assert len(runner.creates()) == 1


def test_proposed_owner_sync_launches_only_one_fix(store):
    a, b = pair()
    runner = Runner()
    file(a, store, runner)
    sh.record_event("proposed", a["fingerprint"], store=store, now=NOW + 10)
    file(b, store, runner)
    fixed = []
    sh.sync(store, runner=runner, now=NOW + 20, auto=True,
            fixer=lambda fp, **kw: fixed.append(fp) or {"card": "t_abcdef03"})
    assert fixed == [a["fingerprint"]]


@pytest.mark.parametrize("status", ["filed", "proposed", "fixed", "not_defect"])
def test_adoption_does_not_impose_owner_lifecycle_on_other_turns(store, status):
    a, b = pair()
    runner = Runner()
    file(a, store, runner)
    lane = sh.LaneState(store.root)
    lane.update(a["fingerprint"], status=status, closed_at=NOW - 1, fixed_at=NOW - 1)
    file(b, store, runner)
    assert lane.get(b["fingerprint"])["status"] == "adopted"
    # Same fingerprint, genuinely different complaint, even on the same UTC day.
    b.update(session_id="new-session", quote="another real failure", at=NOW + 3600)
    assert file(b, store, runner, NOW + 3600)["action"] == "filed"
    assert not lane.get(b["fingerprint"]).get("complaint_owner")


def test_alias_does_not_outlive_canonical_owner_decline(store):
    a, b = pair()
    runner = Runner()
    file(a, store, runner)
    file(b, store, runner)
    sh.record_event("declined", a["fingerprint"], store=store, now=NOW + 10)
    c = defect("desktop", "third detector phrasing", quote=a["quote"])
    assert file(c, store, runner, NOW + 20)["action"] == "filed"


def test_complaint_adoption_preserves_sqlite_incident_comment(store):
    a, b = pair()
    a.update(component="kanban")
    a["fingerprint"] = sh.fingerprint(a["component"], a["symptom"])
    runner = Runner()
    file(a, store, runner)
    b.update(component="kanban", symptom="database disk image is malformed")
    file(b, store, runner)
    assert len(runner.creates()) == 1
    assert len([argv for argv, _ in runner.calls if "comment" in argv]) == 1


def test_alias_preserves_own_prior_fix_for_a_new_recurrence(store):
    a, b = pair()
    old = {**b, "quote": "old unrelated defect", "at": NOW - 10 * 86400}
    runner = Runner()
    file(old, store, runner, NOW - 10 * 86400)
    sh.record_event("fixed", b["fingerprint"], "t_abcdef04", store=store, now=NOW - 9 * 86400)
    file(a, store, runner)
    assert file(b, store, runner)["adopted"]
    new = {**b, "quote": "a new real recurrence", "at": NOW + 10 * 86400}
    out = file(new, store, runner, NOW + 10 * 86400)
    assert out["action"] == "filed"
    state = sh.LaneState(store.root).get(b["fingerprint"])
    assert state["recurrences"] == 1 and state["fix_card"] == "t_abcdef04"
    assert "(came back)" in runner.creates()[-1][3]


def test_old_alias_turn_survives_owner_refiling(store):
    a, b = pair()
    runner = Runner()
    file(a, store, runner)
    file(b, store, runner)
    sh.record_event("fixed", a["fingerprint"], "t_abcdef04", store=store, now=NOW)
    new = {**a, "session_id": "another-session", "quote": "new occurrence", "at": NOW + 10 * 86400}
    file(new, store, runner, NOW + 10 * 86400)
    count = len(runner.creates())
    assert file(b, store, runner, NOW + 11 * 86400)["adopted"]
    assert len(runner.creates()) == count


def test_session_adoption_persists_turn_for_later_detector(store):
    a, b = pair()
    runner = Runner()
    # The session card names the defect: a backstop finding needs subject evidence, not just the session
    # (agent-e5b6dcf428). This test is about the adopted turn persisting for the later detector.
    out = sh.file_defect(a, store=store, m="live", runner=runner, now=NOW, cfg=HUB, budget_fn=OPEN,
                         owner_fn=lambda *a: [{"id": "t_abcdef05", "status": "running", "board": None,
                                               "title": "Bot fix not actually applied despite claiming done"}])
    assert out["adopted"]
    assert file(b, store, runner)["card"] == "t_abcdef05"
    assert not runner.creates()


def _occurrence(at=NOW):
    d = defect("gateway", "gateway (default) on macbook supervisor restarted into a live gateway", source="signal",
               quote="", session_id=None, at=at, comment_open_card=True)
    d["fingerprint"] = "gateway-cc28b19bca"
    return d


def test_supervised_refusal_comments_on_the_open_fix_card_instead_of_filing(store):
    sh.LaneState(store.root).update("gateway-cc28b19bca", status="approved", card="t_abcdef10", fix_card="t_abcdef11",
                                  component="gateway", symptom="restart storm")
    runner = Runner()
    out = file(_occurrence(), store, runner)
    assert out["card"] == "t_abcdef11" and out["action"] == "skip"
    comments = [argv for argv, _ in runner.calls if "comment" in argv]
    assert len(comments) == 1 and comments[0][comments[0].index("comment") + 1] == "t_abcdef11"
    assert runner.creates() == []
    assert ev(store, "selfheal_occurrence_comment")[-1]["ok"] is True


def test_supervised_refusal_after_the_fix_is_a_recurrence_not_a_comment(store):
    sh.record_event("fixed", "gateway-cc28b19bca", "t_abcdef11", store=store, now=NOW - 30 * 86400)
    runner = Runner()
    out = file(_occurrence(), store, runner)
    assert out["action"] == "filed"
    assert not [argv for argv, _ in runner.calls if "comment" in argv]
