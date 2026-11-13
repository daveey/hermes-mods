import functools
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
# future_clock reads this pinned clock: 2026-11-11 23:58 PST (07:58 UTC), when every incident time quoted in the
# suite's fixtures is past (reply_check._wall_now; test_future_clock passes its own instants).
os.environ['COACH_CLOCK_EPOCH'] = '1794470280'


# Finite core helpers that write under HERMES_HOME (not the infinite idle-reaper/takeover watchers).
_CORE_WRITER_THREADS = frozenset({'kanban-annotation-refresh', 'kanban-remote-links', 'kanban-annotation-prewarm'})
# Core workers that never exit (a process-lifetime daemon parked on a condition when idle): joining one always times
# out, so drain its queue instead. Thread name -> (module, attribute holding an object with a ``busy`` property).
_CORE_IDLE_WORKERS = {'kanban-remote-links': ('hermes_cli.kanban_session_activity', '_links_refresher')}


def _drain_idle_core_workers(timeout):
    """Wait (bounded) until each live process-lifetime core worker has no queued or running job; return those threads.

    core's ``ScopeRefresher`` (thread ``kanban-remote-links``) stays alive parked on its condition after its last
    refresh, so it cannot be joined. Idle, it writes nothing. Only the refresher's own worker thread is returned (any
    other thread with that name is still joined); a still-busy one is left to the join below and reported.
    """
    import time
    idle = set()
    deadline = time.monotonic() + timeout
    for module_name, attr in _CORE_IDLE_WORKERS.values():
        refresher = getattr(sys.modules.get(module_name), attr, None)
        worker = getattr(refresher, '_worker', None)
        if refresher is None or worker is None or not hasattr(refresher, 'busy'):
            continue  # never started, or an older core whose worker was finite: the join handles it
        while refresher.busy and time.monotonic() < deadline:
            time.sleep(0.05)
        if not refresher.busy:
            idle.add(worker)
    return idle


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
    idle_workers = _drain_idle_core_workers(timeout=10)
    writers = [t for t in writers if t not in idle_workers]
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
    idle_workers = _drain_idle_core_workers(timeout=10)
    threads = [t for t in threads if t not in idle_workers]
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


# Core keeps process-wide per-file handles that are bounded (or deliberately never closed) for a gateway watching a
# few homes: read-only SQLite readers (kanban_session_activity._RO_CACHE up to 32, notification_idle._SIGS up to 64
# pinning a reader, kanban_replica_observer up to 16 observers; ~3 fds each with WAL), one O_RDONLY header-probe fd per
# state.db path (hermes_state_dbfile._HEADER_PROBE_FDS, never closed in production: closing it while SQLite owns the
# inode would cancel its POSIX locks), and routed per-profile log files (hermes_logging._ProfileRoutingFileHandler).
# Here every test has its own tmp home, so they pile up: the hub's coach run reached ~1000 fds before
# test_root_cap_topic_lexicon and ~20 tests then failed with EMFILE (soft RLIMIT_NOFILE 1024), a different set every
# run. Release them after each test, readers first, then this test's routed logs and probe fds (only paths under its
# tmp_path, as core's own private_db_probe_cleanup does). Only modules a test already imported are touched.
_CORE_READER_RESETS = (
    ("tui_gateway.notification_idle", "reset_board_signatures"),
    ("hermes_cli.kanban_reader_retention", "close_all"),
    ("tui_gateway.kanban_replica_observer", "reset"),
)


def _under(path, roots):
    try:
        return any(Path(path).is_relative_to(root) for root in roots)
    except (TypeError, ValueError):
        return False


def _release_test_home_logs(logging_mod, root):
    release = getattr(logging_mod, "release_profile_log_handlers", None)
    if release is None:
        return
    homes = set()
    for router in list(getattr(logging_mod, "_queued_file_handlers", ())):
        homes.update(h for h in list(getattr(router, "_profile_handlers", {})) if _under(h, root))
    for home in homes:
        release(home)


def _close_test_probe_fds(dbfile, root):
    lock = getattr(dbfile, "_HEADER_PROBE_LOCK", None)
    probes = getattr(dbfile, "_HEADER_PROBE_FDS", None)
    if lock is None or probes is None:
        return
    with lock:
        for key in [k for k in probes if _under(k, root)]:
            fd = probes.pop(key)[0]
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def _release_core_handles(tmp_path):
    yield
    for module, name in _CORE_READER_RESETS:
        reset = getattr(sys.modules.get(module), name, None)
        if reset is not None:
            try:
                reset()
            except Exception:  # a reader still borrowed by a straggler is left for its owner; never fail teardown
                pass
    root = {tmp_path, tmp_path.resolve()}
    for module, release in (("hermes_logging", _release_test_home_logs), ("hermes_state_dbfile", _close_test_probe_fds)):
        if module in sys.modules:
            try:
                release(sys.modules[module], root)
            except Exception:
                pass

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
def _close_core_reader_caches(_join_coach_threads, tmp_path_factory):
    # Teardown runs after _join_coach_threads: a joined poller can no longer reopen a reader.
    """Close core's per-path SQLite reader caches after each test.

    Core keeps one cached read-only connection per board path (kanban_session_activity._RO_CACHE, unbounded), up
    to 16 replica observers (tui_gateway.kanban_replica_observer), and one never-closed lock-safe pread fd per
    database file (sqlite_safe_read._PREAD_FDS; hermes_state_dbfile._HEADER_PROBE_FDS, opened by every SessionDB()).
    Production has a
    handful of boards; this suite makes a fresh kanban.db/state.db per test and pytest keeps every test dir until
    the session ends, so the caches pinned ~10 fds (db, -wal, -shm) per test and the serial run hit EMFILE ('Too
    many open files') near the 6,000th test under a 1024-fd limit (hub gate, 25515f06c15b and 91aa39d4f314).
    Only modules a test already imported are reset (importing them here would cost every test), and only pread
    fds on files under this session's basetemp are closed: the test is over, so dropping its POSIX locks is moot.
    """
    yield
    activity = sys.modules.get('hermes_cli.kanban_session_activity')
    if activity is not None and hasattr(activity, '_reset_cache_for_tests'):
        activity._reset_cache_for_tests()
    observer = sys.modules.get('tui_gateway.kanban_replica_observer')
    if observer is not None and hasattr(observer, 'reset'):
        observer.reset()
    safe_read = sys.modules.get('hermes_cli.sqlite_safe_read')
    probes = sys.modules.get('hermes_state_dbfile')
    base = str(tmp_path_factory.getbasetemp().resolve()) + os.sep
    for module, cache, guard in ((safe_read, '_PREAD_FDS', '_PREAD_LOCK'),
                                 (probes, '_HEADER_PROBE_FDS', '_HEADER_PROBE_LOCK')):
        fds, lock = getattr(module, cache, None), getattr(module, guard, None)
        if not isinstance(fds, dict) or lock is None:
            continue
        with lock:
            for key in [k for k in fds if os.path.realpath(k).startswith(base)]:
                try:
                    os.close(fds.pop(key)[0])
                except OSError:
                    pass


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
    ``board_identity.set(synced=..., gateway=..., error=...)`` re-points it; the default is synced + named.

    Without core (``--no-core`` CI) there is nothing to patch in core, and ``placement_evidence._core_identity``
    would answer (False, None) itself. Its one seam is then replaced on every loaded copy of placement_evidence.py
    (package import, self_heal's spec-loaded copy, the tests' own ``pe`` copies), so the same synced default holds."""
    from types import SimpleNamespace
    state = {"synced": True, "gateway": BOARD_GATEWAY, "error": None}
    handle = SimpleNamespace(gateway=BOARD_GATEWAY, set=lambda **kw: state.update(kw), state=state)
    try:
        from hermes_cli import kanban_db_sync, kanban_sync_client
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_cli":  # core is there but broken: fail loudly, never fall back silently
            raise
        def core_identity():  # the same order as core: unsynced wins, then a failed identity read raises
            if not state["synced"]:
                return False, None
            if state["error"]:
                raise RuntimeError(state["error"])
            return True, state["gateway"]
        for mod in _placement_evidence_copies():
            monkeypatch.setattr(mod, "_core_identity", core_identity)
        return handle

    def load_identity(*_a, **_k):
        if state["error"]:
            raise kanban_sync_client.SyncUnavailable(state["error"])
        return SimpleNamespace(url="https://example.com", key="k", gateway=state["gateway"])

    monkeypatch.setattr(kanban_db_sync, "sync_enabled", lambda: state["synced"])
    monkeypatch.setattr(kanban_sync_client, "load_identity", load_identity)
    return handle


@functools.lru_cache(maxsize=None)
def _realpath(path):
    return os.path.realpath(path)


def _placement_evidence_copies():
    """Every loaded module object executed from coach's placement_evidence.py. Spec-loaded copies are often not in
    sys.modules (self_heal's ``_placement_evidence`` fallback), so coach-owned modules' attributes are searched too."""
    import types
    root = os.path.realpath(Path(__file__).resolve().parents[1])
    target = os.path.join(root, "placement_evidence.py")

    def path_of(mod):
        f = getattr(mod, "__file__", None)
        return _realpath(f) if isinstance(f, str) else None

    found = {}
    for mod in list(sys.modules.values()):
        if not isinstance(mod, types.ModuleType):
            continue
        p = path_of(mod)
        if p == target:
            found[id(mod)] = mod
        if not p or not p.startswith(root + os.sep):
            continue
        for val in list(vars(mod).values()):
            if isinstance(val, types.ModuleType) and path_of(val) == target:
                found[id(val)] = val
    return list(found.values())
