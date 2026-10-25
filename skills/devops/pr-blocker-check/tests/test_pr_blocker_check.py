import importlib.util
import pathlib

p = pathlib.Path(__file__).parents[1] / "scripts" / "pr_blocker_check.py"
spec = importlib.util.spec_from_file_location("pbc", p)
pbc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbc)

OK = [{"name": "t", "status": "COMPLETED", "conclusion": "SUCCESS"}]


def pr(**kw):
    base = dict(state="OPEN", mergeable="MERGEABLE", mergeStateStatus="CLEAN", reviewDecision="APPROVED",
                statusCheckRollup=OK, headRefOid="abc", reviewRequests=[])
    base.update(kw)
    return pbc.classify(base)


def test_merged():
    assert pr(state="MERGED")["action"] == "verify_deployment"


def test_closed():
    assert pr(state="CLOSED")["verdict"] == "closed"


def test_dirty_repairs():
    assert pr(mergeStateStatus="DIRTY", mergeable="CONFLICTING")["action"] == "repair_conflicts"


def test_failed_check_fixes():
    r = pr(statusCheckRollup=[{"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}])
    assert r["action"] == "fix_failing_checks" and "lint" in r["detail"]


def test_pending_retries_not_asks():
    r = pr(statusCheckRollup=[{"name": "bazel", "status": "IN_PROGRESS"}], mergeStateStatus="BLOCKED")
    assert r["action"] == "schedule_retry"


def test_status_context_failure():
    r = pr(statusCheckRollup=[{"context": "ci", "state": "ERROR"}])
    assert r["verdict"] == "checks_failed"


def test_review_required_is_named_not_generic_blocked():
    r = pr(mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED")
    assert r["verdict"] == "review_required" and "admin" in r["detail"]


def test_bare_blocked_diagnoses():
    r = pr(mergeStateStatus="BLOCKED", reviewDecision="APPROVED")
    assert r["action"] == "diagnose"


def test_approved_green_attempts_normal_merge():
    r = pr()
    assert r["action"] == "attempt_normal_merge" and "not --admin" in r["detail"]


def test_never_emits_ask_user():
    for mss in ("BLOCKED", "UNKNOWN", "CLEAN", "BEHIND"):
        assert pr(mergeStateStatus=mss)["action"] != "ask_user"
