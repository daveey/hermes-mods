"""Cross-plugin callable contract; regex exports remain legacy compatibility APIs."""
import importlib.util
from pathlib import Path
import sys

import pytest

PLUGINS = Path(__file__).resolve().parents[2]


def load(relative):
    name = '_notice_categories_' + relative.replace('/', '_').replace('.', '_')
    spec = importlib.util.spec_from_file_location(name, PLUGINS / relative)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def predicates():
    return [getattr(load(path), fn) for path, fn in [
        ('coach/compute_envelope.py', 'runtime_notice'),
        ('coach/reply_check.py', 'notice_turn'),
        ('coach/reply_check.py', 'synthetic_user_turn'),
        ('coach/refusal_streak.py', 'automated'),
        ('coach/relay_repeat.py', '_automated'),
        ('coach/self_heal.py', 'synthetic'),
        ('skill-outcomes/outcomes.py', 'runtime_notice'),
        ('chat-closeout/core.py', 'runtime_notice'),
        ('kanban-honest-done/acceptance.py', 'runtime_notice'),
        ('follow-through/core.py', 'runtime_notice'),
        ('user-steps/core.py', '_runtime_message'),
    ]]


@pytest.mark.parametrize('text', [
    glyph + variation + space + '[new notice] do work'
    for glyph in ['⏳', '👀', '🛑', '⛔', 'ℹ', '☃', '🜁', '🧪']
    for variation in ['', '\ufe0f'] for space in ['', ' ', '\t\n']
] + ['[kanban] update', '[IMPORTANT: result', '[System note: result',
     '[chat-closeout] update', '[owner-wake] update'])
def test_runtime_envelopes(predicates, text):
    for predicate in predicates:
        assert predicate(text), (predicate.__module__, text)


@pytest.mark.parametrize('text', ['👍 yes', '✅ done', '⚠ careful, but go',
                                       'I saw ⏳ [board] in the log'])
def test_human_pictographs(predicates, text):
    for predicate in predicates:
        assert not predicate(text), (predicate.__module__, text)


def test_outcomes_callables_and_human_steer():
    mod = load('skill-outcomes/outcomes.py')
    assert mod.is_real_user_message('⚠ careful, but go')
    assert not mod.is_real_user_message('🧪 [new] notice')
    assert mod.is_real_user_message('[OUT-OF-BAND USER MESSAGE]\nyes\n[/OUT-OF-BAND USER MESSAGE]', 'steer')


def test_status_plain_uses_synthetic_user_predicate(monkeypatch):
    mod = load('coach/reply_check.py')
    seen = []
    monkeypatch.setattr(mod, 'synthetic_user_turn', lambda text: seen.append(text) or True)
    assert mod.status_plain_hint('how goes?') is None
    assert seen == ['how goes?']


def test_status_plain_category_and_human_warning():
    mod = load('coach/reply_check.py')
    assert mod.status_plain_hint('🧪\ufe0f [new] how goes?') is None
    assert mod.status_plain_hint('⚠ careful, but go; how goes?')
    assert not mod.synthetic_user_turn('[OUT-OF-BAND USER MESSAGE]\nhow goes?\n[/OUT-OF-BAND USER MESSAGE]')


def test_user_steps_runtime_kinds_and_exception():
    mod = load('user-steps/__init__.py')
    for kind in ['internal_notification', 'process_complete', 'async_delegation_complete',
                 'loop_tick', 'kanban_notice', 'auto_continue']:
        assert mod._turn_message('notice', [{'role': 'user', 'display_kind': kind}]).startswith(mod.AUTOMATIC_PREFIX)
    text = 'Canonical user decision. Proceed.'
    assert mod._turn_message(text, [{'role': 'user', 'content': text, 'display_kind': 'internal_notification'}]) == text
    assert mod._turn_message('yes', [{'role': 'user', 'display_kind': 'steer'}]) == 'yes'
    assert not load('user-steps/core.py')._runtime_message('[OUT-OF-BAND USER MESSAGE]\nyes\n[/OUT-OF-BAND USER MESSAGE]')
