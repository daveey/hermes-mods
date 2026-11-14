import importlib.util
import json
import os
import re
import sqlite3
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]

# Core is private, so the public plugin-suites CI has none. A test that needs core there (it imports
# agent/hermes_cli/model_tools, or ask-gate's runtime contract refuses registration because
# agent.pre_final_gate is not importable) skips instead of failing. Only when no core is on the path and
# none was named: scripts/test-all sets HERMES_CORE_CHECKOUT whenever it puts core on PYTHONPATH, and then
# a missing core module still fails loudly. Only core's own top-level modules count.
_NO_CORE = importlib.util.find_spec("hermes_cli") is None and not os.environ.get("HERMES_CORE_CHECKOUT")
_CORE_TOPS = frozenset({"agent", "acp_adapter", "cron", "gateway", "model_tools", "run_agent", "tools",
                        "toolsets", "cli", "batch_runner"})
_UNIMPORTABLE = re.compile(r"could not import ([\w.]+)")


def _core_module(name):
    top = (name or "").split(".")[0]
    return top in _CORE_TOPS or top.startswith("hermes_")


def _missing_core(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ModuleNotFoundError) and _core_module(exc.name):
            return exc.name
        found = _UNIMPORTABLE.search(str(exc))
        if found and _core_module(found.group(1)):
            return found.group(1)
        exc = exc.__cause__ or exc.__context__
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not (_NO_CORE and report.failed and call.excinfo is not None):
        return
    missing = _missing_core(call.excinfo.value)
    if missing:
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: needs Hermes core ({missing} not importable)")
FIX = json.loads((Path(__file__).parent / "fixtures" / "session_20261021_103941.json").read_text())

SHEET = ("Things the agent can do itself (generated from local config just now):\n"
         "- token-broker scopes: cf-token-minter (auto, can mint narrower Cloudflare tokens), cf-infra (auto)\n"
         "- ssh hosts: mini-host\n- Chrome profiles signed in: user@example.com\n- browser vault entries: none")


def _load():
    spec = importlib.util.spec_from_file_location("ask_gate_plugin_t", HERE / "__init__.py",
                                                  submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ASK_GATE_LEDGER", str(tmp_path / "state" / "ask-ledger.sqlite"))
    for k in ("HERMES_KANBAN_TASK", "ASK_GATE_OFF"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASK_GATE_NO_PREWARM", "1")
    # Core's chat projection (other gateways' cards of a chat): empty by default, so no test reads a real core's
    # replica or ledger; tests/test_l1_remote_cards.py installs its own fake.
    projection = types.ModuleType("hermes_cli.kanban_session_activity")
    projection.session_tasks = lambda session_ids, statuses=(), *, stale_ok=False: []
    # Only the projection read is faked: core's own writers (kanban_schedule's annotation scope reads
    # ``_origin_scope``) still reach the real module's other names when a core is importable.
    try:
        real = importlib.import_module("hermes_cli.kanban_session_activity")
    except ImportError:
        real = None
    if real is not None and real is not projection:
        projection.__getattr__ = lambda name: getattr(real, name)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_session_activity", projection)
    mod = _load()
    monkeypatch.setattr(mod.core, "capability_sheet", lambda *a, **k: SHEET)

    def boom(system, user):
        raise AssertionError("the model must not be called here")
    mod.LLM = boom
    mod.LOG.reset("s1")
    return mod


@pytest.fixture
def board(tmp_path):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text, title text, status text, assignee text, created_at real, "
              "completed_at real, result text, session_id text)")
    c.commit()
    c.close()

    def add(id, status, title="card", session="s1", created=1000.0, completed=None, result=""):
        c = sqlite3.connect(db)
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?)", (id, title, status, "default", created, completed, result, session))
        c.commit()
        c.close()
    return add


def llm_returning(verdicts):
    """A stub model returning the given [(verdict, reason, how)] in order; records the prompts it saw."""
    seen = []

    def fn(system, user):
        seen.append((system, user))
        return json.dumps({"asks": [{"i": i, "verdict": v[0], "reason": v[1], "how": v[2] if len(v) > 2 else "",
                                    **({"equivalence": v[3]} if len(v) > 3 else {})}
                                    for i, v in enumerate(verdicts)]})
    fn.seen = seen
    return fn
