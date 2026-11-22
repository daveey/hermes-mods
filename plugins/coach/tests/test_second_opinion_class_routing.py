"""Legacy fixloop config/env must never reintroduce exact Kanban routing."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import pytest


def aux_modules(monkeypatch, config, call):
    aux = ModuleType('agent.auxiliary_client')
    aux.call_llm = call
    cfg = ModuleType('hermes_cli.config')
    cfg.load_config_readonly = lambda: config
    monkeypatch.setitem(sys.modules, 'agent.auxiliary_client', aux)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', cfg)


@pytest.mark.parametrize('task_config', [
    {'provider': 'configured-provider'}, {'base_url': 'https://example.com/v1'},
    {'api_key': 'fixture'}, {'api_mode': 'responses'}, {'key_env': 'FIXTURE_KEY'},
    {'api_key_env': 'FIXTURE_KEY'}, {'model': 'configured-model'},
])
def test_aux_respects_specialized_configuration(monkeypatch, task_config):
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    aux_modules(monkeypatch, {'auxiliary': {'coach_diff_review': task_config}}, call)
    assert load().aux_review('system', 'prompt') == '{}'
    assert 'model' not in calls[0]


def test_aux_default_requests_deep_class(monkeypatch):
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=' {} '))])
    aux_modules(monkeypatch, {}, call)
    assert load().aux_review('system', 'prompt') == '{}'
    assert calls[0]['model'] == 'class:deep'
    assert 'provider' not in calls[0]


@pytest.mark.parametrize('provider_only', [False, True])
def test_aux_through_real_class_boundary(monkeypatch, provider_only):
    aux = pytest.importorskip('agent.auxiliary_client')
    boundary = pytest.importorskip('agent.auxiliary_class_routing')
    managed = pytest.importorskip('agent.model_routing_managed')
    effective = pytest.importorskip('hermes_cli.config_effective')
    routing = pytest.importorskip('agent.model_routing')
    cfg = pytest.importorskip('hermes_cli.config')
    task_config = {'provider': 'configured-provider'} if provider_only else {}
    monkeypatch.setattr(cfg, 'load_config_readonly', lambda: {
        'auxiliary': {'coach_diff_review': task_config}})
    monkeypatch.setattr(managed, 'managed_config', lambda: None)
    monkeypatch.setattr(effective, 'load_user_config_effective', lambda: {'model_routing': {'enabled': False}})
    monkeypatch.setattr(aux, '_get_auxiliary_task_config', lambda task: task_config)
    monkeypatch.setattr(aux, '_normalize_main_runtime', lambda runtime: {})
    monkeypatch.setattr(aux, '_read_main_model', lambda: None)
    monkeypatch.setattr(aux, '_resolve_task_provider_model', lambda task, provider, model, base, key:
                        (task_config.get('provider'), model, None, None, None))
    calls = []
    def legacy(task, **options):
        calls.append(options)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    monkeypatch.setattr(aux, 'call_llm', boundary.class_routed_sync(legacy))
    if provider_only:
        assert load().aux_review('system', 'prompt') == '{}'
        assert len(calls) == 1 and 'model' not in calls[0]
    else:
        with pytest.raises(routing.ModelRoutingError, match='routing is disabled'):
            load().aux_review('system', 'prompt')
        assert not calls


def test_aux_routing_error_is_not_retried(monkeypatch):
    routing = pytest.importorskip('agent.model_routing')
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        raise routing.ModelRoutingError('Model class routing is disabled')
    aux_modules(monkeypatch, {}, call)
    with pytest.raises(routing.ModelRoutingError, match='routing is disabled'):
        load().aux_review('system', 'prompt')
    assert len(calls) == 1


def load():
    spec = importlib.util.spec_from_file_location('coach_class_opinion', Path(__file__).resolve().parents[1] / '__init__.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._load('second_opinion')


def test_fixloop_ignores_legacy_config_and_env(monkeypatch):
    so = load()
    monkeypatch.setenv('COACH_FIXLOOP_REVIEW_MODEL', 'legacy-concrete')
    monkeypatch.setenv('COACH_FIXLOOP_REVIEW_PROVIDER', 'legacy-provider')
    args = so.card_args('s', [], settings={'fixloop_review_model': 'another-concrete',
                                          'fixloop_review_provider': 'another-provider'})
    assert args['model'] == 'class:deep'
    assert 'provider' not in args and 'pin_mandatory' not in args


def test_fixloop_hint_describes_class_not_concrete_pin(monkeypatch):
    so = load()
    monkeypatch.setenv('COACH_FIXLOOP_REVIEW_MODEL', 'legacy-concrete')
    monkeypatch.setenv('COACH_FIXLOOP_REVIEW', '1')
    hint = so.fixloop_review({'fails': [1]}, 's', 1, {'n': 2}, 'broken',
                            create=lambda args: {'task_id': 't_class'})
    assert 'class:deep' in hint
    assert 'legacy-concrete' not in hint and 'pinned to' not in hint
