import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("drift_monitor", Path(__file__).parents[1] / "monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
DIRTY = {"clean": False, "rejected_ops": 1}
CLEAN = {"clean": True}
UNKNOWN = {"clean": False, "unavailable": True}


class Tools:
    def __init__(self):
        self.cards, self.keys, self.calls = {}, {}, []
        self.crash_after_create = False

    def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "kanban_create":
            key = args["idempotency_key"]
            if key not in self.keys:
                tid = "t_fixture_" + str(len(self.cards))
                self.keys[key] = tid
                self.cards[tid] = {"id": tid, "status": "ready"}
            if self.crash_after_create:
                self.crash_after_create = False
                raise RuntimeError("crash after durable create")
            return {"task_id": self.keys[key]}
        if name == "kanban_show":
            return {"task": self.cards[args["task_id"]]}
        assert name == "kanban_comment"
        return {"success": True}


def test_dirty_dirty_dedup_restart_and_changed_evidence(tmp_path):
    tools = Tools()
    path = tmp_path / "state.json"
    m = monitor.Monitor(path, tools)
    assert m.sample("host/default", DIRTY)["card"] is None
    m = monitor.Monitor(path, tools)
    assert m.sample("host/default", DIRTY)["card"]
    m.sample("host/default", DIRTY)
    assert len(tools.cards) == 1
    assert sum(n == "kanban_create" for n, _ in tools.calls) == 1
    m.sample("host/default", {"clean": False, "rejected_ops": 2})
    assert sum(n == "kanban_comment" for n, _ in tools.calls) == 1


@pytest.mark.parametrize("middle", [CLEAN, UNKNOWN])
def test_consecutive_samples_required(tmp_path, middle):
    tools = Tools()
    m = monitor.Monitor(tmp_path / "state.json", tools)
    for sample in (DIRTY, middle, DIRTY):
        m.sample("host/default", sample)
    assert not tools.cards
    m.sample("host/default", DIRTY)
    assert len(tools.cards) == 1


def test_unavailable_preserves_incident_and_closed_card_starts_new(tmp_path):
    tools = Tools()
    path = tmp_path / "state.json"
    m = monitor.Monitor(path, tools)
    m.sample("host/default", DIRTY)
    old = m.sample("host/default", DIRTY)["card"]
    m.sample("host/default", UNKNOWN)
    assert m.state["targets"]["host/default"]["card"] == old
    tools.cards[old]["status"] = "done"
    m = monitor.Monitor(path, tools)
    assert m.sample("host/default", DIRTY)["card"] is None
    new = m.sample("host/default", DIRTY)["card"]
    assert new != old
    assert len(tools.cards) == 2


def test_crash_after_create_retries_same_identity(tmp_path):
    tools = Tools()
    path = tmp_path / "state.json"
    m = monitor.Monitor(path, tools)
    m.sample("host/default", DIRTY)
    tools.crash_after_create = True
    with pytest.raises(RuntimeError):
        m.sample("host/default", DIRTY)
    m = monitor.Monitor(path, tools)
    m.sample("host/default", DIRTY)
    assert len(tools.cards) == 1
    keys = [args["idempotency_key"] for name, args in tools.calls if name == "kanban_create"]
    assert keys[0] == keys[1]


@pytest.mark.parametrize("code, payload, available", [(0, CLEAN, True), (1, DIRTY, True), (2, CLEAN, False),
                                                      (1, CLEAN, False), (0, {}, False)])
def test_command_contract(code, payload, available):
    script = f"import sys; print({json.dumps(payload)!r}); sys.exit({code})"
    report = monitor.collect([sys.executable, "-c", script])
    assert bool(report.get("unavailable")) is (not available)


def test_invalid_output_and_unreachable_are_unavailable():
    assert monitor.collect([sys.executable, "-c", "print('not json')"])["unavailable"]
    assert monitor.collect(["/nonexistent/drift-fixture"])["unavailable"]
