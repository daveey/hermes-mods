"""Semantic oracle exercised with real SQL -> Feed -> serializer output.

Canonical expected rows come from an independent per-chat reader/reducer control
at the same pinned source state, never from the candidate wire payload.
"""
import copy
import json
from pathlib import Path

import pytest
from test_source_evidence import measured, graph, api, board
from test_source_analysis import A
from test_source_receipts import receipts


def expectations(rows):
    cases = {}
    for event in rows:
        if event['event'] not in ('emit_success', 'catchup_success'):
            continue
        for pub in event.get('publications', []):
            admission = pub['admission']
            identity = json.dumps([event['window_id'], admission['id'], admission['chat']], sort_keys=True)
            if identity in cases:
                continue
            section, row = rows.controls[pub['session']][0]
            cases[identity] = dict(window_id=event['window_id'], admission=copy.deepcopy(admission), rows=[dict(
                owner={k: row[k] for k in ('id', 'key', 'gateway', 'profile', 'board')},
                section=section, title=row['title'],
                status={'detail': row['detail']},
                actions=copy.deepcopy(row['actions'])), dict(
                owner={**{k: row[k] for k in ('gateway', 'profile', 'board')},
                       'id': 't_absent', 'key': row['key'].rsplit(':', 1)[0] + ':t_absent'},
                section=None)])
    assert cases
    return dict(schema='panel-semantic-expectations/1', cases=list(cases.values()))


def analyze(rows, expected=None):
    return A.analyze_records(rows, receipts=receipts(), expectations=expected)


def test_independent_canonical_expectation_passes_actual_wire(measured, tmp_path):
    manifest = expectations(measured)
    result = analyze(measured, manifest)
    (tmp_path / 'semantic-analysis.json').write_text(json.dumps(result, indent=2))
    (tmp_path / 'semantic-expectations.json').write_text(json.dumps(manifest, indent=2))
    if destination := __import__('os').environ.get('B3_EVIDENCE'):
        (Path(destination) / 'semantic-expectations.json').write_text(json.dumps(manifest, indent=2))
        (Path(destination) / 'semantic-analysis.json').write_text(json.dumps(result, indent=2))
        (Path(destination) / 'semantic-measured.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in measured))
    assert result['inputs_valid'], result
    assert result['semantic_oracle'] == 'PASS'
    assert result['episodes']['fulfilled'] > 0
    assert result['no_change_evidence']['matched'] > 0
    assert result['acceptance'] == A.NOT_EVALUATED


@pytest.mark.parametrize('kind', ['section', 'status', 'title', 'action', 'owner', 'absence', 'watch', 'age'])
def test_wrong_expected_semantics_retain_censored_age(measured, kind):
    manifest = expectations(measured)
    for case in manifest['cases']:
        row = case['rows'][0]
        if kind == 'section':
            row['section'] = 'stuck'
        elif kind == 'status':
            row['status']['detail'] = 'wrong status'
        elif kind == 'title':
            row['title'] = 'wrong title'
        elif kind == 'action':
            row['actions'][0]['target']['id'] = 'other'
        elif kind == 'owner':
            row['owner']['gateway'] = 'other'
        elif kind == 'absence':
            row['section'] = None
        elif kind == 'watch':
            case['admission']['watch_token'] = 'other'
        elif kind == 'age':
            case['admission']['first_admitted_mono_ns'] += 1
    result = analyze(measured, manifest)
    assert not result['inputs_valid'] and result['semantic_oracle'] == 'FAIL'
    assert result['episodes']['fulfilled'] == 0
    assert result['episodes']['unmatched'] > 0
    assert all(e['lower_bound_ns'] >= 0 for e in result['episodes']['unmatched_sample'])


@pytest.mark.parametrize('mutation', ['writer', 'source', 'projection_scope', 'projection_dependency',
    'drop', 'hash', 'epoch', 'source_owner', 'no_prior', 'wrong_prior', 'model_alias'])
def test_actual_trace_negative_controls_reject_without_fabricated_success(measured, mutation):
    rows = copy.deepcopy(measured)
    expected = expectations(measured)
    if mutation in ('writer', 'source'):
        rows = [r for r in rows if (not r['event'].startswith('writer_') if mutation == 'writer'
                                  else r['event'] != 'source_received')]
    elif mutation in ('projection_scope', 'projection_dependency'):
        for r in rows:
            if r['event'] == 'projection_end':
                r.pop('dependencies' if mutation == 'projection_dependency' else 'covered_scopes')
    elif mutation == 'drop':
        next(r for r in rows if r['event'] == 'window_end')['dropped_records'] = 1
    elif mutation == 'hash':
        next(r for r in rows if r['event'] == 'emit_success')['payload_sha256'] = '0' * 64
    elif mutation == 'epoch':
        for r in rows:
            if r['event'] == 'emit_success':
                for p in r.get('publications', []):
                    p['feed_epoch'] = 'other'
    elif mutation == 'source_owner':
        next(r for r in rows if r['event'] == 'source_received')['receipt']['owner_token'] = 'other'
    elif mutation in ('no_prior', 'wrong_prior'):
        # Keep the assertion of prior success but remove/corrupt its actual evidence.
        assert any(r.get('prior_successful_publication') for r in rows if r['event'] == 'no_change')
        if mutation == 'no_prior':
            rows = [r for r in rows if r['event'] != 'emit_success']
        else:
            for r in rows:
                if r['event'] == 'no_change' and r.get('prior_successful_publication'):
                    r['prior_successful_publication']['chat']['profile'] = 'other'
    elif mutation == 'model_alias':
        for r in rows:
            if r['event'] == 'no_change' and r.get('prior_successful_publication'):
                r['identical_model']['version'] = True
    result = analyze(rows, expected)
    assert not result['inputs_valid'], result
    if mutation in ('no_prior', 'wrong_prior', 'model_alias'):
        assert result['no_change_evidence']['invalid'] > 0
    if mutation in ('writer', 'source', 'projection_scope', 'projection_dependency', 'epoch'):
        assert result['episodes']['unmatched'] > 0


@pytest.mark.parametrize('manifest', [False, {}, {'schema': 'panel-semantic-expectations/1', 'cases': []}])
def test_malformed_manifest_cannot_pass(measured, manifest):
    result = analyze(measured, manifest)
    assert not result['inputs_valid'] and result['semantic_oracle'] == 'FAIL'


def test_missing_expectations_are_not_a_semantic_pass(measured):
    result = analyze(measured)
    assert result['inputs_valid'] and result['evidence_valid']
    assert result['semantic_oracle'] == A.NOT_EVALUATED


@pytest.mark.parametrize('mutation', ['regression', 'unrelated_coverage', 'off_list_group'])
def test_absence_needs_owner_coverage_and_cannot_regress(measured, mutation):
    import base64
    oracle_module = A.load_sibling('source_oracle.py')
    manifest = expectations(measured)
    # Only a real completed full-coverage payload supplies the positive.
    event = next(r for r in reversed(measured) if r['event'] == 'catchup_success')
    pub = event['publications'][0]
    body = json.loads(base64.b64decode(event['payload_b64']))['bundles'][pub['session']]
    case = next(c for c in manifest['cases'] if c['admission']['id'] == pub['admission']['id'])
    one = dict(schema=manifest['schema'], cases=[case])
    oracle = oracle_module.Oracle(one)
    assert oracle.check(event['window_id'], pub, body) is True
    if mutation == 'regression':
        body['model']['sections'][case['rows'][0]['section']][0]['title'] = 'wrong'
    elif mutation == 'unrelated_coverage':
        for source, demand in pub['covered_scopes']['entries']:
            source['gateway'] = 'unrelated'
            demand['refs'] = []
    else:
        absent = case['rows'][1]['owner']
        body['model']['groups'] = [{'rows': [{**absent, 'title': 'Forbidden off-list row'}]}]
    assert oracle.check(event['window_id'], pub, body) is False
    assert oracle.report()['verdict'] == 'FAIL'
