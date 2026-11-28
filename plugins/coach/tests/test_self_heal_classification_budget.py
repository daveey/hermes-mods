"""A failing severity service must not starve the rest of the natural cron pass."""
import json
import subprocess
from types import SimpleNamespace

from test_self_heal_one_card import sh, env, defect, NOW


def seed(e, n):
    for i in range(n):
        d = {**defect(symptom=f"separate failure {i}", at=NOW-60),
             "fingerprint": f"budget-{i:04d}"}
        sh._defer_investigation(d, {"retry_at": sh._iso(NOW-1)}, e.store)


def test_failed_classifier_stops_batch_but_cron_continues(env, monkeypatch):
    e = env
    seed(e, 20)
    monkeypatch.setattr(sh, "lane_config", lambda: {"backlog_ranked": True})
    monkeypatch.setattr(sh, "mode", lambda: "live")
    monkeypatch.setattr(sh, "remote_verify", lambda: None)
    monkeypatch.setattr(sh, "note_pull", lambda *a: None)
    monkeypatch.setattr(sh, "refresh_backlog_report", lambda *a: None)
    monkeypatch.setattr(sh, "fold_pending_keys", lambda *a: None)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(kwargs["input"])
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", unavailable)
    monkeypatch.setattr(sh, "dupe_model", lambda: ("fixture", "fixture"))
    filed = []
    monkeypatch.setattr(sh, "file_defect", lambda d, **kw: filed.append(d) or
                        {"action": "deferred", "stop_pass": True})
    phases = []
    monkeypatch.setattr(sh, "sync", lambda *a, **kw: phases.append("sync") or [])
    monkeypatch.setattr(sh, "drain", lambda *a: phases.append("drain") or [])
    sh.cron(e.root, store=e.store, now=NOW, pull_fn=lambda _: None,
            backstop_fn=lambda *a: phases.append("backstop") or [],
            liveness_fn=lambda *a, **kw: phases.append("liveness") or {"action": "ok"})
    assert len(calls) == 1
    assert phases == ["sync", "drain", "backstop", "liveness"]
    assert filed
    with sh.contextlib.closing(sh._pending_db(e.store)) as db:
        assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 20


def setup_batch(e, monkeypatch):
    monkeypatch.setattr(sh, "lane_config", lambda: {"backlog_ranked": True})
    monkeypatch.setattr(sh, "fold_pending_keys", lambda *a: None)
    monkeypatch.setattr(sh, "refresh_backlog_report", lambda *a: None)
    monkeypatch.setattr(sh, "dupe_model", lambda: ("fixture", "fixture"))
    return lambda now=NOW: sh.retry_deferred(e.store, now=now,
        filer=lambda *a, **kw: {"action": "deferred", "stop_pass": True})


def test_successful_slow_classifier_has_aggregate_deadline(env, monkeypatch):
    seed(env, 20)
    run = setup_batch(env, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(sh.time, "monotonic", lambda: clock[0])
    calls = []
    def slow(*a, **kw):
        calls.append(kw["input"])
        clock[0] += 30
        return SimpleNamespace(returncode=0, stdout='{"severity": 3}')
    monkeypatch.setattr(subprocess, "run", slow)
    run()
    assert len(calls) == 2  # 60 seconds; a per-call timeout is not a batch budget


def test_fast_successful_classifier_has_count_bound(env, monkeypatch):
    seed(env, 20)
    run = setup_batch(env, monkeypatch)
    calls = []
    def fast(*a, **kw):
        calls.append(kw["input"])
        return SimpleNamespace(returncode=0, stdout='{"severity": 3}')
    monkeypatch.setattr(subprocess, "run", fast)
    run()
    assert len(calls) == 16
    run(NOW+3600)
    assert len(calls) == 20  # cached successes cost no new calls


def test_failed_row_does_not_starve_next_row_on_successor_pass(env, monkeypatch):
    seed(env, 3)
    run = setup_batch(env, monkeypatch)
    seen = []
    def unavailable(*a, **kw):
        seen.append(json.loads(kw["input"])["symptom"])
        return SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(subprocess, "run", unavailable)
    for i in range(3):
        run(NOW+i*3600)
    assert len(seen) == len(set(seen)) == 3


def test_occurrence_fold_preserves_classification_retry_order():
    old = {**defect(), "backlog_classification_attempt": NOW}
    new = {**old, "at": NOW+1, "quote": "new occurrence"}
    del new["backlog_classification_attempt"]
    assert sh.fold_payloads(old, new)["backlog_classification_attempt"] == NOW


