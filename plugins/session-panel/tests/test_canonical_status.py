"""Contract: PanelModel consumes the published envelope, not surrounding prose or counts."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('panel_status_contract', Path(__file__).resolve().parents[1] / 'model.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def outcome(kind='done', stripe='done', revision=9):
    return {'kind': 'decision', 'status': {
        'status_version': 1, 'revision': revision, 'generation': 3,
        'identity': {'gateway': 'edge', 'profile': 'default', 'session_id': 'chat', 'owner_epoch': 'epoch'},
        'kind': kind, 'coverage': {'state': 'complete', 'reasons': []},
        'presentation': {'stripe': stripe, 'reason_codes': ['work_settled']}}}


def reduce(value, **extra):
    return model.reduce_panel({'outcome_json': value, 'tasks': [], 'done': [], 'cards': {}, **extra}, now=1000)


def test_published_status_and_revision_are_shared_by_panel_header():
    value = outcome()
    header = reduce(value)['header']
    assert header['state'] == 'done'
    assert header['status'] == value['status']
    assert header['label'] == 'done'


def test_no_legacy_or_row_count_reclassification():
    value = outcome('unresolved', 'attention')
    task = {'id': 't_run', 'title': 'Running card', 'status': 'running', 'gateway': 'edge', 'profile': 'default', 'board': 'default',
            'execution_observations': [{'identity': {'kind': 'card', 'work_id': 't_run'}, 'state': 'running'}]}
    header = reduce(value, tasks=[task])['header']
    assert header['state'] == 'unresolved'
    assert header['label'] != 'working'
    assert header['status']['presentation']['stripe'] == 'attention'


def test_missing_malformed_or_unsupported_protocol_is_unknown_never_legacy_done():
    for value in ({'kind': 'done'}, {'kind': 'done', 'status': None}, {'kind': 'done', 'status': {'status_version': 2}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'revision': -1}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'coverage': {'state': 'unknown', 'reasons': ['source_failed']}}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'kind': 'unresolved'}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'coverage': {'state': 'partial'}}}):
        header = reduce(value)['header']
        assert header['state'] == 'unknown'
        assert header['status'] is None


def test_status_revision_changes_panel_revision_without_touching_counts_or_actions():
    first, second = reduce(outcome(revision=9)), reduce(outcome(revision=10))
    assert first['revision'] != second['revision']
    assert first['sections'] == second['sections']
    assert first['header']['counts'] == second['header']['counts']
