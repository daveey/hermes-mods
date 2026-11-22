"""Legacy fixloop config/env must never reintroduce exact Kanban routing."""
import importlib.util
from pathlib import Path


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
