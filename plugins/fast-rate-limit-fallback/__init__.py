"""Local-only private-API workaround; not a catalog-compatible extension.

The classification hook cannot bypass the pool-availability gate while retaining
rate_limit semantics. Keep upstream recovery and routing intact; only adjust the
long-reset retry decision and the route-local availability hint. AST fingerprints
fail closed on drift, including same-signature behavioral changes.
"""
from contextvars import ContextVar
from functools import wraps
import ast
import hashlib
import importlib
import inspect
import json
import logging
from pathlib import Path
import textwrap

logger = logging.getLogger('hermes.plugins.fast-rate-limit-fallback')
_MARKER = '_fast_rate_limit_fallback'


def _fingerprint(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return hashlib.sha256(ast.dump(tree.body[0], include_attributes=False).encode()).hexdigest()


def _audited_recovery_base(function):
    """Recognize ONE audited account adapter, never generic __wrapped__ chains.

    These plugins own disjoint patch targets. This exception is only a guard
    input: fast fallback neither captures nor replaces the account wrapper.
    Verify the live code, owning installation, captured original and pinned
    adapter source. A copied marker/__wrapped__ cannot establish ownership.
    """
    import sys
    import types
    module = sys.modules.get('account_usage_selection')
    if module is None or not inspect.isfunction(function):
        raise ValueError('unrecognized recovery wrapper')
    path = Path(module.__file__).resolve()
    expected = json.loads(Path(__file__).with_name('account-usage-compatibility.json').read_text())
    source = path.read_bytes()
    if path.name != 'selection.py' or hashlib.sha256(source).hexdigest() != expected['selection_sha256']:
        raise ValueError('account recovery adapter changed; re-audit composition')
    def codes(code):
        yield code
        for child in code.co_consts:
            if isinstance(child, types.CodeType):
                yield from codes(child)
    compiled = compile(source, str(path), 'exec', dont_inherit=True)
    audited = next(c for c in codes(compiled)
                   if c.co_qualname == 'ExecutionIntegration.recovery_wrapper.<locals>.recover')
    if function.__code__ != audited or function.__globals__ is not vars(module):
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
    core = compile(core_path.read_bytes(), str(core_path), 'exec', dont_inherit=True)
    if not any(c == original.__code__ for c in codes(core)):
        raise ValueError('captured core recovery code changed')
    return original


def _warn(reason):
    if not getattr(logger, '_compatibility_warning_emitted', False):
        logger._compatibility_warning_emitted = True
        logger.warning('fast-rate-limit-fallback DISABLED: incompatible Hermes recovery API (%s). '
                       'Core behavior is unchanged. See ~/.hermes/local-fixes/fast-rate-limit-fallback/README.md', reason)


def register(ctx):
    """Install atomically after all guards pass; register reversible ownership."""
    try:
        helpers = importlib.import_module('agent.agent_runtime_helpers')
        recovery = importlib.import_module('agent.turn_recovery')
        api_error = importlib.import_module('agent.turn_api_error')
        runtime = importlib.import_module('run_agent')
        from agent.error_classifier import FailoverReason
        from agent.fallback_cooldown import _provider_reset_delay
        from agent.retry_utils import RETRY_AFTER_CAP_S
        from hermes_constants import get_hermes_home
        expected = json.loads(Path(__file__).with_name('compatibility.json').read_text())
        current = getattr(helpers, '_recover_rate_limit', None)
        existing = getattr(current, '_fast_rate_limit_state', None)
        if existing is not None:
            if not all(getattr(module, name) is wrapper for module, name, original, wrapper in existing['patches']):
                raise ValueError('partial installation or another plugin replaced a wrapper')
            _own(ctx, existing, str(get_hermes_home()))
            return
        for key, digest in expected.items():
            module_name, name = key.split(':')
            function = getattr(importlib.import_module(module_name), name)
            if key == 'agent.agent_runtime_helpers:recover_with_credential_pool' and hasattr(function, '__wrapped__'):
                function = _audited_recovery_base(function)
            if hasattr(function, '__wrapped__') or _fingerprint(function) != digest:
                raise ValueError(key + ' changed; re-audit before updating compatibility.json')
        if RETRY_AFTER_CAP_S != 600:
            raise ValueError('retry cutoff changed')
        if api_error.route_classified_error is not recovery.route_classified_error:
            raise ValueError('route alias is already overridden')
    except Exception as exc:
        _warn(str(exc))
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
    # Own cleanup before touching core so a failed registration leaves no patch.
    _own(ctx, state, str(get_hermes_home()))
    for module, name, original, wrapper in state['patches']:
        setattr(module, name, wrapper)
    logger.info('fast-rate-limit-fallback active: reset >600s rotates first, then eager fallback')


def _own(ctx, state, home):
    owner = id(ctx)
    if owner in state['owners']:
        return

    def unload():
        state['owners'].pop(owner, None)
        if not state['owners']:
            for module, name, original, wrapper in reversed(state['patches']):
                if getattr(module, name) is wrapper:
                    setattr(module, name, original)

    ctx.on_unload(unload)
    state['owners'][owner] = home
