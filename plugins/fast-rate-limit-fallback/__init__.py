"""Local-only private-API workaround; not a catalog-compatible extension.

The classification hook cannot bypass the pool-availability gate while retaining
rate_limit semantics. Keep upstream recovery and routing intact; only adjust the
long-reset retry decision and the route-local availability hint. AST fingerprints
fail closed on drift, including same-signature behavioral changes.

Load cost: register() runs on core's loader thread under plugins.load_timeout_seconds
(10 s), often while the main thread is still importing run_agent -> model_tools, which
discovers plugins. Importing run_agent from here would wait on that import lock until the
deadline fires (or pay a cold run_agent import under load). So register() installs at once
only when every seam module is already importable without waiting, and otherwise defers the
same atomic install to the first pre_api_request; every recovery decision follows an API
request. Guard fingerprints are cached per file digest (see _Fingerprints); a miss parses
or compiles as before, and that result stays authoritative.
"""
from contextvars import ContextVar
from functools import wraps
import ast
import hashlib
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import textwrap
import threading
import time

logger = logging.getLogger('hermes.plugins.fast-rate-limit-fallback')
_MARKER = '_fast_rate_limit_fallback'
_PLUGIN_DIR = Path(__file__).resolve().parent
# A load slower than this is logged with pid + process role (core's own timeout
# warning names neither), well before core's 10 s plugins.load_timeout_seconds.
_SLOW_LOAD_SECS = 4.0
_SEAMS = ('agent.agent_runtime_helpers', 'agent.turn_recovery', 'agent.turn_api_error', 'run_agent',
          'agent.error_classifier', 'agent.fallback_cooldown', 'agent.retry_utils')
_RECOVER_QUALNAME = 'ExecutionIntegration.recovery_wrapper.<locals>.recover'
_CACHE_VERSION = 1
_install_lock = threading.RLock()


def _process_role():
    if os.environ.get('HERMES_KANBAN_TASK'):
        return f"kanban-worker:{os.environ['HERMES_KANBAN_TASK']}"
    if os.environ.get('HERMES_DESKTOP'):
        return 'desktop-backend'
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    known = {'serve', 'gateway', 'dashboard', 'chat', 'cron', 'acp', 'mcp', 'kanban', 'tui'}
    return next((a for a in args if a in known), 'cli')


def _where():
    return f'pid={os.getpid()} role={_process_role()}'


class _Fingerprints:
    """$HERMES_HOME/cache/fast-rate-limit-fallback/code-fingerprints.json.

    Each entry is keyed by interpreter + file path + that file's sha256 (+ line/qualname), so
    a hit only says "this interpreter already parsed/compiled these exact bytes". A miss,
    corrupt file or unwritable cache costs a parse/compile, never a different verdict.
    """

    def __init__(self, home):
        self.path = Path(home) / 'cache' / 'fast-rate-limit-fallback' / 'code-fingerprints.json'
        try:
            files = json.loads(self.path.read_text()).get('files')
            self.files = files if isinstance(files, dict) else {}
        except (OSError, ValueError, AttributeError):
            self.files = {}
        self.digests = {}
        self.dirty = False

    def digest(self, path, data=None):
        path = str(path)
        if path not in self.digests:
            data = Path(path).read_bytes() if data is None else data
            self.digests[path] = hashlib.sha256(data).hexdigest()
        return self.digests[path]

    def key(self, kind, path, *extra):
        return '|'.join([str(_CACHE_VERSION), str(sys.implementation.cache_tag), sys.version, kind,
                         str(path), self.digest(path), *map(str, extra), ''])

    def get(self, key):
        return self.files.get(key)

    def put(self, key, value):
        self.files[key] = value
        self.dirty = True

    def save(self):
        # Merge with what other processes wrote meanwhile; drop entries for older bytes of
        # the files hashed in this load. A failed write only costs the next process a parse.
        if not self.dirty:
            return
        try:
            try:
                old = json.loads(self.path.read_text()).get('files', {})
            except (OSError, ValueError, AttributeError):
                old = {}
            merged = {**(old if isinstance(old, dict) else {}), **self.files}
            stale = [(f'|{p}|', f'|{p}|{d}|') for p, d in self.digests.items()]
            files = {k: v for k, v in merged.items()
                     if not any(name in k and current not in k for name, current in stale)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f'{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_text(json.dumps({'files': files}))
            os.replace(tmp, self.path)
            self.dirty = False
        except Exception:
            pass


def _fingerprint(function, cache=None):
    key = None
    if cache is not None:
        code = function.__code__
        try:
            key = cache.key('ast', Path(code.co_filename).resolve(), code.co_qualname, code.co_firstlineno)
        except OSError:
            key = None
        hit = cache.get(key) if key else None
        if isinstance(hit, str):
            return hit
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    digest = hashlib.sha256(ast.dump(tree.body[0], include_attributes=False).encode()).hexdigest()
    if key:
        cache.put(key, digest)
    return digest


def _codes(code):
    import types
    yield code
    for child in code.co_consts:
        if isinstance(child, types.CodeType):
            yield from _codes(child)


def _audited_recovery_base(function, cache=None):
    """Recognize ONE audited account adapter, never generic __wrapped__ chains.

    These plugins own disjoint patch targets. This exception is only a guard
    input: fast fallback neither captures nor replaces the account wrapper.
    Verify the live code, owning installation, captured original and pinned
    adapter source. A copied marker/__wrapped__ cannot establish ownership.
    """
    module = sys.modules.get('account_usage_selection')
    if module is None or not inspect.isfunction(function):
        raise ValueError('unrecognized recovery wrapper')
    path = Path(module.__file__).resolve()
    expected = json.loads((_PLUGIN_DIR / 'account-usage-compatibility.json').read_text())
    source = path.read_bytes()
    if path.name != 'selection.py' or hashlib.sha256(source).hexdigest() != expected['selection_sha256']:
        raise ValueError('account recovery adapter changed; re-audit composition')
    # account-usage's code fingerprint (from the selection.py pinned above): a process-
    # independent digest of everything code equality compares. Absent = always compile.
    fingerprint = getattr(module, '_code_fingerprint', None) if cache is not None else None
    known = key = None
    if fingerprint is not None:
        cache.digest(path, source)
        key = cache.key('codes', path, _RECOVER_QUALNAME)
        known = cache.get(key)
    if not (isinstance(known, list) and fingerprint(function.__code__) in known):
        compiled = compile(source, str(path), 'exec', dont_inherit=True)
        audited = next(c for c in _codes(compiled) if c.co_qualname == _RECOVER_QUALNAME)
        if key:
            cache.put(key, [fingerprint(audited)])
        if function.__code__ != audited:
            raise ValueError('foreign recovery wrapper')
    if function.__globals__ is not vars(module):
        raise ValueError('foreign recovery wrapper')
    closed = inspect.getclosurevars(function).nonlocals
    owner, original = getattr(function, '_account_usage_owner', None), closed.get('original')
    from agent import agent_runtime_helpers as helpers
    if (type(owner) is not module.ExecutionIntegration or not owner.active
            or not any(obj is helpers and name == 'recover_with_credential_pool'
                       and base is original and wrapper is function
                       for obj, name, base, wrapper in owner.patches)
            or getattr(function, '__wrapped__', None) is not original
            or not inspect.isfunction(original) or hasattr(original, '__wrapped__')):
        raise ValueError('unowned or nested recovery wrapper')
    # inspect.getsource alone would not catch a changed live __code__ that kept
    # the source location. Compare the captured core code to its source as well.
    core_path = Path(helpers.__file__).resolve()
    core_source = core_path.read_bytes()
    known = key = None
    if fingerprint is not None:
        cache.digest(core_path, core_source)
        key = cache.key('codes', core_path)
        known = cache.get(key)
    if not (isinstance(known, list) and fingerprint(original.__code__) in known):
        core = compile(core_source, str(core_path), 'exec', dont_inherit=True)
        if key:
            cache.put(key, sorted({fingerprint(c) for c in _codes(core)}))
        if not any(c == original.__code__ for c in _codes(core)):
            raise ValueError('captured core recovery code changed')
    return original


def _warn(reason):
    if not getattr(logger, '_compatibility_warning_emitted', False):
        logger._compatibility_warning_emitted = True
        logger.warning('fast-rate-limit-fallback DISABLED: incompatible Hermes recovery API (%s). '
                       'Core behavior is unchanged. See ~/.hermes/local-fixes/fast-rate-limit-fallback/README.md', reason)


def _seams_ready():
    """True when every seam can be imported on this thread without waiting on another
    thread's import lock: run_agent fully imported and no seam mid-initialization."""
    if 'run_agent' not in sys.modules:
        return False
    for name in _SEAMS:
        module = sys.modules.get(name)
        if module is not None and getattr(getattr(module, '__spec__', None), '_initializing', False):
            return False
    return True


def register(ctx):
    """Install atomically after all guards pass; register reversible ownership."""
    started = time.perf_counter()
    phases = {}

    def mark(phase, since=[started]):
        now = time.perf_counter()
        phases[phase] = round(now - since[0], 2)
        since[0] = now

    if getattr(ctx, '_load_abandoned', False):
        return _abandoned(None, started, phases)
    from hermes_constants import get_hermes_home
    owner = {'home': str(get_hermes_home()), 'state': None, 'cancelled': False, 'failed': False}
    if _seams_ready():
        # Own cleanup after the guards pass but before touching core, so a failed
        # registration leaves no patch and an incompatible core leaves no owner.
        _install(owner, mark, own=lambda: ctx.on_unload(_unloader(owner)))
    else:
        ctx.on_unload(_unloader(owner))  # also cancels a still-pending install
        ctx.register_hook('pre_api_request', _deferred_installer(owner))
        mark('deferred')
    if getattr(ctx, '_load_abandoned', False):
        # Core's deadline fired while register() ran: on_unload/hooks registered after it are
        # ignored, so nothing could ever remove these patches. Leave core behavior unchanged.
        return _abandoned(owner, started, phases)
    elapsed = time.perf_counter() - started
    if elapsed > _SLOW_LOAD_SECS:
        logger.warning('fast-rate-limit-fallback: slow plugin load %.1fs (%s) %s', elapsed, _where(),
                       ' '.join(f'{k}={v}s' for k, v in phases.items()))


def _abandoned(owner, started, phases):
    if owner is not None:
        _unloader(owner)()
    logger.warning("fast-rate-limit-fallback: load finished after core's deadline (%.1fs, %s) %s; "
                   'patches undone, core behavior unchanged in this process until a reload',
                   time.perf_counter() - started, _where(), ' '.join(f'{k}={v}s' for k, v in phases.items()))


def _deferred_installer(owner):
    def install_on_first_api_request(**_):
        if owner['state'] is None and not owner['cancelled'] and not owner['failed']:
            started = time.perf_counter()
            _install(owner, lambda phase: None)
            elapsed = time.perf_counter() - started
            if elapsed > _SLOW_LOAD_SECS:
                logger.warning('fast-rate-limit-fallback: slow deferred install %.1fs (%s)', elapsed, _where())
        return None
    return install_on_first_api_request


def _unloader(owner):
    def unload():
        with _install_lock:
            owner['cancelled'] = True
            state, owner['state'] = owner['state'], None
            if state is None:
                return
            state['owners'].pop(id(owner), None)
            if not state['owners']:
                for module, name, original, wrapper in reversed(state['patches']):
                    if getattr(module, name) is wrapper:
                        setattr(module, name, original)
    return unload


def _attach(owner, state):
    state['owners'][id(owner)] = owner['home']
    owner['state'] = state


def _install(owner, mark, own=None):
    with _install_lock:
        if owner['state'] is not None or owner['cancelled'] or owner['failed']:
            return
        cache = None
        try:
            helpers = importlib.import_module('agent.agent_runtime_helpers')
            recovery = importlib.import_module('agent.turn_recovery')
            api_error = importlib.import_module('agent.turn_api_error')
            runtime = importlib.import_module('run_agent')
            from agent.error_classifier import FailoverReason
            from agent.fallback_cooldown import _provider_reset_delay
            from agent.retry_utils import RETRY_AFTER_CAP_S
            from hermes_constants import get_hermes_home
            mark('imports')
            expected = json.loads((_PLUGIN_DIR / 'compatibility.json').read_text())
            current = getattr(helpers, '_recover_rate_limit', None)
            existing = getattr(current, '_fast_rate_limit_state', None)
            if existing is not None:
                if not all(getattr(module, name) is wrapper for module, name, original, wrapper in existing['patches']):
                    raise ValueError('partial installation or another plugin replaced a wrapper')
            else:
                cache = _Fingerprints(owner['home'])
                for key, digest in expected.items():
                    module_name, name = key.split(':')
                    function = getattr(importlib.import_module(module_name), name)
                    if key == 'agent.agent_runtime_helpers:recover_with_credential_pool' and hasattr(function, '__wrapped__'):
                        function = _audited_recovery_base(function, cache)
                    if hasattr(function, '__wrapped__') or _fingerprint(function, cache) != digest:
                        raise ValueError(key + ' changed; re-audit before updating compatibility.json')
                if RETRY_AFTER_CAP_S != 600:
                    raise ValueError('retry cutoff changed')
                if api_error.route_classified_error is not recovery.route_classified_error:
                    raise ValueError('route alias is already overridden')
            mark('guards')
        except Exception as exc:
            owner['failed'] = True
            _warn(str(exc))
            return
        finally:
            if cache is not None:
                cache.save()
        if own is not None:
            own()
        if existing is not None:
            _attach(owner, existing)
            return

        original_rate = helpers._recover_rate_limit
        original_route = recovery.route_classified_error
        original_gate = runtime._pool_may_recover_from_rate_limit
        bypass_pool = ContextVar('fast_rate_limit_fallback_pool', default=False)
        state = {'owners': {}, 'patches': []}

        def enabled():
            return str(get_hermes_home()) in state['owners'].values()

        def long_reset(context):
            delay = _provider_reset_delay(context.get('reset_at')) if isinstance(context, dict) else None
            return delay is not None and delay > RETRY_AFTER_CAP_S

        @wraps(original_rate)
        def rate(pool, *, has_retried_429, error_context, api_key_hint, credential_id, rotate_and_swap):
            # Enter upstream's existing rotate branch, retaining cooldown, identity,
            # endpoint and swap-veto behavior. Short/unknown resets are untouched.
            return original_rate(
                pool, has_retried_429=has_retried_429 or (enabled() and long_reset(error_context)),
                error_context=error_context, api_key_hint=api_key_hint,
                credential_id=credential_id, rotate_and_swap=rotate_and_swap,
            )

        @wraps(original_route)
        def route(agent, api_error, classified, _retry, **kwargs):
            bypass = (enabled() and classified.reason == FailoverReason.rate_limit
                      and not kwargs['recovered_with_pool'] and long_reset(kwargs['error_context']))
            token = bypass_pool.set(bypass)
            try:
                return original_route(agent, api_error, classified, _retry, **kwargs)
            finally:
                bypass_pool.reset(token)

        @wraps(original_gate)
        def gate(pool):
            # Context-local: never hide availability from concurrent turns or from
            # the credential rotation itself. No shared agent/pool mutation.
            return False if bypass_pool.get() else original_gate(pool)

        state['patches'] = [
            (helpers, '_recover_rate_limit', original_rate, rate),
            (recovery, 'route_classified_error', original_route, route),
            (api_error, 'route_classified_error', original_route, route),
            (runtime, '_pool_may_recover_from_rate_limit', original_gate, gate),
        ]
        for module, name, original, wrapper in state['patches']:
            setattr(wrapper, _MARKER, True)
            wrapper._fast_rate_limit_state = state
        _attach(owner, state)
        for module, name, original, wrapper in state['patches']:
            setattr(module, name, wrapper)
        mark('install')
        logger.info('fast-rate-limit-fallback active: reset >600s rotates first, then eager fallback')
