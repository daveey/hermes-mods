"""Keep core's launch bootstrap from replacing the pytest process.

Core imports hermes_bootstrap lazily (SessionDB.append_message -> agent.conversation_loop ->
agent.process_bootstrap -> hermes_bootstrap). Its import runs venv_sync.prepare_launch, which on a
self-managed data root rebuilds a runtime and os.execv()s into the store interpreter. Under pytest that
swaps the process for an interpreter without pytest: the run dies mid-test with no summary line (rc 0/1
depending on -v/-q). Our fixtures also point HERMES_HOME at a tmp_path, which is always such a root.
HERMES_DISABLE_LAZY_INSTALLS=1 makes prepare_launch return None, so the import is side-effect free.
"""
import os

os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"

import pytest


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    """Every test, including parser/error paths, gets private stores before executing."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
