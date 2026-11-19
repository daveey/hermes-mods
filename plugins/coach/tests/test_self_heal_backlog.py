"""Importance backlog: deterministic score, bounded evidence, no lost frequency."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("coach_backlog_test", Path(__file__).resolve().parents[1] / "self_heal.py")
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)
NOW = 1800000000.0
WEEK = 7 * 86400


def detection(at=NOW, **kw):
    return {"fingerprint": "desktop-example", "symptom": "broken button", "at": at,
            "source": "signal", "p_defect": 0.5, **kw}


def test_exact_score_and_decay():
    d = detection(backlog_severity=4)
    assert sh.backlog_score(d, NOW) == pytest.approx(4 * 1 * .5 * .8)
    assert sh.backlog_score(d, NOW + WEEK) == pytest.approx(.8)
    assert sh.backlog_score(d, NOW + 100 * WEEK) > 0


def test_unknown_probability_source_and_severity():
    d = detection(p_defect=None, source=None)
    assert sh.backlog_score(d, NOW) == pytest.approx(2 * .5 * .5)
    assert sh.backlog_score(detection(p_defect=0), NOW) == 0


def test_complaint_regression_and_manual_boost():
    d = detection(source="complaint", backlog_bump=2)
    assert sh.backlog_score(d, NOW, recurrence=True) == pytest.approx(2 * .5 * 1 * 1.5 * 2 * 2)


def test_fold_keeps_frequency_beyond_evidence_window():
    d = detection(at=NOW - 100)
    for i in range(1, 50):
        d = sh.fold_payloads(d, detection(at=NOW - 100 + i))
    assert len(d["occurrences"]) == sh.PENDING_OCCURRENCES_MAX
    expected = sum(.5 ** ((100 - i) / WEEK) for i in range(50))
    assert sh.backlog_score(d, NOW) == pytest.approx(2 * .5 * .8 * expected)
    again = sh.fold_payloads(d, detection(at=NOW - 51))
    assert sh.backlog_score(again, NOW) == pytest.approx(sh.backlog_score(d, NOW))


def test_severity_failure_neutral_and_retryable():
    def fail(*args):
        raise TimeoutError("test timeout")
    d = sh.backlog_classify(detection(), judge=fail)
    assert d["backlog_severity"] == 2
    assert not d["backlog_severity_ready"]
    d = sh.backlog_classify(d, judge=lambda *args: '{"severity": 4}')
    assert d["backlog_severity_ready"]
    assert d["backlog_severity"] == 4
    assert sh.backlog_classify(d, judge=fail) == d


def test_shadow_orders_without_changing_pending_or_lane(tmp_path):
    import json
    from types import SimpleNamespace
    events = []
    store = SimpleNamespace(root=tmp_path, log=events.append)
    db = sh._pending_db(store)
    try:
        with db:
            for index, severity in enumerate((1, 4, 2, 3)):
                d = detection(fingerprint=f"desktop-{index}", backlog_severity=severity)
                db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)",
                           (d["fingerprint"], json.dumps(d), NOW-1, str(index)))
        before = db.execute("SELECT * FROM pending ORDER BY fp").fetchall()
        result = sh.backlog_shadow(store, NOW)
        assert [r["fingerprint"] for r in result] == ["desktop-1", "desktop-3", "desktop-2", "desktop-0"]
        assert all(r["event"] == "selfheal_backlog_would_file" for r in events)
        assert db.execute("SELECT * FROM pending ORDER BY fp").fetchall() == before
        assert not (tmp_path / "selfheal_lane.json").exists()
    finally:
        db.close()


def test_newest_breaks_ties():
    assert sh.backlog_priority(detection(at=NOW), NOW) < sh.backlog_priority(detection(at=NOW-1), NOW)
