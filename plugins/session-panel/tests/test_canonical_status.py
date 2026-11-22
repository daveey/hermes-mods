"""Contract: PanelModel consumes the published envelope, not surrounding prose or counts."""
import importlib.util
import json
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


def test_missing_malformed_or_unsupported_protocol_is_derived_never_legacy_done():
    for value in ({'kind': 'done'}, {'kind': 'done', 'status': None}, {'kind': 'done', 'status': {'status_version': 2}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'revision': -1}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'coverage': {'state': 'unknown', 'reasons': ['source_failed']}}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'kind': 'unresolved'}},
                  {'kind': 'done', 'status': {**outcome()['status'], 'coverage': {'state': 'partial'}}}):
        header = reduce(value)['header']
        assert header['source'] == 'derived' and header['state'] == 'idle'
        assert header['status'] is None


def test_integer_valued_json_numbers_match_desktop_wire_validation():
    for key, value in (('status_version', 1.0), ('revision', 9.0), ('generation', 3.0)):
        wire = outcome()
        wire['status'][key] = value
        header = reduce(json.loads(json.dumps(wire)))['header']
        assert header['state'] == 'done', key
        assert header['status'] == wire['status'], key


def test_invalid_json_numbers_never_publish_done():
    for key in ('status_version', 'revision', 'generation'):
        for value in (True, False, '1', -1, 1.5, float('inf'), float('nan'), 9007199254740992):
            wire = outcome()
            wire['status'][key] = value
            assert reduce(wire)['header']['status'] is None, (key, value)


def test_status_revision_changes_panel_revision_without_touching_counts_or_actions():
    first, second = reduce(outcome(revision=9)), reduce(outcome(revision=10))
    assert first['revision'] != second['revision']
    assert first['sections'] == second['sections']
    assert first['header']['counts'] == second['header']['counts']


def test_nested_wire_objects_are_not_decoded_from_strings_into_canonical_status():
    for field in ('identity', 'coverage', 'presentation'):
        value = outcome()
        value['status'][field] = json.dumps(value['status'][field])
        header = reduce(value)['header']
        assert header['source'] == 'derived' and header['state'] == 'idle', field
        assert header['status'] is None, field
