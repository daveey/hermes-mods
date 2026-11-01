import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading

import pytest

from home_guard import HomeGuard

# Collection is isolated too: imported modules may capture home paths before fixtures.
_LIVE_HOME = Path.home() / '.hermes'
_COLLECTION_HOME = tempfile.TemporaryDirectory(prefix='coach-collection-')
# tempfile users (including pytest fd capture) must stay inside our owned root.
tempfile.tempdir = _COLLECTION_HOME.name
_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_ROOTS = [p.resolve() for p in (_LIVE_HOME / 'plugins').glob('*')]
_ALLOWED_CODE = [Path(sys.prefix), Path(sys.base_prefix)]
sys.dont_write_bytecode = True  # no generated caches in the live runtime checkout
# Managed core lives under .hermes; allow its source, not its home/state.
_core_spec = importlib.util.find_spec('hermes_cli')
if _core_spec and _core_spec.origin:
    core_root = Path(_core_spec.origin).resolve().parents[1]
    _ALLOWED_CODE.append(core_root)
    # Bootstrap probes its installation manifest next to the source checkout.
    # This is immutable runtime metadata, not profile config/auth/session state.
    _ALLOWED_CODE.append(core_root.parent / 'manifest.json')
_GUARD = HomeGuard(_LIVE_HOME, [_ROOT, _COLLECTION_HOME.name], _PLUGIN_ROOTS, _ALLOWED_CODE)
_GUARD.install()
os.environ['HERMES_HOME'] = _COLLECTION_HOME.name
os.environ['HERMES_RUNTIME_DIR'] = str(Path(_COLLECTION_HOME.name) / 'tools')
os.environ['COACH_LESSONS'] = '0'


def pytest_configure(config):
    # Give pytest a known owned root before it creates its lock files/fixtures.
    if not config.option.basetemp:
        config.option.basetemp = str(Path(_COLLECTION_HOME.name) / 'pytest')
    _GUARD.allow(config.option.basetemp)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    late = list(_GUARD.violations)
    if hasattr(session.config, 'workeroutput'):
        # xdist ignores a worker's non-interrupt exitstatus. Send the ledger to
        # the controller instead of silently losing last-test/collection leaks.
        session.config.workeroutput['coach_isolation_violations'] = late
    late += getattr(session.config, '_coach_worker_violations', [])
    if late:
        session.exitstatus = 1
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            reporter.write_sep('!', 'coach late/collection isolation violations')
            for violation in late:
                reporter.write_line(violation)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    violations = node.workeroutput.get('coach_isolation_violations', [])
    existing = getattr(node.config, '_coach_worker_violations', [])
    node.config._coach_worker_violations = existing + violations


@pytest.fixture(autouse=True)
def _live_home_guard(tmp_path_factory):
    _GUARD.allow(tmp_path_factory.getbasetemp())
    yield
    violations, _GUARD.violations = _GUARD.violations, []
    assert not violations, 'coach isolation violations:\n' + '\n'.join(violations)


@pytest.fixture(autouse=True)
def _join_coach_threads(monkeypatch, _live_home_guard):
    """Join while the shared monkeypatch fixture's fake judges/env are still installed."""
    threads = []
    start = threading.Thread.start

    def tracked(thread, *args, **kwargs):
        target = getattr(thread, '_target', None)
        code = getattr(target, '__code__', None)
        if code and (Path(code.co_filename).resolve().parent == _ROOT / 'plugins/coach'
                     or code.co_name == '_lesson_bg' or code.co_name.startswith('_selfheal_')):
            threads.append(thread)
        return start(thread, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, 'start', tracked)
    yield
    for thread in threads:
        thread.join(timeout=10)
    assert not [t.name for t in threads if t.is_alive()], 'coach background thread outlived its test'

# Tests set HERMES_HOME=tmp_path. The first lazy import of hermes_bootstrap under that home (e.g. SessionDB.append_message
# -> agent.conversation_loop -> hermes_bootstrap) otherwise runs a full dependency sync into the tmp home: ~400MB
# (cache/uv/archive-v0 + installs/<id>/environments) per test basetemp. When the interpreter already has Hermes'
# dependencies (the test environment), turn the lazy sync off. Without them the sync is the only way the import works.
if importlib.util.find_spec("ruamel") is not None:
    os.environ.setdefault("HERMES_DISABLE_LAZY_INSTALLS", "1")


@pytest.fixture(autouse=True)
def _isolated_worker_context(monkeypatch, tmp_path):
    """Tests are not children of the worker running pytest, nor owners of its live board.

    Clear inherited routing/identity pins before test fixtures apply their own values.
    Every test gets an isolated home; individual fixtures may override it.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Core terminal probes toolchain facts independently of HERMES_HOME.
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("COACH_LESSONS", "0")
    for key in tuple(os.environ):
        if key in ("HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_SINGLE_QUERY_SESSION") or key.startswith("HERMES_KANBAN_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))


@pytest.fixture(autouse=True)
def _reap_test_home_gateways(tmp_path):
    """Stop gateways a test's subprocesses auto-started under tmp_path. They detach from the
    child and would otherwise hold the tmp home's state.db WAL/SHM after pytest deletes it."""
    yield
    from gateway_reaper import stop_gateways_under
    stop_gateways_under(tmp_path)


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
    # session_commit_fix reads the fork's real git history (~/.hermes/hermes-agent origin/main) by default, so a real
    # upstream `fix(<component>): ...` commit near a fixture's NOW gets "adopted" and the test's outcome depends on
    # whatever the fork has fetched. Point it at a missing repo; tests that exercise it build their own repo.
    monkeypatch.setenv("COACH_FORK_REPO", str(tmp_path / "no-fork-repo"))
