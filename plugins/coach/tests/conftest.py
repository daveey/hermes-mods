import importlib.util
import os

import pytest

# Tests set HERMES_HOME=tmp_path. The first lazy import of hermes_bootstrap under that home (e.g. SessionDB.append_message
# -> agent.conversation_loop -> hermes_bootstrap) otherwise runs a full dependency sync into the tmp home: ~400MB
# (cache/uv/archive-v0 + installs/<id>/environments) per test basetemp. When the interpreter already has Hermes'
# dependencies (the test environment), turn the lazy sync off. Without them the sync is the only way the import works.
if importlib.util.find_spec("ruamel") is not None:
    os.environ.setdefault("HERMES_DISABLE_LAZY_INSTALLS", "1")


@pytest.fixture(autouse=True)
def _no_dependency_sync_in_tmp_home(tmp_path):
    """Regression guard: a test basetemp must never hold a uv cache or a built environment."""
    yield
    leaked = [str(p.relative_to(tmp_path)) for pattern in ("cache/uv/archive-v0", "installs/*/environments")
              for p in tmp_path.glob(pattern)]
    assert not leaked, f"dependency sync ran inside the test home (~400MB per test): {leaked}"



@pytest.fixture(autouse=True)
def _no_system_one_network(monkeypatch, tmp_path):
    """Never call the real System One API from tests (the client reads the key from ~/.hermes/.env too) and never
    write the live system-one log: mode off unless a test opts in, log to tmp."""
    monkeypatch.setenv("SYSTEM_ONE_MODE", "off")
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    monkeypatch.setenv("SYSTEM_ONE_LOG", str(tmp_path / "system-one-log.jsonl"))
    # The second-opinion actions are on in the live config (shared-config); tests opt in explicitly so no test
    # creates a real Kanban card or makes a real strong-model call.
    monkeypatch.setenv("COACH_FIXLOOP_REVIEW", "0")
    monkeypatch.setenv("COACH_DIFF_REVIEW", "0")
    # Self-heal judges call the aux model: off unless a test opts in with a fake judge.
    monkeypatch.setenv("COACH_SELFHEAL", "off")
    # The self-heal lane never runs real `hermes kanban` / `hermes inbox` / ssh from tests (inject a runner).
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")
    monkeypatch.delenv("COACH_SELFHEAL_HUB", raising=False)
    monkeypatch.delenv("COACH_SELFHEAL_IS_HUB", raising=False)
