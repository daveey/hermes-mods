"""Queue drains checkpoint completed work and yield without losing later input."""
import json
from types import SimpleNamespace

import pytest

from test_self_heal_lane import sh


@pytest.fixture
def queue(tmp_path):
    store = SimpleNamespace(root=tmp_path)
    path = tmp_path / "selfheal_queue.jsonl"
    path.write_text("".join(json.dumps({"fingerprint": f"agent-{i}", "component": "agent",
                                      "symptom": "fixture", "ts": i}) + "\n" for i in range(5)))
    return store, path, tmp_path / "selfheal_queue.offset"


def test_completed_prefix_survives_later_filer_exception(queue):
    store, _, offset = queue
    seen = []
    def fail(d, **kwargs):
        seen.append(d["fingerprint"])
        if len(seen) == 3:
            raise RuntimeError("interrupted")
        return {"action": "filed"}
    with pytest.raises(RuntimeError, match="interrupted"):
        sh.drain(store, filer=fail)
    assert offset.read_text() == "2"
    rest = []
    sh.drain(store, filer=lambda d, **kw: rest.append(d["fingerprint"]) or {"action": "filed"})
    assert rest == ["agent-2", "agent-3", "agent-4"]


def test_row_limit_resumes_exact_prefix(queue):
    store, _, offset = queue
    seen = []
    filer = lambda d, **kw: seen.append(d["fingerprint"]) or {"action": "filed"}
    sh.drain(store, filer=filer, limit=2)
    assert offset.read_text() == "2"
    sh.drain(store, filer=filer, limit=2)
    sh.drain(store, filer=filer, limit=2)
    assert seen == [f"agent-{i}" for i in range(5)]
    assert offset.read_text() == "5"


def test_elapsed_budget_yields_after_completed_item(queue, monkeypatch):
    store, _, offset = queue
    clock = [0.0]
    monkeypatch.setattr(sh.time, "monotonic", lambda: clock[0])
    def slow(d, **kw):
        clock[0] += 61
        return {"action": "filed"}
    assert len(sh.drain(store, filer=slow)) == 1
    assert offset.read_text() == "1"


def test_append_during_filing_is_not_acknowledged(queue):
    store, path, offset = queue
    def append(d, **kw):
        with path.open("a") as f:
            f.write(json.dumps({"fingerprint": "later"}) + "\n")
        return {"action": "filed"}
    sh.drain(store, filer=append, limit=1)
    assert offset.read_text() == "1"
    rest = []
    sh.drain(store, filer=lambda d, **kw: rest.append(d["fingerprint"]) or {})
    assert rest == ["agent-1", "agent-2", "agent-3", "agent-4", "later"]


def test_partial_trailing_line_is_retained(queue):
    store, path, offset = queue
    path.write_text('{"fingerprint":"partial"')
    assert sh.drain(store, filer=lambda *a, **k: pytest.fail("unfinished input")) == []
    with path.open("a") as f:
        f.write('}\n')
    seen = []
    sh.drain(store, filer=lambda d, **kw: seen.append(d["fingerprint"]) or {})
    assert seen == ["partial"]
    assert offset.read_text() == "1"


def test_checkpoint_failure_keeps_last_confirmed_prefix(queue, monkeypatch):
    store, _, offset = queue
    replace = sh.os.replace
    calls = []
    def fail_second(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError("checkpoint unavailable")
        return replace(src, dst)
    monkeypatch.setattr(sh.os, "replace", fail_second)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        sh.drain(store, filer=lambda *a, **k: {"action": "filed"})
    assert offset.read_text() == "1"
    monkeypatch.setattr(sh.os, "replace", replace)
    seen = []
    sh.drain(store, filer=lambda d, **kw: seen.append(d["fingerprint"]) or {})
    assert seen == ["agent-1", "agent-2", "agent-3", "agent-4"]


def test_complete_bad_rows_consume_budget_without_losing_next(queue):
    store, path, offset = queue
    path.write_text('bad json\n[]\n{"fingerprint":"good"}\n')
    assert sh.drain(store, filer=lambda *a, **k: pytest.fail("budget exceeded"), limit=2) == []
    assert offset.read_text() == "2"
    seen = []
    sh.drain(store, filer=lambda d, **kw: seen.append(d["fingerprint"]) or {})
    assert seen == ["good"]


def test_reentrant_consumer_cannot_replay_or_regress_cursor(queue):
    store, _, offset = queue
    def nested(d, **kw):
        assert sh.drain(store, filer=lambda *a, **k: pytest.fail("second consumer")) == []
        return {"action": "filed"}
    sh.drain(store, filer=nested, limit=1)
    assert offset.read_text() == "1"
