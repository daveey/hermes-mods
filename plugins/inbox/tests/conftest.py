"""Keep core's launch bootstrap from replacing the pytest process.

Core imports hermes_bootstrap lazily (SessionDB.append_message -> agent.conversation_loop ->
agent.process_bootstrap -> hermes_bootstrap). Its import runs venv_sync.prepare_launch, which on a
self-managed data root rebuilds a runtime and os.execv()s into the store interpreter. Under pytest that
swaps the process for an interpreter without pytest: the run dies mid-test with no summary line (rc 0/1
depending on -v/-q). Our fixtures also point HERMES_HOME at a tmp_path, which is always such a root.
HERMES_DISABLE_LAZY_INSTALLS=1 makes prepare_launch return None, so the import is side-effect free.
"""
import importlib.util
import os

os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"

import pytest

# Core modules the store and its callers import (SessionDB, the Kanban board, the delegation context).
CORE_MODULES = ("hermes_state", "hermes_state_ids", "hermes_constants", "hermes_cli", "agent")


def _missing_core() -> list[str]:
    missing = []
    for name in CORE_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_core: test runs the real Hermes core (hermes_state SessionDB, Kanban board); "
        "skipped only when core is not importable and no core checkout was selected",
    )


def pytest_collection_modifyitems(config, items):
    """Skip core-backed tests when core is absent (public plugin-suites CI has no core checkout).

    A run that selected a core (scripts/test-all sets HERMES_CORE_SOURCE) never skips: there a missing
    core module is a broken environment and must fail loudly, so the same tests keep running with core.
    """
    if os.environ.get("HERMES_CORE_SOURCE"):
        return
    missing = _missing_core()
    if not missing:
        return
    skip = pytest.mark.skip(reason=f"needs Hermes core (not importable: {', '.join(missing)}); "
                                   "run via scripts/test-all with a core checkout")
    for item in items:
        if item.get_closest_marker("needs_core"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    """Every test, including parser/error paths, gets private stores before executing."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKER_TASK", raising=False)
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    # A Kanban worker's shell carries the core write fence (value = the real Hermes root). pytest's
    # tmp_path sits under TMPDIR=<root>/cache/scratch, i.e. inside that root, so a temp board would
    # be opened read-only and a missing kanban.db fails with "unable to open database file".
    monkeypatch.delenv("HERMES_DELEGATED_CHILD_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_DELEGATED_CHILD_OWNER", raising=False)
