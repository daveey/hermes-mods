"""Strict acceptance metrics are independent of admission's consecutive mute threshold."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("strict_audit", HERE / "junk_audit.py")
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
NOW = 2_000_000.0


def item(**kw):
    return dict(dict(gw="example", session_id="one", poster="x-post", key="draft:one",
                     title="Publish this draft?", state="archived", outcome="archived",
                     created_at=NOW - 200, resolved_at=NOW - 100), **kw)


def test_single_archive_and_accepted_success_do_not_cancel_strict_failure():
    res = audit.audit([item(), item(session_id="two", state="accepted", outcome="post", created_at=NOW - 50)], NOW)
    assert res["senders"] == []
    assert res["strict_counts"]["archived_untouched"] == 1
    assert res["strict_counts"]["junk_items"] == 1
    assert res["strict_findings"][0]["fresh"] is True


def test_delayed_archive_includes_old_creation_and_uses_decision_time():
    res = audit.audit([item(created_at=NOW - 30 * 86400)], NOW)
    assert res["window_items"] == 0
    assert res["strict_counts"]["archived_untouched"] == 1
    assert res["fresh"] is True
    assert res["observation"]["decision_since"] == NOW - audit.WINDOW_S


def test_recent_creation_cannot_make_old_archive_fresh():
    res = audit.audit([item(created_at=NOW - 90000, resolved_at=NOW - 87000)], NOW)
    assert res["strict_counts"]["junk_items"] == 1
    assert res["strict_findings"][0]["fresh"] is False


def test_missing_archive_timestamp_is_reported_not_guessed():
    res = audit.audit([item(resolved_at=None, updated_at=NOW - 10)], NOW)
    assert res["strict_counts"]["archived_untouched"] == 1
    assert res["strict_findings"][0]["fresh"] is False
    assert res["observation"]["archive_time_unknown"] == 1


def test_test_status_and_unregistered_are_independent_and_count_union():
    rows = [item(state="open", outcome=None, title="Fixed: work complete", poster="cron"),
            item(session_id="two", state="open", title="TEST: fixture", poster="unknown-family")]
    res = audit.audit(rows, NOW)
    assert res["strict_counts"] == dict(archived_untouched=0, test_cards=1, status_cards=1,
                                        unregistered_posters=1, junk_items=2)


@pytest.mark.parametrize("poster,title,actions", [
    ("x-post", 'Reply to "The build failed": publish?', [{"kind": "resolve"}]),
    ("cron", "Approve a higher spending cap?", [{"kind": "resolve"}]),
    ("cli", "Sign in to continue", []),
])
def test_legitimate_open_asks_are_not_junk(poster, title, actions):
    res = audit.audit([item(state="open", outcome=None, poster=poster, title=title, actions=actions)], NOW)
    assert res["strict_counts"]["junk_items"] == 0


def test_repeated_audit_across_midnight_reuses_family_owner():
    from audit_fixtures import BoardRunner
    runner = BoardRunner()
    midnight = 24 * 86400
    rows = [item(created_at=midnight - 200, resolved_at=midnight - 100)]
    for now in (midnight - 1, midnight + 1):
        assert audit.file_cards(audit.audit(rows, now), now, runner) == {"x-post": "t_1"}
    assert len(runner.tasks) == 1
    assert runner.comments["t_1"] == []


@pytest.mark.parametrize("closed_status", ["done", "archived"])
def test_owner_gets_new_evidence_once_and_successor_after_done(closed_status):
    from audit_fixtures import BoardRunner
    runner = BoardRunner()
    rows = [item()]
    audit.file_cards(audit.audit(rows, NOW), NOW, runner)
    rows.append(item(session_id="two", key="draft:two", resolved_at=NOW - 50))
    for _ in range(2):
        audit.file_cards(audit.audit(rows, NOW), NOW, runner)
    assert len(runner.tasks) == 1 and len(runner.comments["t_1"]) == 1
    runner.tasks["t_1"]["status"] = closed_status
    audit.file_cards(audit.audit(rows, NOW), NOW, runner)
    assert len(runner.tasks) == 1  # already observed evidence is not a new defect
    rows.append(item(session_id="three", key="draft:three", resolved_at=NOW - 10))
    for _ in range(2):
        assert audit.file_cards(audit.audit(rows, NOW), NOW, runner) == {"x-post": "t_2"}
    assert len(runner.tasks) == 2


def test_missing_comment_readback_fails():
    from audit_fixtures import BoardRunner
    runner = BoardRunner()
    audit.file_cards(audit.audit([item()], NOW), NOW, runner)
    runner.drop_comments = True
    with pytest.raises(RuntimeError, match="readback"):
        audit.file_cards(audit.audit([item(session_id="new")], NOW), NOW, runner)


@pytest.mark.parametrize("response", [SimpleNamespace(returncode=1, stdout="", stderr="refused"),
                                      SimpleNamespace(returncode=0, stdout="{}", stderr=""),
                                      SimpleNamespace(returncode=0, stdout="not json", stderr="")])
def test_filing_failure_is_not_success(response):
    def runner(argv, **kw):
        return SimpleNamespace(returncode=0, stdout="[]", stderr="") if argv[2] == "list" else response
    with pytest.raises(RuntimeError, match="x-post"):
        audit.file_cards(audit.audit([item()], NOW), NOW, runner)


def test_dry_run_emits_strict_counts_without_writes(tmp_path, monkeypatch, capsys):
    source = tmp_path / "items.json"
    source.write_text(json.dumps([item()]))
    monkeypatch.setattr(audit.time, "time", lambda: NOW)
    monkeypatch.setattr(audit, "file_cards", lambda *a: pytest.fail("dry run filed"))
    monkeypatch.setattr(audit, "drain_refusals", lambda *a: pytest.fail("dry run drained"))
    assert audit.main(["--json", str(source), "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out.splitlines()[0])
    assert output["strict_counts"]["junk_items"] == 1


def test_main_reports_filing_failure_as_nonzero(tmp_path, monkeypatch, capsys):
    source = tmp_path / "items.json"
    source.write_text(json.dumps([item()]))
    monkeypatch.setattr(audit.time, "time", lambda: NOW)
    monkeypatch.setattr(audit, "drain_refusals", lambda *a: 0)
    def fail(*a):
        raise RuntimeError("x-post: refused")
    monkeypatch.setattr(audit, "file_cards", fail)
    assert audit.main(["--json", str(source)]) == 1
    assert "x-post" in json.loads(capsys.readouterr().out.splitlines()[0])["filing_error"]


def test_refusal_drain_failure_is_visible(tmp_path, monkeypatch, capsys):
    log = tmp_path / "inbox" / "refusals.jsonl"
    log.parent.mkdir()
    log.write_text(json.dumps(dict(family="cron", key="one", title="notice", routing_error="old error")) + "\n")
    def fail(**kw):
        raise RuntimeError("board unavailable")
    with pytest.raises(RuntimeError, match="refusal drain failed"):
        audit.drain_refusals(tmp_path, fail)
    assert "routing_error" in json.loads(log.read_text())
    source = tmp_path / "empty.json"
    source.write_text("[]")
    monkeypatch.setattr(audit, "drain_refusals", lambda home: audit_failure())
    def audit_failure():
        raise RuntimeError("refusal drain failed")
    assert audit.main(["--json", str(source)]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[0])["drain_error"]
