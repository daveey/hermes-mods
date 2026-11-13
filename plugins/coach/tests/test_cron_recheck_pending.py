"""A deferred cron alarm is re-asked of the current cron classifier before filing (cron-4fc984eb0b).

The old collector counted gateway restart cuts of `x-reply-discovery` as failures ("3 failures in 24h (each
recovered)"); self-heal deferred the row on the concurrency cap; 3d196a72 (cron-70b179cf31) taught collect_cron that a
restart cut is not a failure, yet retry_deferred filed the queued row the next morning. retry_deferred's generic
detector recheck (_detector_exempts_now) had no cron kind, so a cron row must carry one (or, for rows queued before
the field, have it rebuilt from its details) and be withdrawn when cron_lifecycle_interrupt now matches.
"""
from contextlib import closing

import pytest
from test_self_heal import sh, core, load, ROOT

sg = load("coach_signals_cron_recheck_test", ROOT / "signals.py")

NOW = 1794583198
HOST = "example-host"
CUT = ("Interrupted by a gateway restart before the run finished (CronShutdownInterrupted: Cron job "
       "'x-reply-discovery' was stopped: Gateway shutdown interrupted the run)")
REAL = "RuntimeError: x api returned 503 Service Unavailable"


def _legacy(detail, name="x-reply-discovery", jid="5f1c0a9e7b21", prof="default", seen=1):
    """A cron row exactly as the pre-3d196a72 collector queued it (no recheck field)."""
    s = {"kind": "cron", "component": "cron", "at": NOW - 80000, "profile": prof, "host": HOST, "count": seen,
         "symptom": f"cron job '{name}' failed on {HOST}: {detail.splitlines()[0][:120]}",
         "details": (f"job {jid} ({name}, profile {prof}) failure_streak=0 last_status=ok "
                     f"filed: 3 failures in 24h (each recovered)\nscript=None\n{detail}")[:1500],
         "fp": sg.fp("cron", HOST, prof, name)}
    return sg.to_defect(s)


def _retry(store, d):
    sh._defer_investigation(d, {"retry_at": "2026-11-13T00:00:00Z"}, store)
    filed = []
    out = sh.retry_deferred(store, now=NOW, filer=lambda d, **kw: filed.append(d) or {"action": "filed"})
    with closing(sh._pending_db(store)) as db:
        left = db.execute("SELECT count(*) FROM pending").fetchone()[0]
    return out, filed, left


@pytest.fixture
def store(tmp_path, monkeypatch):
    for name in ("_sqlite_incident_over", "kanban_trigger_closed", "_monitor_withdrawn", "_resolved_crash_signal"):
        monkeypatch.setattr(sh, name, lambda *a, **k: None)
    return core.Store(tmp_path / "coach", clock=lambda: NOW)


def _skips(store):
    return [r for r in store.read_log() if r.get("event") == "selfheal_skip"]


@pytest.mark.parametrize("seen", [1, 2])
def test_queued_restart_cut_cron_row_is_withdrawn(store, seen):
    """The live cron-4fc984eb0b shape: queued under the old rule, the current classifier calls it a restart cut."""
    d = _legacy(CUT, seen=seen)
    out, filed, left = _retry(store, d)
    assert filed == [] and left == 0
    assert out and out[0]["action"] == "skip"
    assert out[0]["why"] == "detector_exempts_now: cron lifecycle interrupt (current classifier)"
    assert [r["fingerprint"] for r in _skips(store)] == [d["fingerprint"]]


def test_queued_ordinary_cron_failure_still_files(store):
    out, filed, left = _retry(store, _legacy(REAL))
    assert len(filed) == 1 and left == 0 and _skips(store) == []


def test_restart_cut_not_recovered_row_still_files(store, tmp_path):
    """The unrecovered-cut alarm quotes the cut detail too, but it is a real defect (the cut's work was lost)."""
    job = {"id": "5f1c0a9e7b21", "name": "x-reply-discovery", "last_run_at": None, "last_status": "error"}
    lost = sg._cron_cut_lost(job, {"at": NOW - 30000, "detail": CUT}, NOW - 100, HOST, "default")
    assert lost is not None
    d = sg.to_defect({**lost, "host": HOST})
    out, filed, left = _retry(store, d)
    assert len(filed) == 1 and _skips(store) == []


def test_legacy_row_with_mismatched_fingerprint_files(store):
    """The details parse is trusted only when it rebuilds the row's own fingerprint."""
    d = {**_legacy(CUT), "fingerprint": "cron-0000000000"}
    assert len(_retry(store, d)[1]) == 1 and _skips(store) == []


def test_collect_cron_carries_the_recheck(tmp_path):
    """New rows carry the detail they were judged on, so a later classifier change reaches them while queued."""
    import json
    h = tmp_path / "home"
    (h / "cron").mkdir(parents=True)
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat()
    (h / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "j1", "name": "x-reply-discovery", "failure_streak": 3, "last_status": "error",
         "last_failure": {"at": iso, "detail": REAL}}]}))
    out = sg.collect_cron(h, {"cron": {"default:j1": NOW - 7200}}, NOW, HOST)
    assert len(out) == 1
    rc = out[0]["recheck"]
    assert rc == {"kind": "cron_failure", "detail": REAL, "failure_kind": None}
    d = sg.to_defect(out[0])
    assert d["recheck"] == rc and sg.recheck_exempts(rc) is None
    # the typed record decides alone, exactly as in collect_cron
    assert sg.recheck_exempts({**rc, "failure_kind": "lifecycle_interrupt"})
    assert sg.recheck_exempts({**rc, "detail": CUT, "failure_kind": "some_other_kind"}) is None
