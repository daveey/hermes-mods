"""A deferred signal is re-asked of its own detector before filing (t_01a1d5815b28b08b, agent-ff48223e1b).

signals.template_holds flagged report_only holds on "[IMPORTANT: 2 background processes compl" at 03:33; self-heal
deferred it; the notice classifier was fixed at 14:41 (81139c60) so the detector exempts that head now, yet the
15:19 retry still filed it. retry_deferred must re-run the detector's exemption predicate and withdraw the row.
"""
from contextlib import closing

import pytest
from test_self_heal import sh, core, load, ROOT

sg = load("coach_signals_recheck_test", ROOT / "signals.py")

NOW = 1794583198
NOTICE = "[IMPORTANT: 2 background processes compl"
HUMAN = "why is the deploy still red on the hub??"


def _signal(rule, head, legacy=False):
    s = {"kind": "coach", "component": "agent", "at": NOW - 40000, "session_id": "", "host": "example-host",
         "symptom": f"coach {rule} held 3 replies whose user turn has the same opening text \"{head}\" in 3 chats: "
                    f"the rule is firing on a fixed (automation) prompt",
         "details": "3 holds in 3 chats", "fp": sg.fp("agent", "template-hold", rule, head.lower()),
         "comment_open_card": True, "recheck": sg.template_recheck(rule, head)}
    if legacy:  # payloads deferred before the recheck field existed (the live agent-ff48223e1b row)
        s.pop("recheck")
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


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("rule,head", [("report_only", NOTICE), ("merge_forecast", HUMAN)])
def test_detector_now_exempts_pending_template_hold(store, legacy, rule, head):
    assert sg._notice_head(NOTICE) and rule in (sg.NOTICE_JUDGING_RULES | sg._reply_only_rules())
    d = _signal(rule, head, legacy)
    out, filed, left = _retry(store, d)
    assert filed == [] and left == 0
    assert out and out[0]["action"] == "skip" and out[0]["why"].startswith("detector_exempts_now")
    assert [r["fingerprint"] for r in _skips(store)] == [d["fingerprint"]]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("rule,head", [("report_only", HUMAN), ("status_only", NOTICE)])
def test_detector_still_fires_files(store, legacy, rule, head):
    out, filed, left = _retry(store, _signal(rule, head, legacy))
    assert len(filed) == 1 and left == 0 and _skips(store) == []


def test_legacy_symptom_with_a_mismatched_fingerprint_files(store):
    """The symptom parse is trusted only when it rebuilds the row's own fingerprint."""
    d = {**_signal("report_only", NOTICE, legacy=True), "fingerprint": "agent-0000000000"}
    out, filed, left = _retry(store, d)
    assert len(filed) == 1 and _skips(store) == []


def test_unknown_recheck_kind_and_broken_detector_fail_open(store, monkeypatch):
    d = {**_signal("report_only", NOTICE), "recheck": {"kind": "no-such-detector"}}
    assert len(_retry(store, d)[1]) == 1
    monkeypatch.setattr(sh, "_signals_module", lambda: (_ for _ in ()).throw(ImportError("boom")))
    assert sh._detector_exempts_now(_signal("report_only", NOTICE)) is None


def test_template_holds_carries_the_recheck_and_to_defect_keeps_it():
    nudges = [{"rule": "report_only", "ask_head": HUMAN, "session_id": f"s{i}", "ts": NOW - i, "ask_fp": f"p{i}"}
              for i in range(3)]
    out = sg.template_holds({}, nudges, NOW)
    assert len(out) == 1 and out[0]["recheck"] == {"kind": "template_hold", "rule": "report_only", "head": HUMAN}
    assert sg.to_defect({**out[0], "host": "h"})["recheck"] == out[0]["recheck"]
    assert sg.recheck_exempts(out[0]["recheck"]) is None
