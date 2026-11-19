"""Enabled rollout: actual admission/cap reads over a fixture-owned board."""
import json
import sqlite3

import pytest
from test_self_heal_one_card import sh, env, defect, OPEN, NOW

WORDS = ("apple", "banana", "cherry", "date", "elderberry", "fig", "grape", "hazelnut", "iris")


@pytest.fixture
def ranked(env, monkeypatch):
    cfg = {"backlog_ranked": True, "daily_card_cap": 5}
    monkeypatch.setattr(sh, "lane_config", lambda: cfg)
    monkeypatch.setattr(sh, "_dupe_check", lambda *a, **k: None)
    monkeypatch.setattr(sh, "backlog_classify", lambda d: {**d, "backlog_severity_ready": True,
                                                         "backlog_severity": d.get("backlog_severity", 2)})
    return env


def file(e, d, now=NOW, **kw):
    e.board.now = now
    return sh.file_defect(d, store=e.store, m="live", runner=e.board, now=now, budget_fn=OPEN, **kw)


def drain(e, now):
    return sh.retry_deferred(e.store, now=now,
        filer=lambda d, **kw: file(e, d, now=kw["now"], backlog_drain=kw["backlog_drain"]))


def pending(e):
    with sh.contextlib.closing(sh._pending_db(e.store)) as db:
        return {fp: json.loads(p) for fp, p in db.execute("SELECT fp,payload FROM pending")}


def test_cap_five_files_highest_not_first_arrival(ranked):
    e = ranked
    now = int(NOW) // 86400 * 86400 + 23 * 3600
    ds = [defect(symptom=f"different failure {WORDS[i]}", backlog_severity=1+i%4,
                 p_defect=.5+i/100, at=now-60) for i in range(9)]
    for d in ds:
        assert file(e, d, now)["action"] == "queued"
    assert not e.board.creates()
    expected = sorted(ds, key=lambda d: sh.backlog_priority(d, now))[:5]
    results = drain(e, now)
    assert len([r for r in results if r["action"] == "filed"]) == 5
    keys = [row[2] for row in e.board.rows()]
    assert keys == [sh.inv_key(d["fingerprint"], 1) for d in expected]
    assert len(pending(e)) == 4


def test_midday_discovery_competes_same_day(ranked):
    e = ranked
    day = int(NOW) // 86400 * 86400
    for i in range(4):
        file(e, defect(symptom=f"early minor failure {WORDS[i]}", backlog_severity=1, at=day), day)
    assert len([r for r in drain(e, day) if r["action"] == "filed"]) == 1
    high = defect(symptom="important later failure", backlog_severity=4, at=day+13*3600)
    assert file(e, high, day+13*3600)["action"] == "queued"
    results = drain(e, day+13*3600)
    assert results[0]["key"] == sh.inv_key(high["fingerprint"], 1)
    assert len(e.board.creates()) == 3


def test_old_rows_survive(ranked):
    e = ranked
    d = defect(at=NOW-30*86400)
    sh._defer_investigation(d, {"retry_at": sh._iso(NOW-1)}, e.store)
    seen = []
    sh.retry_deferred(e.store, now=NOW, filer=lambda d, **kw: seen.append(d) or {"action": "error"})
    assert seen and d["fingerprint"] in pending(e)


@pytest.mark.parametrize("component,kind", [("kanban", "worker"), ("auth", "errors"),
    ("quota", "signal"), ("pacing", "signal"), ("billing", "signal"), ("gateway", "errors"),
    ("agent", "down"), ("agent", "corruption")])
def test_bypass_first_ignores_pacing_not_cap(ranked, component, kind):
    e = ranked
    day = int(NOW)//86400*86400
    file(e, defect(symptom="ordinary first", at=day), day)
    drain(e, day)
    urgent = defect(symptom="urgent second", component=component, kind=kind, at=day)
    # The binding amendment queues emergencies too, first in the next drain.
    assert file(e, urgent, day)["action"] == "queued"
    assert drain(e, day)[0]["action"] == "filed"
    assert sh.backlog_allowance(5, 5, day, True) == 0


def test_detector_text_cannot_spoof_bypass():
    assert not sh.backlog_bypass({"component": "desktop", "kind": "hermes_bug",
                                  "symptom": "quota credentials outage billing down"})


def test_concurrent_fold_survives_drain_delete(ranked):
    e = ranked
    d = defect(at=NOW-60)
    file(e, d)
    def writer(payload, **kw):
        sh._defer_investigation({**d, "at": NOW, "symptom": "new occurrence"},
                               {"retry_at": sh._iso(NOW)}, e.store)
        return {"action": "filed"}
    sh.retry_deferred(e.store, now=NOW, filer=writer)
    assert pending(e)[d["fingerprint"]]["occurrence_count"] == 2


def test_unreadable_cap_fails_closed(ranked, monkeypatch):
    def fail(*a, **k):
        raise sqlite3.OperationalError("fixture unavailable")
    monkeypatch.setattr(sh, "cards_created_today", fail)
    assert file(ranked, defect())["action"] == "error"
    assert not ranked.board.creates()


def test_placement_refusal_stops_ranked_pass(ranked):
    e = ranked
    for i in range(3):
        file(e, defect(symptom=f"failure {WORDS[i]}"))
    seen = []
    sh.retry_deferred(e.store, now=NOW, filer=lambda d, **kw: seen.append(d) or
                     {"action": "error", "placement_refused": True})
    assert len(seen) == 1 and len(pending(e)) == 3


def test_hourly_gate(ranked):
    e = ranked
    file(e, defect())
    seen = []
    f = lambda d, **kw: seen.append(d) or {"action": "error"}
    sh.retry_deferred(e.store, now=NOW, filer=f, hourly=True)
    sh.retry_deferred(e.store, now=NOW+300, filer=f, hourly=True)
    assert len(seen) == 1
    sh.retry_deferred(e.store, now=NOW+3600, filer=f, hourly=True)
    assert len(seen) == 2
