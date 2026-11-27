"""Controls and refusal-branch coverage for the source-review corrections."""
import math

import pytest

from test_source_review_regressions import OWN, OTHER, board, consent, core, plugin, stop


@pytest.mark.parametrize('tool,key', [('terminal', 'command'), ('process_manage', 'data'), ('run_in_user_terminal', 'command')])
def test_semantic_consent_applies_to_all_shell_tools(tmp_path, tool, key):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    args = {key: 'hermes kanban comment t_1234""5678 retry'}
    assert consent.check_relay('tool_call', {'calls': [{'name': tool, 'arguments': args}]}, ['chat-a'], env)


@pytest.mark.parametrize('observed,extra', [
    ('31%', {}), ('31%', {'threshold': '25%', 'comparator': '>='}),
    ('31%', {'threshold': '>= 25%'}),
])
def test_body_bound_numeric_positive_control(tmp_path, observed, extra):
    db = board(tmp_path, 'ACCEPTANCE disk: >= 25%')
    md = {'acceptance_results': [dict(check='disk', status='pass', observed=observed, **extra)]}
    assert plugin.classify('kanban_complete', {'summary': 'Finished.', 'metadata': md}, OWN, lambda: [db]) is None


@pytest.mark.parametrize('body,observed,extra', [
    ('ACCEPTANCE disk: >= 25%', '31%', {'threshold': '5%', 'comparator': '>='}),
    ('ACCEPTANCE disk: >= 25%', '31%', {'comparator': '<='}),
    ('ACCEPTANCE disk: >= 25%', '31 GB', {}),
    ('ACCEPTANCE disk: >= 25%', 'CI success', {}),
    ('ACCEPTANCE disk: >= 25%\nACCEPTANCE disk: >= 5%', '31%', {}),
    ('ACCEPTANCE disk: >= 25%', '6.1%', {}),
    ('ACCEPTANCE disk: >= 25%', math.inf, {}),
    ('ACCEPTANCE disk: >= 25%', math.nan, {}),
    ('ACCEPTANCE disk: >= 25%', 10 ** 400, {}),
    ('ACCEPTANCE check 14: >= 25%', '31%', {}),
    ('Run disk check.', '6.1% free.', {}),
    ('Run disk check.', True, {}),
    ('Run disk check.', 31, {}),
])
def test_bound_threshold_negative_controls(tmp_path, body, observed, extra):
    db = board(tmp_path, body)
    check = 'check 4' if 'check 14' in body else 'disk'
    md = {'acceptance_results': [dict(check=check, status='pass', observed=observed, **extra)]}
    assert plugin.classify('kanban_request_review', {'summary': 'Finished.', 'metadata': md}, OWN, lambda: [db])


def test_literal_other_card_and_priority_edit_are_not_own_scope():
    assert core.check_shell(f'hermes kanban edit {OTHER} --title updated', OWN, lambda: []) is None
    assert core.check_shell(f'hermes kanban edit {OWN} --priority 5', OWN, lambda: []) is None


def test_bound_check_named_by_label_only(tmp_path):
    db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
    md = {'acceptance_results': [{'check': 'check 4 (disk)', 'status': 'pass', 'observed': '31%'}]}
    assert plugin.classify('kanban_complete', {'summary': 'Finished.', 'metadata': md}, OWN, lambda: [db]) is None
