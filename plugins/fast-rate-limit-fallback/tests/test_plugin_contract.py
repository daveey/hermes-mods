from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import logging

import pytest


def seams():
    import agent.agent_runtime_helpers as helpers
    import agent.turn_recovery as recovery
    import agent.turn_api_error as api_error
    import run_agent
    return [(helpers, '_recover_rate_limit'), (recovery, 'route_classified_error'),
            (api_error, 'route_classified_error'), (run_agent, '_pool_may_recover_from_rate_limit')]


def test_discovery_reload_and_unload_are_idempotent(loaded_local_plugin):
    manager = loaded_local_plugin
    first = [getattr(m, n) for m, n in seams()]
    manager.discover_and_load()
    assert [getattr(m, n) for m, n in seams()] == first
    manager.discover_and_load(force=True)
    for m, n in seams():
        function = getattr(m, n)
        assert function._fast_rate_limit_fallback
        assert not getattr(function.__wrapped__, '_fast_rate_limit_fallback', False)
    manager.unload()
    assert all(not getattr(getattr(m, n), '_fast_rate_limit_fallback', False) for m, n in seams())


@pytest.mark.parametrize('drift', ['missing', 'signature', 'body', 'alias', 'cutoff'])
def test_incompatible_core_warns_and_leaves_all_seams_alone(loaded_local_plugin, monkeypatch, caplog, drift):
    module = loaded_local_plugin._plugins['fast-rate-limit-fallback'].module
    loaded_local_plugin.unload()
    import agent.agent_runtime_helpers as helpers
    import agent.turn_api_error as api_error
    import agent.retry_utils as retry_utils
    if drift == 'missing':
        monkeypatch.delattr(helpers, '_recover_rate_limit')
    elif drift == 'signature':
        monkeypatch.setattr(helpers, '_recover_rate_limit', lambda new_required: None)
    elif drift == 'body':
        def changed(pool, *, has_retried_429, error_context, api_key_hint, credential_id, rotate_and_swap):
            return False, False
        monkeypatch.setattr(helpers, '_recover_rate_limit', changed)
    elif drift == 'alias':
        monkeypatch.setattr(api_error, 'route_classified_error', lambda *a, **k: None)
    else:
        monkeypatch.setattr(retry_utils, 'RETRY_AFTER_CAP_S', 601)
    before = [getattr(m, n, None) for m, n in seams()]
    monkeypatch.setattr(module.logger, '_compatibility_warning_emitted', False, raising=False)
    ctx = SimpleNamespace(on_unload=MagicMock())
    with caplog.at_level(logging.WARNING):
        module.register(ctx)
        module.register(ctx)
    assert [getattr(m, n, None) for m, n in seams()] == before
    assert sum('fast-rate-limit-fallback DISABLED' in r.message for r in caplog.records) == 1
    ctx.on_unload.assert_not_called()


@pytest.mark.parametrize('delay, rotated', [(599, False), (600, False), (601, True)])
def test_exact_cutoff(delay, rotated):
    import agent.agent_runtime_helpers as helpers
    pool = MagicMock()
    pool.entries.return_value = []
    pool.current.return_value = None
    rotate = MagicMock(return_value=True)
    with patch('agent.fallback_cooldown.time.time', return_value=2_000_000_000):
        result = helpers._recover_rate_limit(pool, has_retried_429=False,
            error_context={'reset_at': 2_000_000_000 + delay}, api_key_hint=None,
            credential_id=None, rotate_and_swap=rotate)
    assert result == ((True, False) if rotated else (False, True))
    assert rotate.call_count == int(rotated)


def test_other_profile_keeps_original_transient_policy(monkeypatch, tmp_path):
    import agent.agent_runtime_helpers as helpers
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    rotate = MagicMock(return_value=True)
    pool = MagicMock()
    pool.current.return_value = None
    token = set_hermes_home_override(tmp_path / 'another-profile')
    try:
        result = helpers._recover_rate_limit(pool, has_retried_429=False,
            error_context={'reset_at': 4_000_000_000}, api_key_hint=None,
            credential_id=None, rotate_and_swap=rotate)
    finally:
        reset_hermes_home_override(token)
    assert result == (False, True)
    rotate.assert_not_called()
