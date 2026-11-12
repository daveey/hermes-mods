import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

import pytest

from home_guard import HomeGuard
from collection_home import create as create_collection_home

# Collection is isolated too: imported modules may capture home paths before fixtures.
_LIVE_HOME = Path.home() / '.hermes'
_COLLECTION_HOME = create_collection_home()
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


# Finite core helpers that write under HERMES_HOME (not the infinite idle-reaper/takeover watchers).
_CORE_WRITER_THREADS = frozenset({'kanban-annotation-refresh', 'kanban-remote-links', 'kanban-annotation-prewarm'})


def pytest_configure(config):
    # Give pytest a known owned root before it creates its lock files/fixtures.
    if not config.option.basetemp:
        config.option.basetemp = str(Path(_COLLECTION_HOME.name) / 'pytest')
    _GUARD.allow(config.option.basetemp)
    config.addinivalue_line('markers', 'platforms(*specs, arch=None, arch_negate=False): run only on hosts '
                                       "matching a spec (core's tests/_fixtures/platform_gating.py)")


def pytest_collection_modifyitems(config, items):
    if _NO_CORE:
        for item in items:
            if f'{item.path.name}::{item.originalname}' in _CORE_ONLY_TESTS:
                item.add_marker(pytest.mark.skip(reason='needs Hermes core (none on the path; HERMES_CORE_CHECKOUT unset)'))
    # Core's own conftest applies platforms(...) gating, but scripts/test-all runs this suite with its own
    # rootdir, so only this conftest loads: Linux-only tests (core's http_readback) ran and failed on macOS.
    # Reuse core's gate rather than a copy that can drift from it.
    try:
        from tests._fixtures.platform_gating import _platforms_gate_reason
    except ImportError:  # no core on the path, or a core from before the fixture split
        return
    for item in items:
        reason = _platforms_gate_reason(item)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


# Core is private, so the public plugin-suites CI has none: a test module that imports core at module
# level skips there instead of erroring at collection. Only when no core is on the path and none was
# named (scripts/test-all sets HERMES_CORE_CHECKOUT whenever it puts core on PYTHONPATH); with a core
# named, a missing core module still fails loudly. Only core's own top-level modules count.
_NO_CORE = _core_spec is None and not os.environ.get('HERMES_CORE_CHECKOUT')
_CORE_TOPS = frozenset({'agent', 'acp_adapter', 'cron', 'gateway', 'model_tools', 'run_agent', 'tools',
                        'toolsets', 'hermes_platform', 'batch_runner', 'cli'})
_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")
# Tests that need core but never see its ImportError: the plugin swallows it (the judge/context path
# reports judge_or_context_error), core's websockets dependency is absent, or they read core's source or
# platform-gating fixture. They pass with core; skipped only when there is none (see _NO_CORE).
_CORE_ONLY_TESTS = frozenset({
    'test_goal_bounded_evidence.py::test_oversized_memory_is_bounded_and_stop_remains_human',
    'test_goal_bounded_evidence.py::test_restriction_after_large_tool_is_not_trimmed',
    'test_goal_capability_gate.py::test_current_boundary_is_logged',
    'test_goal_capability_gate.py::test_gate_does_not_certify_superseded_boundary',
    'test_goal_disposition.py::test_no_owner_adapter_never_certifies_ownership',
    'test_goal_process_serve.py::test_process_ownership_over_production_serve',
    'test_goal_restriction_gate.py::test_explicit_restriction_never_nudges_work',
    'test_goal_restriction_gate.py::test_new_repair_steering_supersedes_old_whole_turn_veto',
    'test_goal_restriction_gate.py::test_partial_quoted_or_ambiguous_scope_is_not_whole_turn_veto',
    'test_goal_serve_acceptance.py::test_production_serve_auth_and_next_turn',
    'test_goal_transport_controls.py::test_ws_reconnect_guarded_completion',
    'test_platform_marks.py::test_windows_only_mark_runs_only_on_windows',
    'test_reply_check.py::test_notice_turn_recognizes_every_prompt_producer_text',
    # calls the real register() inside a swallow-all try/except: without core it fails before wiring hooks.
    'test_root_cap_guard.py::test_register_wires_pre_and_post_callbacks',
})


def _core_module(name):
    parts = name.split('.')
    if parts[0] == 'tests' and len(parts) > 1:  # core's own tests package (tests.agent, tests.waiting, ...)
        parts = parts[1:]
        if not (_ROOT / 'tests' / parts[0]).exists() and not (_ROOT / 'tests' / f'{parts[0]}.py').exists():
            return True
    top = parts[0]
    return top in _CORE_TOPS or top.startswith('hermes_')


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    report = outcome.get_result()
    if not (_NO_CORE and report.failed and isinstance(collector, pytest.Module)):
        return
    missing = _MISSING_MODULE.findall(str(report.longrepr))
    if missing and all(_core_module(m) for m in missing):
        report.outcome = 'skipped'
        report.longrepr = (str(collector.path), 0, f'Skipped: needs Hermes core ({missing[-1]} not importable)')


def _missing_core(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ModuleNotFoundError) and exc.name and _core_module(exc.name):
            return exc.name
        exc = exc.__cause__ or exc.__context__
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # Same rule for a test (or fixture) that imports core at run time.
    outcome = yield
    report = outcome.get_result()
    if not (_NO_CORE and report.failed and call.excinfo is not None):
        return
    missing = _missing_core(call.excinfo.value)
    if missing:
        report.outcome = 'skipped'
        report.longrepr = (str(item.path), item.location[1] or 0, f'Skipped: needs Hermes core ({missing} not importable)')


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    # A client-auto-started `gateway run --idle-exit` can be homed at the pytest root itself (not a
    # per-test tmp_path), so the per-test reaper misses it. Its graceful exit writes
    # gateway_state.json/.clean_shutdown/logs while TemporaryDirectory cleanup rmtree()s the root
    # (OSError 39 at interpreter exit). Stop it, and wait, before cleanup.
    from gateway_reaper import stop_gateways_under
    stop_gateways_under(_COLLECTION_HOME.name)
    # Core's finite fire-and-forget helpers (daemon threads) persist origin-work.db under HERMES_HOME; one still
    # running at teardown recreates a file mid-rmtree (OSError 39). Wait for them (bounded) before cleanup.
    # A sibling plugin's waker loop started outside any test (collection imports) is stopped the same way.
    writers = [t for t in threading.enumerate() if t.name in _CORE_WRITER_THREADS or _plugin_loop_stop(t) is not None]
    _stop_plugin_loops(writers)
    for thread in writers:
        thread.join(timeout=10)
    remaining = [t.name for t in writers if t.is_alive()]
    if remaining:
        raise RuntimeError(f'core background writers outlived collection: {remaining}')
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
        if thread.name in _CORE_WRITER_THREADS or (code and (
                Path(code.co_filename).resolve().parent == _ROOT / 'plugins/coach'
                or code.co_name == '_lesson_bg' or code.co_name.startswith('_selfheal_'))):
            threads.append(thread)
        elif _plugin_loop_stop(thread) is not None:
            threads.append(thread)
        return start(thread, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, 'start', tracked)
    yield
    _stop_plugin_loops(threads)
    for thread in threads:
        thread.join(timeout=10)
    assert not [t.name for t in threads if t.is_alive()], 'coach background thread outlived its test'


def _plugin_loop_stop(thread):
    """Stop event of a sibling plugin's waker loop (``Thread(target=_loop, args=(stop_event,))``), else None.

    Tests that load real plugins through the plugin manager (chat-closeout via the goal acceptance
    fixtures) start that plugin's infinite daemon loop; ``manager.unload()`` does not stop it. Left
    running, it resolves HERMES_HOME per tick, so after the test it writes cache/chat-closeout/ledger.db
    and origin-work.db into the collection home while TemporaryDirectory cleanup rmtree()s it (OSError 39
    after the summary). The loop's own reload path stops stale copies via the same first-arg event.
    """
    code = getattr(getattr(thread, '_target', None), '__code__', None)
    args = getattr(thread, '_args', None) or ()
    if not (code and args and isinstance(args[0], threading.Event)):
        return None
    path = Path(code.co_filename).resolve()
    plugins = _ROOT / 'plugins'
    if not path.is_relative_to(plugins) or path.is_relative_to(plugins / 'coach'):
        return None
    return args[0]


def _stop_plugin_loops(threads):
    for thread in threads:
        stop = _plugin_loop_stop(thread)
        if stop is not None:
            stop.set()

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
    # Same for hermes-mods (session_commit_fix and selfheal_late_adopt also read ~/code/hermes-mods).
    monkeypatch.setenv("COACH_MODS_REPO", str(tmp_path / "no-mods-repo"))


BOARD_GATEWAY = "gw-board-a"


@pytest.fixture
def board_identity(monkeypatch):
    """Core's own enrollment answers for the home machine pin: a synced board whose configured gateway is
    ``BOARD_GATEWAY``. Replaced (not read from the real host config), so no test depends on where it runs.
    ``board_identity.set(synced=..., gateway=..., error=...)`` re-points it; the default is synced + named."""
    from types import SimpleNamespace
    pytest.importorskip("hermes_cli.kanban_db_sync")
    from hermes_cli import kanban_db_sync, kanban_sync_client
    state = {"synced": True, "gateway": BOARD_GATEWAY, "error": None}

    def load_identity(*_a, **_k):
        if state["error"]:
            raise kanban_sync_client.SyncUnavailable(state["error"])
        return SimpleNamespace(url="https://example.com", key="k", gateway=state["gateway"])

    monkeypatch.setattr(kanban_db_sync, "sync_enabled", lambda: state["synced"])
    monkeypatch.setattr(kanban_sync_client, "load_identity", load_identity)
    return SimpleNamespace(gateway=BOARD_GATEWAY, set=lambda **kw: state.update(kw), state=state)
