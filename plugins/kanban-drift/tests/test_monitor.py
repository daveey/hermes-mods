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
# Core-backed tests (hermes_cli kanban tools, owner identity) skip only when core is absent
# (plugin-suites CI has no core checkout); with core they run, and a broken core still fails.
requires_core = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None,
                                   reason="Hermes core (hermes_cli) not importable: core-backed test")


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


@requires_core
def test_native_tools_create_readback_and_restart_dedup(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    # The native tool's canonical path, not just HERMES_HOME, must be isolated.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("platform_toolsets:\n  cli: [terminal, file, kanban]\nterminal:\n  backend: local\n")
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    # Gateway identity too: on the hub, placement='local' is a hub slot that needs a live admission
    # judge, so the host's own hermes-mail/hub identity must not leak into this create path.
    (tmp_path / "mail.json").write_text(json.dumps({"gateway": "fixture-gw"}))
    monkeypatch.setenv("HERMES_MAIL_CONFIG", str(tmp_path / "mail.json"))
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "hub"))
    path = kb.kanban_db_path()
    assert path.is_relative_to(tmp_path)
    from agent.delegation_context import kanban_path_is_fenced
    assert not kanban_path_is_fenced(path)
    conn = kbc.connect(path)
    conn.close()
    state = tmp_path / "drift.json"
    m = monitor.Monitor(state, monitor.native_tool)
    assert m.sample("fixture/default", DIRTY)["card"] is None
    card = m.sample("fixture/default", DIRTY)["card"]
    result = monitor.native_tool("kanban_show", {"task_id": card, "board": "default"})
    assert result["task"]["title"] == "Repair persistent Kanban sync drift: fixture/default"
    assert monitor.Monitor(state, monitor.native_tool).sample("fixture/default", DIRTY)["card"] == card
    conn = kbc.connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    finally:
        conn.close()


@requires_core
def test_main_owner_guard_before_state_write(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from hermes_cli import kanban_sync_client as client
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"owner_gateway": "owner", "state_path": str(state), "targets": {}}))
    monkeypatch.setattr(sys, "argv", ["monitor", "--config", str(config)])
    monkeypatch.setattr(client, "load_identity", lambda: SimpleNamespace(gateway="other"))
    with pytest.raises(RuntimeError, match="owner gateway"):
        monitor.main()
    assert not state.exists()


def test_malformed_command_preserves_exit_diagnostic():
    report = monitor.collect([sys.executable, "-c", "import sys; print('fixture refusal', file=sys.stderr); sys.exit(23)"])
    assert report["unavailable"]
    assert report["exit_code"] == 23
    assert report["stderr"] == "fixture refusal\n"


def test_invalid_output_and_unreachable_are_unavailable():
    assert monitor.collect([sys.executable, "-c", "print('not json')"])["unavailable"]
    assert monitor.collect(["/nonexistent/drift-fixture"])["unavailable"]


def _run_main(tmp_path, monkeypatch, targets, now, tools=None, **extra):
    from types import SimpleNamespace
    from hermes_cli import kanban_sync_client as client
    config = tmp_path / "config.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"owner_gateway": "owner", "state_path": str(state), "targets": targets, **extra}))
    monkeypatch.setattr(sys, "argv", ["monitor", "--config", str(config)])
    monkeypatch.setattr(client, "load_identity", lambda: SimpleNamespace(gateway="owner"))
    monkeypatch.setattr(monitor, "native_tool", tools or Tools())
    import time as real_time
    from types import SimpleNamespace as NS
    monkeypatch.setattr(monitor, "time", NS(**{**vars(real_time), "time": lambda: now}))
    code = monitor.main()
    return code, json.loads(state.read_text())


def _emit(payload, code=0):
    return [sys.executable, "-c", f"import sys; print({json.dumps(payload)!r}); sys.exit({code})"]


ASLEEP = ["/nonexistent/drift-fixture-asleep"]


@requires_core
def test_main_unavailable_target_within_threshold_exits_zero(tmp_path, monkeypatch, capsys):
    code, state = _run_main(tmp_path, monkeypatch, {"hub/default": _emit(CLEAN), "laptop/default": ASLEEP}, 1000.0)
    assert code == 0
    assert state["targets"]["laptop/default"]["unavailable_since"] == 1000.0
    assert "unavailable_since" not in state["targets"]["hub/default"] or state["targets"]["hub/default"]["unavailable_since"] is None
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["stale_targets"] == []
    # A second dark sample keeps the first-seen time; still under the default 6 h threshold.
    code, state = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 1000.0 + 21600)
    assert code == 0
    assert state["targets"]["laptop/default"]["unavailable_since"] == 1000.0


@requires_core
def test_main_unavailable_past_threshold_exits_one_and_lists_stale(tmp_path, monkeypatch, capsys):
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 1000.0)
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 1000.0 + 21601)
    assert code == 1
    assert json.loads(capsys.readouterr().out)["stale_targets"] == ["laptop/default"]


@requires_core
def test_main_per_target_threshold_map(tmp_path, monkeypatch, capsys):
    targets = {"laptop/default": ASLEEP, "server/default": ASLEEP}
    limits = {"unavailable_alert_after": {"laptop/default": 43200}}
    _run_main(tmp_path, monkeypatch, targets, 0.0, **limits)
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, targets, 30000.0, **limits)
    assert code == 1
    assert json.loads(capsys.readouterr().out)["stale_targets"] == ["server/default"]
    code, _ = _run_main(tmp_path, monkeypatch, targets, 43201.0, **limits)
    assert json.loads(capsys.readouterr().out)["stale_targets"] == ["laptop/default", "server/default"]


@requires_core
@pytest.mark.parametrize("payload, code", [(CLEAN, 0), (DIRTY, 1)])
def test_main_real_sample_clears_unavailable_since(tmp_path, monkeypatch, payload, code):
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 1000.0)
    rc, state = _run_main(tmp_path, monkeypatch, {"laptop/default": _emit(payload, code)}, 2000.0)
    assert rc == 0
    assert state["targets"]["laptop/default"]["unavailable_since"] is None
    _, state = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 99999.0)
    assert state["targets"]["laptop/default"]["unavailable_since"] == 99999.0


@requires_core
def test_main_consumer_error_still_exits_one(tmp_path, monkeypatch):
    def broken(name, args):
        raise RuntimeError("fixture tool failure")
    tools = Tools()
    _run_main(tmp_path, monkeypatch, {"host/default": _emit(DIRTY, 1)}, 1.0, tools=tools)
    code, _ = _run_main(tmp_path, monkeypatch, {"host/default": _emit(DIRTY, 1)}, 2.0, tools=broken)
    assert code == 1


# Presence: tell "host is off" (expected, e.g. a closed laptop) from "host is up but sampling fails".
HOST_DOWN = [sys.executable, "-c", "import sys; sys.exit(1)"]
HOST_UP = [sys.executable, "-c", "import sys; sys.exit(0)"]
H14 = 14 * 3600.0


@requires_core
def test_main_absent_host_14h_exits_zero_and_lists_absent(tmp_path, monkeypatch, capsys):
    """Replays cron-0d63c0cf26: laptop closed 14 h, presence probe says down -> not a failure."""
    extra = {"presence": {"laptop/default": HOST_DOWN}, "unavailable_alert_after": {"laptop/default": 43200}}
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 0.0, **extra)
    capsys.readouterr()
    code, state = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, H14, **extra)
    receipt = json.loads(capsys.readouterr().out)
    assert code == 0
    assert receipt["stale_targets"] == []
    assert receipt["absent_targets"] == ["laptop/default"]
    assert state["targets"]["laptop/default"]["unavailable_since"] == 0.0


@requires_core
def test_main_sample_failing_while_host_up_still_exits_one(tmp_path, monkeypatch, capsys):
    """Detection guard: the real blind spot (host reachable, sampling broken) stays loud."""
    extra = {"presence": {"laptop/default": HOST_UP}, "unavailable_alert_after": {"laptop/default": 43200}}
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 0.0, **extra)
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, H14, **extra)
    receipt = json.loads(capsys.readouterr().out)
    assert code == 1
    assert receipt["stale_targets"] == ["laptop/default"]
    assert receipt["stale_reasons"] == {"laptop/default": "sample_failing_while_up"}
    assert receipt["absent_targets"] == []


@requires_core
def test_main_absent_longer_than_absent_alert_after_exits_one(tmp_path, monkeypatch, capsys):
    extra = {"presence": {"laptop/default": HOST_DOWN}}
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 0.0, **extra)
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 6 * 86400.0, **extra)
    assert code == 0
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 7 * 86400.0 + 1, **extra)
    receipt = json.loads(capsys.readouterr().out)
    assert code == 1
    assert receipt["stale_targets"] == ["laptop/default"]
    assert receipt["stale_reasons"] == {"laptop/default": "absent_too_long"}
    assert receipt["absent_targets"] == ["laptop/default"]


@requires_core
def test_main_without_presence_keeps_elapsed_only_behaviour(tmp_path, monkeypatch, capsys):
    extra = {"unavailable_alert_after": {"laptop/default": 43200}}
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 0.0, **extra)
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, H14, **extra)
    receipt = json.loads(capsys.readouterr().out)
    assert code == 1
    assert receipt["stale_targets"] == ["laptop/default"]
    assert receipt["stale_reasons"] == {"laptop/default": "unavailable_too_long"}
    assert receipt["absent_targets"] == []


@requires_core
@pytest.mark.parametrize("probe", [["/nonexistent/presence-fixture"],
                                   [sys.executable, "-c", "import time; time.sleep(30)"]])
def test_main_presence_probe_error_counts_as_up(tmp_path, monkeypatch, capsys, probe):
    extra = {"presence": {"laptop/default": probe}, "presence_timeout": 1,
             "unavailable_alert_after": {"laptop/default": 43200}}
    _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, 0.0, **extra)
    capsys.readouterr()
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": ASLEEP}, H14, **extra)
    receipt = json.loads(capsys.readouterr().out)
    assert code == 1
    assert receipt["stale_reasons"] == {"laptop/default": "sample_failing_while_up"}
    assert receipt["absent_targets"] == []


@requires_core
def test_main_presence_not_probed_for_available_target(tmp_path, monkeypatch, capsys):
    marker = tmp_path / "probed"
    probe = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    code, _ = _run_main(tmp_path, monkeypatch, {"laptop/default": _emit(CLEAN)}, 0.0,
                        presence={"laptop/default": probe})
    assert code == 0
    assert not marker.exists()
    assert json.loads(capsys.readouterr().out)["absent_targets"] == []


# Composed-hook regression (defect cron-0d63c0cf26): in production native_tool reads the model-facing result,
# after transform_tool_result hooks ran. skill-triggers decorates kanban_create, so a repair filing must still
# parse, read back and dedupe with that hook in the path, and a refusal must stay an explicit error.
def _skill_triggers(home, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "skill_triggers_for_drift", Path(__file__).parents[2] / "skill-triggers" / "__init__.py")
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    skill = home / "skills" / "devops" / "kanban-orchestrator"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("**Card bodies for this user**: spec.\n\n**Card mechanics for this user**: x.\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(st, "_skill_dirs", lambda: [home / "skills"])
    monkeypatch.setattr(st, "_viewed_in_db", lambda s, k: False)
    return st


def _hooked_model_tools(monkeypatch, tools, st):
    import types
    fake = types.ModuleType("model_tools")
    hooked = []

    def handle_function_call(name, args, **_):
        raw = json.dumps(tools(name, args))
        out = st.transform_tool_result(tool_name=name, args=args, result=raw, session_id="")
        hooked.append(out is not None)
        return out if out is not None else raw

    fake.handle_function_call = handle_function_call
    monkeypatch.setitem(sys.modules, "model_tools", fake)
    return hooked


def test_composed_skill_triggers_hook_create_readback_restart_dedup(tmp_path, monkeypatch):
    st = _skill_triggers(tmp_path / "home", monkeypatch)
    tools = Tools()
    hooked = _hooked_model_tools(monkeypatch, tools, st)
    state = tmp_path / "drift.json"
    m = monitor.Monitor(state, monitor.native_tool)
    assert m.sample("worker/default", DIRTY)["card"] is None
    card = m.sample("worker/default", DIRTY)["card"]
    assert card == "t_fixture_0" and any(hooked)  # the hook really decorated the create result
    assert monitor.Monitor(state, monitor.native_tool).sample("worker/default", DIRTY)["card"] == card
    assert len(tools.cards) == 1 and sum(n == "kanban_create" for n, _ in tools.calls) == 1


def test_composed_skill_triggers_hook_refusal_stays_explicit(tmp_path, monkeypatch):
    st = _skill_triggers(tmp_path / "home", monkeypatch)

    def refuse(name, args):
        return {"error": "placement_unavailable: target not live", "success": False}

    _hooked_model_tools(monkeypatch, refuse, st)
    m = monitor.Monitor(tmp_path / "drift.json", monitor.native_tool)
    m.sample("worker/default", DIRTY)
    with pytest.raises(RuntimeError, match="placement_unavailable"):
        m.sample("worker/default", DIRTY)
    row = m.state["targets"]["worker/default"]
    assert row["card"] is None and row["incident"]  # identity kept for a safe retry, no phantom card
