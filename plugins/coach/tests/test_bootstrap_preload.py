"""Regression: plugin tests must not re-exec pytest when they point HERMES_HOME at a temp dir.

Root cause: core's lazy `hermes_bootstrap` import ran prepare_launch under a monkeypatched temp
HERMES_HOME (a "borrowed" home) and os.execv'd the pytest process onto the store interpreter.
The plugin conftest now sets core's HERMES_DISABLE_LAZY_INSTALLS test kill-switch.
"""
import os

import pytest


def test_kill_switch_set_at_collection_and_per_test():
    assert os.environ.get("HERMES_DISABLE_LAZY_INSTALLS") == "1"


def test_first_agent_import_under_temp_home_does_not_reexec(tmp_path, monkeypatch):
    """Importing agent.process_bootstrap under a temp HERMES_HOME must return, not os.execv."""
    pytest.importorskip("hermes_bootstrap")
    calls = []
    monkeypatch.setattr(os, "execv", lambda *a: calls.append(a))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import agent.process_bootstrap  # noqa: F401
    assert calls == []
