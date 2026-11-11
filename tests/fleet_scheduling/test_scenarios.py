"""Real-core fleet scenarios. A scenario emits proof only after all its assertions.

Every claimed behaviour is exercised against the real core code through its
real entry path (CLI dispatcher + fake-provider workers, or fresh interpreters
for the remote notice paths). Each oracle is also run against a targeted
*implementation* mutation (a fixture-owned import hook patches the real core
function in the child interpreter, never core on disk) and must reject it.

Parent-runner contracts introduced here:

* ``$FLEET_FIXTURE_ROOT/fleet-cgroups.json`` -- ``{"version": 1, "groups": [abs cgroup path, ...]}``.
  Published atomically BEFORE any cgroup is created. The parent reaps each path
  that still exists (kill only identity-registered fixture processes, wait for an
  empty ``cgroup.procs``, ``rmdir``); a missing path is already clean.
* A proof JSON may carry a top-level ``"result": "UNVERIFIED"`` (with typed
  ``unverified`` reasons); the parent must record that instead of PASS.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest


def publish(name, root, fault, transitions, assertions, result=None, unverified=None):
    proof = {"fixture_root": str(root), "injected_fault": fault,
             "transitions": transitions, "assertions": assertions}
    if result is not None:
        proof["result"] = result
    if unverified:
        proof["unverified"] = unverified
    (Path(os.environ["FLEET_OUTPUT"]) / f"{name}.proof.json").write_text(
        json.dumps(proof, indent=2, default=str), encoding="utf-8")


class Cleanup:
    """Run every cleanup step even when an earlier one (or a diagnostic write) fails."""

    def __init__(self):
        self.errors = []

    def step(self, label, fn, *args):
        try:
            return fn(*args)
        except BaseException as exc:  # noqa: BLE001 - cleanup must continue; reported in finish()
            self.errors.append(f"{label}: {type(exc).__name__}: {exc}")

    def finish(self):
        if not self.errors:
            return
        message = "cleanup failures: " + "; ".join(self.errors)
        if sys.exc_info()[0] is None:
            raise AssertionError(message)
        # Do not mask the in-flight failure, but never lose the cleanup evidence.
        with contextlib.suppress(OSError):
            with open(Path(os.environ["FLEET_OUTPUT"]) / "cleanup-errors.txt", "a", encoding="utf-8") as handle:
                handle.write(message + "\n")


def write_diagnostics(board, tid, prefix):
    out = Path(os.environ["FLEET_OUTPUT"])
    (out / f"{prefix}.diagnostics.txt").write_text(board.diag(tid), encoding="utf-8")
    logs = {str(path.relative_to(board.root)): path.read_text(encoding="utf-8", errors="replace")
            for path in board.root.rglob("*.log")}
    (out / f"{prefix}.logs.json").write_text(json.dumps(logs, indent=2), encoding="utf-8")


def dump_requests(server, prefix):
    """Evaluated lazily inside Cleanup.step so a diagnostic error cannot skip later cleanup."""
    rows = [(record.get("body") or record).get("messages", [])[-3:] for record in server.main_requests()]
    rows.append({"tools": sorted(tool.get("function", {}).get("name", "?") for tool in
                                 ((server.main_requests() or [{}])[0].get("body") or (server.main_requests() or [{}])[0]).get("tools", []))})
    (Path(os.environ["FLEET_OUTPUT"]) / f"{prefix}.requests.json").write_text(
        json.dumps(rows, indent=1, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture-owned implementation hooks (import-time patches in child interpreters)
# ---------------------------------------------------------------------------

HOOK_SITE = r'''
import importlib.abc, importlib.util, json, os, sys, threading, time

_HOOKS = {}
_ON = [h for h in os.environ.get("FLEET_HOOKS", "").split(",") if h]


def _hook(module):
    def wrap(fn):
        _HOOKS.setdefault(module, []).append(fn)
        return fn
    return wrap


class _Loader(importlib.abc.Loader):
    def __init__(self, inner, name):
        self._inner, self._name = inner, name

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        for fn in _HOOKS.get(self._name, ()):
            fn(module)

    def __getattr__(self, item):
        return getattr(self._inner, item)


class _Finder(importlib.abc.MetaPathFinder):
    busy = set()

    def find_spec(self, name, path=None, target=None):
        if name not in _HOOKS or name in self.busy:
            return None
        self.busy.add(name)
        try:
            spec = importlib.util.find_spec(name)
        finally:
            self.busy.discard(name)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader, name)
        return spec


if "lost_retry" in _ON:
    @_hook("hermes_cli.kanban_db_dispatch_exit_code")
    def _lost_retry(module):
        real = module.exit_code_kind
        def mutated(code):
            kind, value = real(code)
            return ("nonzero_exit", value) if kind == "rate_limited" else (kind, value)
        module.exit_code_kind = mutated

if "early_dispatch" in _ON:
    @_hook("hermes_cli.kanban_db_dispatch")
    def _early(module):
        module.check_respawn_guard = lambda *args, **kwargs: None

if "false_human" in _ON:
    @_hook("hermes_cli.kanban_db_dispatch")
    def _human(module):
        real = module.check_respawn_guard
        def mutated(conn, task_id, **kwargs):
            guard = real(conn, task_id, **kwargs)
            if guard in ("rate_limit_cooldown", "subscription_quota_reset"):
                request = {"action": "Fabricated: approve provider cooldown?", "why_human": "fabricated by mutation",
                           "interaction": "approval", "scope": "fixture", "evidence": "mutation"}
                conn.execute("UPDATE tasks SET human_request=?, block_kind='needs_input' WHERE id=?",
                             (json.dumps(request), task_id))
                conn.commit()
            return guard
        module.check_respawn_guard = mutated

if "oom_join" in _ON:
    # The ONLY process that joins the memory-limited group is the bound executor itself:
    # it writes its own pid, after the fixture touches the trigger. No foreign numeric
    # PID is ever migrated by anyone.
    @_hook("gateway.session_kanban")
    def _oom(module):
        real = module.bind_worker_context
        def bound(frame):
            result = real(frame)
            if not (frame.get("policy") or {}).get("kanban_json"):
                return result
            def join():
                trigger, group, joined = (os.environ[k] for k in ("FLEET_OOM_TRIGGER", "FLEET_OOM_GROUP", "FLEET_OOM_JOINED"))
                deadline = time.monotonic() + 120
                while not os.path.exists(trigger):
                    if time.monotonic() > deadline:
                        return
                    time.sleep(0.02)
                me = os.getpid()
                with open(os.path.join(group, "cgroup.procs"), "w") as handle:
                    handle.write(str(me))
                stat = open(f"/proc/{me}/stat").read().rsplit(")", 1)[1].split()
                record = {"pid": me, "start_identity": stat[19], "cgroup": open("/proc/self/cgroup").read().strip()}
                with open(joined + ".tmp", "w") as handle:
                    json.dump(record, handle)
                os.replace(joined + ".tmp", joined)
            threading.Thread(target=join, daemon=True, name="fleet-oom-self-join").start()
            return result
        module.bind_worker_context = bound

if _HOOKS:
    sys.meta_path.insert(0, _Finder())
'''


def hook_env(fleet_root, core, hooks, board_dir=None, **extra):
    """env_extra for a Board: a fixture-owned sitecustomize dir ahead of core on PYTHONPATH."""
    directory = Path(fleet_root) / "hooks" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    (directory / "sitecustomize.py").write_text(HOOK_SITE, encoding="utf-8")
    env = {"FLEET_HOOKS": ",".join(hooks), "PYTHONPATH": f"{directory}{os.pathsep}{core}"}
    env.update({key: str(value) for key, value in extra.items()})
    return env


def rewrite_launch_wrapper(board, hook_dir, core):
    """Explicit launch wrapper: the owner/executor tree is started through HERMES_BIN."""
    board.hermes_bin.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={shlex.quote(str(hook_dir))}:{shlex.quote(str(core))} exec {shlex.quote(sys.executable)} "
        '-m hermes_cli.main "$@"\n', encoding="utf-8")
    board.hermes_bin.chmod(0o755)


HUMAN_KINDS = {"blocked", "needs_input", "human_request", "human_requested", "awaiting_human", "auto_blocked"}


def assert_no_false_human(board, tid, allowed=False):
    """The whole persisted history -- task row, every run and every event -- carries no human blocker."""
    if allowed:
        return
    task = board.task(tid)
    assert task["human_request"] is None, f"false human blocker on task row: {task['human_request']}"
    assert task["block_kind"] not in ("needs_input", "capability"), f"false human wait kind: {task['block_kind']}"
    for run in board.runs(tid):
        assert run["outcome"] not in ("blocked", "needs_input"), f"false human blocker run: {run['outcome']}"
    for event in board.events(tid):
        payload = event["payload"]
        text = json.dumps(payload, default=str)
        assert event["kind"] not in HUMAN_KINDS, f"false human blocker event {event['kind']}: {text[:200]}"
        if isinstance(payload, dict):
            assert not payload.get("human_request"), f"false human blocker payload in {event['kind']}"
            assert payload.get("interaction") != "approval", f"false human approval payload in {event['kind']}"


# ---------------------------------------------------------------------------
# cgroup OOM support
# ---------------------------------------------------------------------------

def cgroup_parent():
    text = Path("/proc/self/cgroup").read_text(encoding="utf-8").strip()
    if "::" not in text:
        raise OSError("cgroup v2 unified hierarchy not visible")
    return (Path("/sys/fs/cgroup") / text.split("::", 1)[1].lstrip("/")).parent


def oom_capability(parent=None):
    """None when a bounded memory cgroup can be created; else a typed UNVERIFIED reason."""
    try:
        parent = Path(parent) if parent is not None else cgroup_parent()
        controllers = (parent / "cgroup.subtree_control").read_text(encoding="utf-8").split()
    except OSError as exc:
        return f"cgroup_v2_unreadable: {exc}"
    if "memory" not in controllers:
        return "memory_controller_not_delegated"
    if not os.access(parent, os.W_OK | os.X_OK):
        return "cgroup_parent_not_writable"
    return None


def register_cgroup(root, path):
    """Publish BEFORE creating the group so the parent runner can always reap it."""
    registry = Path(root) / "fleet-cgroups.json"
    data = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {"version": 1, "groups": []}
    data["groups"].append(str(path))
    temp = registry.with_name(registry.name + ".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temp, registry)


def unregister_cgroup(root, path):
    registry = Path(root) / "fleet-cgroups.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["groups"] = [item for item in data["groups"] if item != str(path)]
    temp = registry.with_name(registry.name + ".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temp, registry)


def release_cgroup(board, root, group):
    """Reap only identity-registered fixture processes, then remove and unpublish the group."""
    if not group.exists():
        unregister_cgroup(root, group)
        return
    for entry in (group / "cgroup.procs").read_text(encoding="utf-8").split():
        pid = int(entry)
        if pid not in board.registry.births:  # fixture-tagged (environ + birth checked) or left alone
            with contextlib.suppress(OSError, PermissionError, KeyError):
                board.registry.register(pid)
        if pid in board.registry.births:
            with contextlib.suppress(FileNotFoundError, ProcessLookupError):
                board.registry.send(pid, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while (group / "cgroup.procs").read_text(encoding="utf-8").split() and time.monotonic() < deadline:
        time.sleep(0.05)
    remaining = (group / "cgroup.procs").read_text(encoding="utf-8").split()
    assert not remaining, f"fixture cgroup still populated: {remaining}"
    for attempt in range(200):  # a just-killed task's cgroup can stay busy briefly
        try:
            group.rmdir()
            break
        except OSError:
            if attempt == 199 or not group.exists():
                raise
            time.sleep(0.05)
    unregister_cgroup(root, group)


def test_crash_oom_capability_is_typed_unverified(tmp_path):
    """A host without delegated memory control is UNVERIFIED with a typed reason, never SIGKILL-as-OOM."""
    assert oom_capability(tmp_path / "missing") .startswith("cgroup_v2_unreadable")
    (tmp_path / "cgroup.subtree_control").write_text("pids\n", encoding="utf-8")
    assert oom_capability(tmp_path) == "memory_controller_not_delegated"
    (tmp_path / "cgroup.subtree_control").write_text("memory pids\n", encoding="utf-8")
    assert oom_capability(tmp_path) in (None, "cgroup_parent_not_writable")


def test_update_real_persisted_continuation(fleet_root, containment, board_factory, monkeypatch):
    import importlib.util

    adapter = Path(os.environ["FLEET_SYNC_SOURCE"]) / "tests/test_fleet_update_resume.py"
    assert adapter.is_file(), "required private update adapter absent"
    spec = importlib.util.spec_from_file_location("fleet_private_update", adapter)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run_update_resume(fleet_root, containment, board_factory, monkeypatch)


# ---------------------------------------------------------------------------
# crash / OOM
# ---------------------------------------------------------------------------

def test_crash_and_cgroup_oom_recover_original_work(fleet_root, containment, board_factory):
    from tests.e2e.core.kanban._helpers import pid_alive, wait_until
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall
    from conftest import identity

    core = Path(os.environ["FLEET_CORE_SOURCE"])
    observations, assertions, unverified = [], [], []
    faults = []
    capability = oom_capability()
    for variant in ("sigkill", "oom"):
        if variant == "oom" and capability is not None:
            unverified.append({"variant": "oom", "reason": capability})
            assertions.append({"name": "cgroup_oom_kernel_kill", "result": "UNVERIFIED", "evidence": capability})
            continue
        ready, release = threading.Event(), threading.Event()
        recovering = [False]
        oom_responses = [0]

        def model(record):
            messages = record["body"]["messages"]
            if recovering[0]:
                return Text("fixture closed") if messages[-1].get("role") == "tool" else ToolCall(
                    "kanban_complete", {"summary": "fixture recovered exactly once"})
            if messages[-1].get("role") != "tool":
                return ToolCall("kanban_heartbeat", {"note": "fixture first attempt about to inject " + variant})
            ready.set()
            assert release.wait(45), "fault injector never released fake provider"
            if variant == "sigkill":
                return Text("fixture killed request released")
            # One bounded response pressures fresh HTTP parsing allocations in the
            # 32 MiB/no-swap executor cgroup. No terminal approval is bypassed.
            assert oom_responses[0] == 0, "OOM injection must not repeat"
            oom_responses[0] += 1
            return Text("x" * (64 * 1024 * 1024), chunk_chars=64 * 1024 * 1024)

        group = None
        env_extra = {"HERMES_KANBAN_CRASH_GRACE_SECONDS": "0"}
        trigger = fleet_root / ("oom-trigger-" + uuid.uuid4().hex)
        joined = fleet_root / ("oom-joined-" + uuid.uuid4().hex + ".json")
        if variant == "oom":
            group = cgroup_parent() / ("fleet-oom-" + uuid.uuid4().hex)
            register_cgroup(fleet_root, group)  # parent-reapable before the group exists
            try:
                group.mkdir()
                (group / "memory.max").write_text(str(32 * 1024 * 1024), encoding="utf-8")
                (group / "memory.swap.max").write_text("0", encoding="utf-8")
                (group / "memory.oom.group").write_text("1", encoding="utf-8")
            except OSError as exc:
                reason = f"cgroup_setup_denied: {exc}"
                if group.exists():
                    group.rmdir()
                unregister_cgroup(fleet_root, group)
                unverified.append({"variant": "oom", "reason": reason})
                assertions.append({"name": "cgroup_oom_kernel_kill", "result": "UNVERIFIED", "evidence": reason})
                continue
            assert (group / "memory.max").read_text(encoding="utf-8").strip() == str(32 * 1024 * 1024)
            assert not (group / "cgroup.procs").read_text(encoding="utf-8").strip()
            env_extra.update(hook_env(fleet_root, core, ["oom_join"], FLEET_OOM_GROUP=group,
                                      FLEET_OOM_TRIGGER=trigger, FLEET_OOM_JOINED=joined))
        cleanup = Cleanup()
        tid = None
        board = None
        try:
            with FakeLLMServer(model) as server:
                board = board_factory("crash-" + variant, server.base_url, env_extra=env_extra)
                if variant == "oom":
                    rewrite_launch_wrapper(board, Path(env_extra["PYTHONPATH"].split(os.pathsep)[0]), core)
                tid = board.create("fixture " + variant + " recovery", "--no-requires")
                board.dispatch()
                assert ready.wait(45), board.diag(tid)
                task = board.task(tid)
                submitter = int(task["worker_pid"])
                bound = board.events(tid, "worker_bound")[-1]
                assert bound["run_id"] == task["current_run_id"]
                pid = int(bound["payload"]["pid"])
                assert pid != submitter, "fault target must be native executor, not submitting client"
                board.registry.register(pid)
                birth = identity(pid)
                attempt = task["current_run_id"]
                assert task["last_heartbeat_at"] and task["human_request"] is None
                if variant == "sigkill":
                    board.registry.send(pid, signal.SIGKILL)
                    release.set()
                    fault = {"signal": "SIGKILL", "pid": pid, "start_identity": birth}
                else:
                    before = (group / "memory.events.local").read_text(encoding="utf-8")
                    trigger.write_text("join", encoding="utf-8")  # the executor, and only it, self-joins
                    wait_until(joined.exists, 30, "executor self-join record")
                    trigger.unlink()  # one-shot: the recovery attempt's executor must not join the limited group
                    record = json.loads(joined.read_text(encoding="utf-8"))
                    assert record["pid"] == pid, "self-join came from a different process than the bound executor"
                    assert record["start_identity"] == birth == identity(pid), "local identity changed"
                    assert record["cgroup"] == "0::/" + str(group.relative_to("/sys/fs/cgroup")), record
                    assert (group / "cgroup.procs").read_text(encoding="utf-8").split() == [str(pid)]
                    release.set()
                    wait_until(lambda: not pid_alive(pid), 30, "contained OOM worker death")
                    after = (group / "memory.events.local").read_text(encoding="utf-8")

                    def require_oom(text):
                        values = dict(line.split() for line in text.splitlines())
                        assert int(values["oom_kill"]) > 0 and int(values["oom"]) > 0, "SIGKILL is not verified OOM"
                    require_oom(after)
                    with pytest.raises(AssertionError, match="not verified OOM"):
                        require_oom(before)
                    fault = {"cgroup": str(group), "memory_max": 32 * 1024 * 1024, "memory_swap_max": 0,
                             "memory_events_before": before, "memory_events_after": after,
                             "pid": pid, "start_identity": birth, "join": "executor self-join (own pid)"}
                wait_until(lambda: not pid_alive(pid), 15, "fixture worker dead")
                recovering[0] = True
                board.wait_worker_exit(tid, submitter, timeout=30)
                board.dispatch("--max", "0")
                assert board.task(tid)["status"] == "ready", board.diag(tid)
                dead = next(run for run in board.runs(tid) if run["id"] == attempt)
                assert dead["outcome"] == "crashed" and dead["worker_pid"] == submitter, board.diag(tid)
                death_events = [event for event in board.events(tid)
                                if event["run_id"] == attempt and event["kind"] == "crashed"]
                assert len(death_events) == 1 and death_events[0]["payload"].get("exit_code") != 0
                board.dispatch()
                next_pid = int(board.task(tid)["worker_pid"])
                assert next_pid != pid
                board.wait_worker_exit(tid, next_pid, timeout=45)
                assert board.task(tid)["status"] == "done", board.diag(tid)
                assert [run["outcome"] for run in board.runs(tid)] == ["crashed", "completed"]
                assert len(board.events(tid, "completed")) == 1
                assert_no_false_human(board, tid)
                assert not board.dispatch().get("spawned")
                faults.append("SIGKILL" if variant == "sigkill" else "bounded cgroup OOM")
                observations.append({"event": "recovered", "reason": fault, "attempt": attempt,
                                     "runs": board.runs(tid), "events": board.events(tid)})
        finally:
            if board is not None and tid is not None:
                cleanup.step("diagnostics", write_diagnostics, board, tid, "crash-" + variant)
            release.set()
            if board is not None:
                cleanup.step("kill_workers", board.kill_workers)
                if group is not None:
                    cleanup.step("cgroup", release_cgroup, board, fleet_root, group)
            elif group is not None:
                cleanup.step("cgroup_unstarted", lambda: (group.rmdir() if group.exists() else None,
                                                          unregister_cgroup(fleet_root, group)))
            cleanup.finish()
    assertions.extend([
        {"name": "sigkill_recovered_exactly_once", "result": "PASS",
         "evidence": "PID+birth bounded SIGKILL of the bound executor; crashed then completed"}])
    if not unverified:
        assertions.extend([
            {"name": "separate_crash_and_kernel_oom", "result": "PASS",
             "evidence": "executor self-joined cgroup; memory.events oom_kill incremented"},
            {"name": "oom_negative_control_rejects_no_oom_kill", "result": "PASS",
             "evidence": "pre-injection memory.events rejected"}])
    publish("crash", fleet_root, faults, observations, assertions,
            result="UNVERIFIED" if unverified else None, unverified=unverified or None)


# ---------------------------------------------------------------------------
# provider exhaustion: bounded non-zero cooldown, one oracle, targeted mutations
# ---------------------------------------------------------------------------

COOLDOWN = 6
QUOTA_HOLDS = ("rate_limit_cooldown", "subscription_quota_reset")  # the real guard reasons for a 429 hold


def provider_flow(board_factory, fleet_root, name, hooks=()):
    from tests.e2e.core.kanban.test_kanban_rate_limit_review import TwoRoleModel, _seed_review_skill, one_terminal_turn
    from tests.fakes.fake_llm_provider import FakeLLMServer

    core = Path(os.environ["FLEET_CORE_SOURCE"])
    model = TwoRoleModel(rate_limited_attempts=1)
    ticks = []
    env_extra = {"HERMES_KANBAN_CRASH_GRACE_SECONDS": "0",
                 "HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS": str(COOLDOWN)}
    if hooks:
        env_extra.update(hook_env(fleet_root, core, hooks))
    with FakeLLMServer(model) as server:
        board = board_factory(name, server.base_url, env_extra=env_extra)
        try:
            _seed_review_skill(board)
            tid = board.create("fixture provider recovery", "--no-requires")
            model.tick = 1
            first = board.dispatch()
            assert any(row["task_id"] == tid for row in first.get("spawned", [])), first
            board.wait_worker_exit(tid, int(board.events(tid, "spawned")[-1]["payload"]["pid"]), timeout=45)
            ticks.append({"event": "spawned_429", "reason": first, "attempt": 1})
            requests_after_429 = len(server.main_requests())
            assert requests_after_429 == 1, "429 attempt must be exactly one provider request"

            model.tick = 2
            reaped = board.dispatch()
            ticks.append({"event": "reaped", "reason": reaped, "attempt": 2})
            runs = board.runs(tid)
            assert [run["outcome"] for run in runs[:1]] == ["rate_limited"] and board.task(tid)["consecutive_failures"] == 0, \
                f"lost neutral retry: {[run['outcome'] for run in runs]} failures={board.task(tid)['consecutive_failures']}"
            assert_no_false_human(board, tid)
            ended = int(runs[0]["ended_at"])
            assert not any(row["task_id"] == tid for row in reaped.get("spawned", [])) and \
                any(row.get("task_id") == tid and row.get("reason") in QUOTA_HOLDS
                    for row in reaped.get("respawn_guarded", [])), \
                f"early dispatch inside the {COOLDOWN}s provider cooldown: {reaped}"
            for n in (3, 4):  # still inside the cooldown: nothing spawns, no provider traffic
                assert time.time() - ended < COOLDOWN - 1, "cooldown window already elapsed; check proves nothing"
                model.tick = n
                held = board.dispatch()
                ticks.append({"event": "held", "reason": held, "attempt": n})
                assert not held.get("spawned"), f"early dispatch inside the provider cooldown: {held}"
                assert len(server.main_requests()) == requests_after_429, "provider billed during cooldown"
                assert_no_false_human(board, tid)
            time.sleep(max(0.0, ended + COOLDOWN + 1 - time.time()))  # bounded wait for the typed cooldown

            for n in range(5, 11):
                model.tick = n
                result = board.dispatch()
                if any(row["task_id"] == tid for row in result.get("spawned", [])):
                    board.wait_worker_exit(tid, int(board.events(tid, "spawned")[-1]["payload"]["pid"]), timeout=45)
                task = board.task(tid)
                assert_no_false_human(board, tid)
                ticks.append({"event": task["status"], "reason": result, "attempt": n})
                if task["status"] == "done":
                    break
            outcomes = [run["outcome"] for run in board.runs(tid)]
            assert outcomes == ["rate_limited", "review_requested", "completed"], \
                f"lost neutral retry/review/completion: {outcomes}"
            assert len(board.events(tid, "rate_limited")) == 1 and len(board.events(tid, "completed")) == 1
            assert board.task(tid)["consecutive_failures"] == 0
            assert [a.role for a in model.attempts] == ["impl", "impl", "review"]
            assert model.attempts[0].answers == ["Error"] and model.attempts[0].requests == 1
            assert model.attempts[1].tick >= 5, "retry dispatched before the cooldown elapsed"
            assert one_terminal_turn(model.attempts[1], "kanban_request_review")
            assert one_terminal_turn(model.attempts[2], "kanban_complete")
            assert_no_false_human(board, tid)
            assert not board.dispatch().get("spawned")
            ticks.extend({"event": event["kind"], "reason": event["payload"], "attempt": event["run_id"]}
                         for event in board.events(tid))
            return outcomes, [a.answers for a in model.attempts], ticks
        finally:
            board.kill_workers()


def test_provider_exhaustion_then_review(fleet_root, containment, board_factory):
    outcomes, billing, ticks = provider_flow(board_factory, fleet_root, "provider")
    mutants = {}
    for mutant, hooks, pattern in (
            ("lost_retry", ("lost_retry",), "lost neutral retry"),
            ("fabricated_human_blocker", ("false_human",), "false human blocker"),
            ("early_dispatch", ("early_dispatch",), "early dispatch")):
        with pytest.raises(AssertionError, match=pattern) as caught:
            provider_flow(board_factory, fleet_root, "provider-" + mutant, hooks)
        mutants[mutant] = str(caught.value)[:200]
    publish("provider", fleet_root, f"fake HTTP429 for first implementer attempt only; cooldown {COOLDOWN}s",
            ticks, [
        {"name": "same_work_neutral_retry_review_completes_once", "result": "PASS", "evidence": outcomes},
        {"name": "bounded_cooldown_no_early_dispatch_then_recovery", "result": "PASS",
         "evidence": f"two ticks inside {COOLDOWN}s: guard rate_limit_cooldown, zero spawns, zero provider requests"},
        {"name": "no_false_human_blocker_whole_history", "result": "PASS", "evidence": "task row, runs and every event"},
        {"name": "provider_billing_bounded", "result": "PASS", "evidence": billing},
        {"name": "implementation_mutations_rejected_by_same_oracle", "result": "PASS", "evidence": mutants}])


# ---------------------------------------------------------------------------
# remote notices: persist-before-ack, crash/failure/restart, ack mutations
# ---------------------------------------------------------------------------

def _remote_poll(root, door, now, mode, mutant):
    from conftest import owned, preflight
    from hermes_state import SessionDB
    from tui_gateway import kanban_remote_notices as notices
    from tui_gateway.session_notifications import _kb_ack_durable
    fixture = Path(os.environ["FLEET_FIXTURE_ROOT"])
    preflight(fixture)
    owned(fixture, root)
    replica, ledger = root / "hub.db", root / "ledger.db"
    notices.ledger_path = lambda: ledger

    def die():
        os.kill(os.getpid(), signal.SIGKILL)

    if mutant == "premature_ack":  # implementation mutation: collect acks the claim before the transcript write
        real_collect = notices.collect

        def collect(*args, **kwargs):
            claims = real_collect(*args, **kwargs)
            for claim in claims:
                notices.ack(claim, ledger=kwargs.get("ledger"))
            return claims
        notices.collect = collect
    if mutant == "lost_dedupe":  # implementation mutation: transcript no longer proves delivery
        notices.notice_in_transcript = lambda *args, **kwargs: False

    with SessionDB(root / "sessions.db") as db:
        real_append = db.append_kanban_notice
        if mode == "failed_persist":
            def failing(*args, **kwargs):
                raise RuntimeError("fixture persist failure")
            type(db).append_kanban_notice = lambda self, *a, **k: failing()
        elif mode == "crash_before_persist" and door == "sweep":
            type(db).append_kanban_notice = lambda self, *a, **k: die()
        if mode == "crash_after_persist" and door == "sweep":
            notices.ack = lambda *args, **kwargs: die()

        @contextlib.contextmanager
        def opener(profile, sid):
            assert profile == "default"
            yield db, sid

        persisted = 0
        if door == "collect":
            got = notices.collect(
                "fixture-chat", "default", hub_db=replica, ledger=ledger, now=now,
                transcript_has=lambda key, task_id: notices.notice_in_transcript(db, "fixture-chat", key, task_id))
            for claim in got:
                with contextlib.closing(sqlite3.connect(ledger)) as conn:
                    delivered = conn.execute("SELECT delivered FROM notices WHERE session=? AND ref=?",
                                             ("fixture-chat", claim["ref"])).fetchone()[0]
                assert delivered == 0, "premature acknowledgement before transcript"
                if mode == "crash_before_persist":
                    die()
                text = object()
                session = {"_kanban_claims": [{"remote": claim, "texts": [text]}]}
                key = claim["event_key"]
                try:
                    if mode == "failed_persist":
                        raise RuntimeError("fixture persist failure")
                    real_append("fixture-chat", claim["text"], {"notice_id": key, "kanban_event_keys": [key]})
                except RuntimeError as exc:
                    print(f"persist error: {exc!r}", file=sys.stderr)
                    continue  # never acknowledged: claim stays in flight for the successor
                persisted += 1
                assert any(key in json.dumps(row) for row in db.get_messages("fixture-chat"))
                if mode == "crash_after_persist":
                    die()
                _kb_ack_durable(session, {id(text)})
                assert session["_kanban_claims"][0]["acked"] is True
            delivered_count = len(got)
        else:
            got = notices.sweep_remote_notices(replica_path=replica, gateway="receiver", session_db=opener,
                                               ledger=ledger, now=now)
            delivered_count = len(got)
        return {"delivered": delivered_count, "persisted": persisted, "pid": os.getpid()}


def _remote_card(tid, chat, at, event_id):
    return {"id": tid, "status": "done", "title": "fixture completion", "board": "default", "assignee": "default",
            "sessions": [{"gw": "receiver", "profile": "default", "session": chat, "rel": "shepherd"}],
            "completed_at": at, "completion_notice": {"event_id": event_id,
                                                      "payload": {"summary": "fixture persisted completion"}}}


def remote_oracle(fleet_root, door, mutant=""):
    """The single oracle. Raises AssertionError on any lost, premature, duplicated or silent-history behaviour."""
    from hermes_state import SessionDB
    terminal = 1_800_000_000.0
    root = fleet_root / f"remote-{door}-{mutant or 'real'}"
    root.mkdir()
    log = []

    def insert(value, seq):
        with contextlib.closing(sqlite3.connect(root / "hub.db")) as conn, conn:
            conn.execute("INSERT INTO replica VALUES (?,?,?,?,?)",
                         ("sender", "tasks", "default/default/" + value["id"], json.dumps(value), seq))

    def state():
        with contextlib.closing(sqlite3.connect(root / "ledger.db")) as conn:
            rows = [] if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='notices'").fetchone() else \
                conn.execute("SELECT ref, delivered FROM notices WHERE session='fixture-chat'").fetchall()
        with SessionDB(root / "sessions.db") as db:
            return {"ledger": rows, "messages": len(db.get_messages("fixture-chat")),
                    "history": len(db.get_messages("fixture-history"))}

    def poll(which, now, mode="normal", expect_kill=False, use_mutant=False):
        result_path = root / "poll-result.json"
        result_path.unlink(missing_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), "--remote-poll", str(root), which, str(now), mode,
                   mutant if use_mutant else ""]
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        log.append({"event": "fresh_process_poll", "reason": {"door": which, "mode": mode, "mutant": use_mutant and mutant,
                    "exit": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-1500:]},
                    "attempt": len(log) + 1})
        if expect_kill:
            assert proc.returncode == -signal.SIGKILL, (mode, proc.returncode, proc.stderr[-1500:])
            return None
        assert proc.returncode == 0, ("premature acknowledgement before transcript" if
                                      "premature acknowledgement" in proc.stderr else "poll failed", proc.stderr[-1500:])
        return json.loads(result_path.read_text(encoding="utf-8"))

    with contextlib.closing(sqlite3.connect(root / "hub.db")) as conn, conn:
        conn.execute("CREATE TABLE meta(k TEXT,v TEXT)")
        conn.executemany("INSERT INTO meta VALUES (?,?)", [("gateway", "receiver"), ("cursor", "100"), ("head", "100")])
        conn.execute("CREATE TABLE replica(gw TEXT,coll TEXT,key TEXT,data TEXT,seq INTEGER)")
    with SessionDB(root / "sessions.db") as db:
        db.create_session("fixture-chat", "desktop")
        db.create_session("fixture-history", "desktop")
    insert(_remote_card("t_history", "fixture-history", terminal - 9 * 3600, 9000), 1)
    use = bool(mutant)
    other = "sweep" if door == "collect" else "collect"
    late = terminal + 8 * 3600

    baseline = poll(door, terminal - 60, use_mutant=use)
    assert baseline["delivered"] == 0 and state()["messages"] == state()["history"] == 0, "retained history must be silent"
    insert(_remote_card("t_delayed", "fixture-chat", terminal, 9001), 2)

    # Crash after claiming/starting, before the transcript write: nothing acked, nothing persisted.
    poll(door, late, "crash_before_persist", expect_kill=True, use_mutant=use)
    after_crash = state()
    assert after_crash["messages"] == 0, "crash before persist wrote a transcript row"
    assert not any(delivered for _, delivered in after_crash["ledger"]), \
        "premature acknowledgement before transcript (ledger delivered with no row)"
    # Persist failure: no ack either.
    poll(door, late + 1, "failed_persist", use_mutant=use)
    after_fail = state()
    assert after_fail["messages"] == 0 and not any(d for _, d in after_fail["ledger"]), \
        "premature acknowledgement after a failed persist"
    # Fresh restart: delivered exactly once, acked only now.
    arrival = poll(door, late + 2, use_mutant=use)
    delivered = state()
    assert arrival["delivered"] == 1 and delivered["messages"] == 1 and delivered["history"] == 0, (arrival, delivered)
    assert delivered["ledger"] and all(d for _, d in delivered["ledger"]), delivered
    # Both entry paths, repeatedly and across fresh processes: idempotent.
    for step, which in enumerate((door, other, door, other), start=3):
        repeat = poll(which, late + step, use_mutant=use)
        assert repeat["delivered"] == 0 and state()["messages"] == 1 and state()["history"] == 0, \
            f"duplicate/redelivered notice via {which}: {repeat} {state()}"

    # Second terminal arrival: persisted, then the process dies before the ack.
    insert(_remote_card("t_second", "fixture-chat", terminal, 9002), 3)
    poll(door, late + 10, "crash_after_persist", expect_kill=True, use_mutant=use)
    persisted_unacked = state()
    assert persisted_unacked["messages"] == 2, persisted_unacked
    restart = poll(door, late + 11, use_mutant=use)
    final = state()
    # collect must hand out nothing (the dead holder's claim is acked from the transcript); the sweep may
    # re-settle the persisted row once (its append dedupes by key), but never adds a second transcript row.
    settled = 0 if door == "collect" else 1
    assert restart["delivered"] <= settled and final["messages"] == 2 and final["history"] == 0, \
        f"duplicate/redelivered notice after persisted-but-unacked crash: {restart} {final} {log[-1]}"
    assert len(final["ledger"]) == 2 and all(d for _, d in final["ledger"]), final
    for step, which in enumerate((other, door), start=12):
        again = poll(which, late + step, use_mutant=use)
        assert again["delivered"] == 0 and state()["messages"] == 2, f"duplicate via {which}"
    return log


def test_remote_collect_sweep_delayed_arrival_restart(fleet_root, containment):
    observations = []
    for door in ("collect", "sweep"):
        observations.extend(remote_oracle(fleet_root, door))
    mutants = {}
    for mutant, pattern in (("premature_ack", "premature acknowledgement"),
                            ("lost_dedupe", "duplicate/redelivered")):
        with pytest.raises(AssertionError, match=pattern) as caught:
            remote_oracle(fleet_root, "collect", mutant)
        mutants[mutant] = str(caught.value)[:200]
    publish("remote", fleet_root, "terminal arrival delayed eight hours after baseline; SIGKILL before persist, "
            "persist failure, SIGKILL after persist before ack", observations, [
        {"name": "persist_before_ack_crash_and_failed_persist", "result": "PASS",
         "evidence": "both doors: ledger undelivered and zero rows after SIGKILL / failed append; fresh process delivers once"},
        {"name": "both_entry_paths_after_process_restart", "result": "PASS",
         "evidence": "collect and sweep each in fresh interpreters, alternating"},
        {"name": "persisted_unacked_crash_no_duplicate", "result": "PASS",
         "evidence": "second notice persisted then SIGKILL; restart acks without a second row"},
        {"name": "durable_dedupe_and_history_silence", "result": "PASS", "evidence": "retained history rows stay silent"},
        {"name": "ack_mutations_rejected_by_same_oracle", "result": "PASS", "evidence": mutants}])


# ---------------------------------------------------------------------------
# wake probes through real fake-provider workers
# ---------------------------------------------------------------------------

def wake_flow(board_factory, fleet_root, monkeypatch, label, command, expected_rc):
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_wake_check as wake
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall

    probe = fleet_root / f"{label}-probe.sh"
    probe.write_text("exit 1\n", encoding="utf-8")
    cmd = "sh " + shlex.quote(str(probe))
    park_args = {"reason": "SCHEDULED_UNTIL=2099-01-01T00:00:00Z fixture producer pending; wake when probe exits 0",
                 "wake_check": cmd, "check_every": 120,
                 "awaiting": {"kind": "external", "ref": "fixture-producer", "why": "controlled test completion"},
                 "kind": "time"}
    phase = {"name": "park"}
    contexts = {}
    scripts = {
        "park": [("kanban_block", park_args)],
        # the repair worker rewrites the probe itself, then parks again
        "repair": [("read_file", {"path": str(probe)}), ("write_file", {"path": str(probe), "content": "exit 1\n"}), ("kanban_block", park_args)],
        "finish": [("kanban_complete", {"summary": "fixture work completed after repaired probe"})],
    }

    def model(record):
        messages = record["body"]["messages"]
        contexts.setdefault(phase["name"], json.dumps(messages, default=str))
        done = sum(1 for message in messages if message.get("role") == "tool")
        script = scripts[phase["name"]]
        if done >= len(script):
            return Text("fixture step finished")
        name, args = script[done]
        return ToolCall(name, args)

    transitions = []
    monkeypatch.setattr(wake, "PROBE_TIMEOUT_SECONDS", 0.2)
    try:
        with FakeLLMServer(model) as server:
            board = board_factory("wake-" + label, server.base_url, env_extra={"HERMES_KANBAN_CRASH_GRACE_SECONDS": "0"})
            try:
                def worker_round(name):
                    phase["name"] = name
                    result = board.dispatch()
                    spawned = [row for row in result.get("spawned", []) if row["task_id"] == tid]
                    assert len(spawned) == 1, (name, result, board.diag(tid))
                    board.wait_worker_exit(tid, int(board.events(tid, "spawned")[-1]["payload"]["pid"]), timeout=60)

                def observe(conn_unused=None):
                    task = board.task(tid)
                    assert_no_false_human(board, tid)
                    transitions.append({"event": task["status"], "reason": board.events(tid)[-1]["payload"],
                                        "attempt": task["current_run_id"], "task_id": tid})
                    return task

                tid = board.create("fixture wake regression " + label, "--no-requires")
                worker_round("park")
                assert observe()["status"] == "scheduled", board.diag(tid)
                with kbc.connect_closing(board.db_path) as conn:
                    now = int(time.time())
                    wake.run_due_wake_checks(conn, now=now, wait=True)
                    assert observe()["status"] == "scheduled"
                    state = wake.get_state(conn, tid)
                    assert state["last_rc"] == 1 and state["fails"] == 0, f"exit 1 must be not-ready: {state}"
                    probe.write_text(command + "\n", encoding="utf-8")  # an already-valid probe breaks
                    for n in range(1, 4):
                        wake.run_due_wake_checks(conn, now=now + n * 200, wait=True)
                        if n < 3:
                            assert observe()["status"] == "scheduled"
                            assert wake.get_state(conn, tid)["fails"] == n
                    events = board.events(tid)
                    assert not any(e["kind"] == "condition_met" for e in events), "broken probe falsely condition_met"
                    broken = [e for e in events if e["kind"] == "wake_check_broken"]
                    assert len(broken) == 1 and broken[0]["payload"]["rc"] == expected_rc, \
                        f"broken probe not reported as wake_check_broken rc={expected_rc}: {[e['kind'] for e in events]}"
                    assert observe()["status"] == "ready"

                    worker_round("repair")  # real worker: repairs the probe file, parks again
                    assert "wake_check" in contexts["repair"] and "Fix the probe" in contexts["repair"], \
                        "repair worker was not told the truthful broken-probe reason"
                    assert observe()["status"] == "scheduled", board.diag(tid)
                    wake.run_due_wake_checks(conn, now=now + 800, wait=True)
                    assert observe()["status"] == "scheduled"
                    probe.write_text("printf 'fixture producer completed\\n'; exit 0\n", encoding="utf-8")  # producer done
                    wake.run_due_wake_checks(conn, now=now + 1000, wait=True)
                    assert observe()["status"] == "ready"
                    met = [e for e in board.events(tid) if e["kind"] == "condition_met"]
                    assert len(met) == 1 and "fixture producer completed" in met[0]["payload"]["output"]
                worker_round("finish")  # real worker resumes and completes the original work
                assert observe()["status"] == "done"
                assert len(board.events(tid, "completed")) == 1
                assert not board.dispatch().get("spawned")
                with kbc.connect_closing(board.db_path) as conn:
                    wake.run_due_wake_checks(conn, now=now + 1200, wait=True)
                assert len(board.events(tid, "completed")) == 1
                return transitions, [run["outcome"] for run in board.runs(tid)]
            finally:
                cleanup = Cleanup()
                cleanup.step("diagnostics", write_diagnostics, board, tid, "wake-" + label)
                cleanup.step("requests", dump_requests, server, f"wake-{label}")
                cleanup.step("kill_workers", board.kill_workers)
                cleanup.finish()
    finally:
        wake._shutdown_for_tests()


def approval_gate_flow(board_factory, fleet_root, monkeypatch):
    """A genuine approval request, made by a real worker, stays human-gated under the same scheduler ticks."""
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_wake_check as wake
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall

    request = {"action": "Choose allow or decline. Unblock = allow; archive = decline.",
               "why_human": "Only the operator may approve the fixture action.",
               "interaction": "approval", "scope": "isolated fixture", "evidence": "test negative control"}

    def model(record):
        messages = record["body"]["messages"]
        if any(message.get("role") == "tool" for message in messages):
            return Text("fixture waiting for approval")
        return ToolCall("kanban_block", {"reason": "fixture approval required", "kind": "needs_input",
                                         "human_request": request})
    try:
        with FakeLLMServer(model) as server:
            board = board_factory("wake-approval", server.base_url, env_extra={"HERMES_KANBAN_CRASH_GRACE_SECONDS": "0"})
            try:
                tid = board.create("genuine fixture approval", "--no-requires")
                result = board.dispatch()
                assert any(row["task_id"] == tid for row in result.get("spawned", [])), result
                board.wait_worker_exit(tid, int(board.events(tid, "spawned")[-1]["payload"]["pid"]), timeout=60)
                task = board.task(tid)
                assert task["status"] == "blocked" and task["human_request"], board.diag(tid)
                before = task["human_request"]
                with kbc.connect_closing(board.db_path) as conn:
                    wake.run_due_wake_checks(conn, now=int(time.time()) + 9999, wait=True)
                assert not board.dispatch().get("spawned"), "approval-gated work was dispatched"
                after = board.task(tid)
                assert after["status"] == "blocked" and after["human_request"] == before
                return json.loads(after["human_request"])["interaction"]
            finally:
                board.kill_workers()
    finally:
        wake._shutdown_for_tests()


def test_wake_repair_and_truthful_reason(fleet_root, containment, board_factory, monkeypatch):
    from hermes_cli import kanban_wake_check as wake

    transitions, outcomes = [], {}
    for label, command, rc in (("exit2", "exit 2", 2), ("timeout", "sleep 2", "timeout")):
        moves, outcomes[label] = wake_flow(board_factory, fleet_root, monkeypatch, label, command, rc)
        transitions.extend(moves)
    # Targeted implementation mutation: the real broken-wake transition mislabelled as success,
    # judged by the very same flow/oracle.
    with monkeypatch.context() as mutant:
        mutant.setattr(wake, "BROKEN_EVENT", "condition_met")
        with pytest.raises(AssertionError, match="falsely condition_met") as caught:
            wake_flow(board_factory, fleet_root, monkeypatch, "mutant", "exit 2", 2)
    interaction = approval_gate_flow(board_factory, fleet_root, monkeypatch)
    assert interaction == "approval"
    publish("wake", fleet_root, ["exit2", "timeout"], transitions, [
        {"name": "exit1_not_ready_exit0_met", "result": "PASS", "evidence": "persisted statuses and condition_met payload"},
        {"name": "broken_recovers_after_three_then_original_completes_once", "result": "PASS",
         "evidence": {"outcomes": outcomes, "path": "real fake-provider workers park, repair, re-park, complete"}},
        {"name": "no_false_human_requests", "result": "PASS", "evidence": "task row, runs and all events of every task"},
        {"name": "mutation_wrong_success_event_killed", "result": "PASS", "evidence": str(caught.value)[:200]},
        {"name": "genuine_approval_remains_gated", "result": "PASS",
         "evidence": "worker-made needs_input request unchanged; no dispatch under the same ticks"}])


if __name__ == "__main__":
    assert sys.argv[1] == "--remote-poll"
    _root = Path(sys.argv[2])
    _result = _remote_poll(_root, sys.argv[3], float(sys.argv[4]), sys.argv[5], sys.argv[6])
    (_root / "poll-result.json").write_text(json.dumps(_result), encoding="utf-8")
