"""Offline exact admission custody; synthetic records are not live inclusion proof."""
import copy

import pytest

from test_source_analysis import Log, SEC, analyze


@pytest.mark.parametrize('via', ['emit', 'catchup'])
@pytest.mark.parametrize('anchor', ['receiptless', 'receipt', 'successor'])
@pytest.mark.parametrize('field', ['watch_token', 'first_admitted_mono_ns', 'first_admitted_wall_ns'])
def test_conflicting_publication_endpoint_retains_unserved_age(via, anchor, field):
    log = Log()
    original = log.admit('initial', ['r1'] if anchor == 'receipt' else [], SEC)
    original.update(watch_token='first-watch', first_admitted_wall_ns=100)
    endpoint = original
    if anchor == 'successor':
        endpoint = log.admit('next', ['r2'], 2 * SEC)
        endpoint.update(watch_token='next-watch', first_admitted_wall_ns=200)
        log.add('superseded', 2 * SEC, predecessor_admission_id='initial', successor_admission_id='next')
    published = copy.deepcopy(endpoint)
    published[field] = 'other-watch' if field == 'watch_token' else endpoint[field] + 1
    pub, body = log.store(published, 3 * SEC)
    log.emit(pub, body, 4 * SEC, via=via)
    log.close(30 * SEC)
    result = analyze(log)
    assert not result['inputs_valid']
    assert result['integrity_errors']
    assert result['events']['fulfilled'] == result['episodes']['fulfilled'] == 0
    assert result['episodes']['unmatched'] == 1
    assert result['episodes']['unmatched_sample'][0]['start'] == SEC
    assert result['episodes']['unmatched_sample'][0]['lower_bound_ns'] == 29 * SEC
    assert result['failures_over_budget'] == 1


@pytest.mark.parametrize('via', ['emit', 'catchup'])
@pytest.mark.parametrize('mode', ['unchanged', 'evolving', 'legacy_admitted', 'legacy_publication',
                                 'later_known', 'successor'])
def test_compatible_publication_admission_custody(via, mode):
    log = Log()
    original = log.admit('initial', [], SEC)
    if mode != 'legacy_admitted':
        original.update(watch_token='first-watch', first_admitted_wall_ns=100)
    endpoint = original
    if mode == 'successor':
        endpoint = log.admit('next', [], 2 * SEC)
        endpoint.update(watch_token='next-watch', first_admitted_wall_ns=200)
        log.add('superseded', 2 * SEC, predecessor_admission_id='initial', successor_admission_id='next')
    published = copy.deepcopy(endpoint)
    if mode == 'legacy_admitted':
        published.update(watch_token='first-watch', first_admitted_wall_ns=100)
    elif mode == 'legacy_publication':
        published.pop('watch_token')
        published.pop('first_admitted_wall_ns')
    elif mode == 'evolving':
        published['receipt_ids'] = ['new-receipt']
        published['required_versions'] = {'entries': [[{'type': 'SourceKey'}, 'new-version']]}
        published['required_scopes'] = {'entries': [[{'type': 'SourceKey'}, ['expanded-scope']]]}
    elif mode == 'later_known':
        original.pop('watch_token')
        original.pop('first_admitted_wall_ns')
        log.add('admitted', 2 * SEC, admission=copy.deepcopy(published), chat=published['chat'])
    pub, body = log.store(published, 3 * SEC)
    log.emit(pub, body, 4 * SEC, via=via)
    log.close(30 * SEC)
    result = analyze(log)
    assert result['inputs_valid'], result
    assert result['episodes']['fulfilled'] == 1
    assert result['episodes']['unmatched'] == result['failures_over_budget'] == 0
    assert result['episodes']['fulfilled_series']['max_ns'] == 3 * SEC


@pytest.mark.parametrize('via', ['emit', 'catchup'])
@pytest.mark.parametrize('mutation', ['absent', 'other_chat', 'other_window', 'conflicted_endpoint'])
def test_publication_requires_unconflicted_owner_matched_admitted_endpoint(via, mutation):
    log = Log()
    original = log.admit('initial', [], SEC)
    original.update(watch_token='original', first_admitted_wall_ns=100)
    published = copy.deepcopy(original)
    if mutation == 'absent':
        published['id'] = 'not-admitted'
    elif mutation == 'other_chat':
        log.rows[0]['admission']['chat']['session_id'] = 'other-chat'
        log.rows[0]['chat']['session_id'] = 'other-chat'
    elif mutation == 'other_window':
        log.rows[0]['window_id'] = 'other-window'
        log.add('window_end', 30 * SEC, dropped_records=0, write_errors=[], encoding_errors=[])
        log.rows[-1]['window_id'] = 'other-window'
    else:
        conflicting = copy.deepcopy(original)
        conflicting['watch_token'] = 'replacement'
        log.add('admitted', 2 * SEC, admission=conflicting, chat=conflicting['chat'])
    pub, body = log.store(published, 3 * SEC)
    log.emit(pub, body, 4 * SEC, via=via)
    log.close(30 * SEC)
    result = analyze(log)
    assert not result['inputs_valid']
    assert result['episodes']['fulfilled'] == result['events']['fulfilled'] == 0
