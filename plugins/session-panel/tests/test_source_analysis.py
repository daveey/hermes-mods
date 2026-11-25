"""Pure stage latency/accounting analyzer over synthetic panel-source-receipts/1 records.

Synthetic fixtures only: nothing here is a measurement, activation or acceptance proof.
"""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SEC = 1_000_000_000
DASH = Path(__file__).resolve().parents[1] / 'dashboard'


def load(name, filename):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, DASH / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


A = load('_panel_source_analysis_test', 'source_analysis.py')
R = load('_panel_source_receipts_analysis', 'source_receipts.py')
IDENT = dict(boot_id='b', pid=1, process_birth_ticks=2, runtime_sha='r', mods_sha='m', core_sha='c',
             api_generation='g', serving_gateway='gw', profile='p')


def chat(n='c1'):
    return {'type': 'ChatKey', 'serving_gateway': 'gw', 'profile': 'p', 'session_id': n}


def payload(body):
    raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
    return dict(payload_sha256=hashlib.sha256(raw).hexdigest(), serialized_bytes=len(raw),
                payload_b64=base64.b64encode(raw).decode())


class Log:
    def __init__(self, window='w1'):
        self.rows, self.n, self.window = [], 0, window

    def add(self, event, mono, **data):
        self.n += 1
        row = dict(schema=R.SCHEMA, event=event, window_id=self.window, record_id=f'{self.window}-{self.n}',
                   wall_ns=10**18 + mono, monotonic_ns=mono, thread_id=1, thread_birth='t', **IDENT, **data)
        self.rows.append(row)
        return row

    def admission(self, aid, receipts, first, c='c1'):
        return {'type': 'Admission', 'id': aid, 'chat': chat(c), 'first_admitted_mono_ns': first,
                'receipt_ids': list(receipts), 'required_versions': {'entries': [[{'type': 'SourceKey'}, 'v']]},
                'required_scopes': {'entries': []}}

    def source(self, rid, received):
        self.add('source_received', received + 1, receipt=dict(received_mono_ns=received, coverage=dict(capture_id=rid)))

    def admit(self, aid, receipts, first, c='c1', at=None):
        a = self.admission(aid, receipts, first, c)
        self.add('admitted', at or first, admission=a, chat=chat(c))
        return a

    def store(self, a, mono, pid='pub1', rev='rev1', c='c1', session='S1'):
        pub = dict(feed_epoch='e', publication_id=pid, session=session, model_revision=rev, chat=chat(c),
                   admission=a, source_vector={'k': 1})
        body = {'model': {'revision': rev}}
        self.add('model_stored', mono, publication=pub, **payload(body))
        return pub, body

    def emit(self, pub, body, done, kind='model', via='emit'):
        out = {'epoch': pub['feed_epoch'], 'bundles': {pub['session']: body}}
        if via == 'emit':
            self.add('emit_success', done, kind=kind, observation='server_emission', publications=[pub] if kind == 'model' else [],
                     callback_started_ns=done - 5, callback_completed_ns=done, **payload(out))
        else:
            self.add('catchup_success', done, kind='model', authenticated=True, request_id='q', response_id='s',
                     write_completed_ns=done, publications=[pub], **payload(out))

    def close(self, mono, dropped=0, errors=None):
        self.add('window_end', mono, dropped_records=dropped, write_errors=errors or [], encoding_errors=[])


def analyze(log, **kw):
    return A.analyze_records(log.rows, receipts=R, **kw)


def served(log=None, first=1 * SEC, done=3 * SEC, via='emit'):
    log = log or Log()
    log.source('r1', first - SEC // 2)
    a = log.admit('a1', ['r1'], first)
    pub, body = log.store(a, first + SEC // 2)
    log.emit(pub, body, done, via=via)
    return log, a, pub, body


def test_receiptless_failed_admission_remains_primary_episode():
    log = Log()
    log.admit('initial', [], SEC)
    log.add('source_error', 2 * SEC, error='TimeoutError')
    log.admit('initial', [], SEC, at=3 * SEC)
    log.close(30 * SEC)
    result = analyze(log)
    assert result['inputs_valid']
    assert result['outcomes']['admitted_distinct'] == 1
    assert result['outcomes']['admitted_duplicate_observations'] == 1
    assert result['events']['total'] == 0, 'no source receipt was acquired'
    episode = result['episodes']
    assert (episode['total'], episode['fulfilled'], episode['unmatched']) == (1, 0, 1)
    assert episode['unmatched_sample'][0]['start'] == SEC
    assert episode['unmatched_sample'][0]['lower_bound_ns'] == 29 * SEC
    assert episode['unmatched_lower_bound_over_budget'] == result['failures_over_budget'] == 1


@pytest.mark.parametrize('mode', ['same_id', 'successor', 'unrelated', 'forgotten', 'open'])
def test_receiptless_episode_requires_exact_inclusion_or_declared_successor(mode):
    log = Log()
    first = log.admit('initial', [], SEC)
    if mode in ('same_id', 'successor', 'unrelated'):
        aid = 'initial' if mode == 'same_id' else 'later'
        later = log.admit(aid, ['new-receipt'], SEC if aid == 'initial' else 2 * SEC)
        if mode == 'successor':
            log.add('superseded', 2 * SEC, predecessor_admission_id='initial', successor_admission_id='later')
        pub, body = log.store(later, 3 * SEC)
        log.emit(pub, body, 4 * SEC)
    elif mode == 'forgotten':
        log.add('forgotten', 2 * SEC, admission=first)
    if mode != 'open':
        log.close(30 * SEC)
    else:
        log.add('source_error', 30 * SEC, error='TimeoutError')
    result = analyze(log)
    if mode in ('same_id', 'successor'):
        assert result['episodes']['unmatched'] == 0
        assert result['episodes']['fulfilled_series']['max_ns'] == 3 * SEC
    else:
        assert result['episodes']['unmatched'] == 1
        assert result['episodes']['unmatched_sample'][0]['lower_bound_ns'] == 29 * SEC
        assert result['episodes']['unmatched_right_censored'] == (mode == 'open')
        assert result['failures_over_budget'] == 1


@pytest.mark.parametrize('via', ['emit', 'catchup'])
def test_receiptless_exact_inclusion_and_join_bound(via, monkeypatch):
    log = Log()
    a = log.admit('initial', [], SEC)
    pub, body = log.store(a, 2 * SEC)
    log.emit(pub, body, 4 * SEC, via=via)
    log.close(30 * SEC)
    result = analyze(log)
    assert result['inputs_valid']
    assert result['events']['total'] == 0
    assert result['episodes']['total'] == result['episodes']['fulfilled'] == 1
    assert result['episodes']['fulfilled_series']['max_ns'] == 3 * SEC
    monkeypatch.setattr(A, 'MAX_JOIN_WORK', 0)
    with pytest.raises(A.AnalysisBounds, match='admission inclusion join bound'):
        analyze(log)


def test_exact_fulfilment_is_reported_without_acceptance_verdict():
    log, *_ = served()
    log.close(10 * SEC)
    r = analyze(log)
    assert r['inputs_valid'] and r['validator']['valid']
    ep = r['episodes']
    assert (ep['total'], ep['fulfilled'], ep['unmatched']) == (1, 1, 0)
    assert ep['fulfilled_series']['p95_ns'] == ep['fulfilled_series']['max_ns'] == 2 * SEC
    assert ep['by_fulfilling_observation'][A.EMIT]['samples'] == 1
    assert ep['by_fulfilling_observation'][A.CATCHUP]['samples'] == 0
    assert r['stages']['source_receipt_to_admission']['p95_ns'] == SEC // 2
    assert r['acceptance'] == r['semantic_oracle'] == r['cpu_gate'] == A.NOT_EVALUATED
    assert r['failures_over_budget'] == 0 and r['log_gaps']['counters_complete'] is True


def test_catchup_labelled_separately_and_both_observations_not_pooled():
    log, a, pub, body = served()
    log.emit(pub, body, 5 * SEC, via='catchup')
    log.close(10 * SEC)
    r = analyze(log)
    assert r['inputs_valid']
    assert r['events']['total'] == 1 and r['events']['fulfilled'] == 1
    assert r['events']['by_observation'][A.EMIT]['p95_ns'] == 2 * SEC
    assert r['events']['by_observation'][A.CATCHUP]['p95_ns'] == 4 * SEC
    assert r['episodes']['fulfilled_series']['samples'] == 1  # first fulfilment, one episode


def test_catchup_only_is_labelled_catchup():
    log, *_ = served(via='catchup')
    log.close(10 * SEC)
    ep = analyze(log)['episodes']
    assert ep['by_fulfilling_observation'][A.CATCHUP]['samples'] == 1
    assert ep['by_fulfilling_observation'][A.EMIT]['samples'] == 0


def test_invalidation_only_never_fulfils_and_failure_over_15s_is_retained():
    log = Log()
    a = log.admit('a1', ['r1'], SEC)
    pub, body = log.store(a, 2 * SEC)
    log.emit(pub, body, 3 * SEC, kind='invalidation_only')
    log.close(20 * SEC)
    r = analyze(log)
    assert r['episodes']['unmatched'] == 1 and r['episodes']['fulfilled'] == 0
    assert r['outcomes']['emit_invalidation_only'] == 1
    assert r['episodes']['unmatched_lower_bound_over_budget'] == 1
    assert r['failures_over_budget'] == 1
    assert r['episodes']['p95_lower_bound_including_unmatched_ns'] == 19 * SEC
    assert r['episodes']['fulfilled_series']['p95_ns'] is None  # no denominator-shrinking success


def test_failed_catchup_and_no_catchup_stay_unfulfilled():
    log = Log()
    a = log.admit('a1', ['r1'], SEC)
    log.store(a, 2 * SEC)
    log.add('catchup_error', 4 * SEC, request_id='q', response_id='s', authenticated=True, error='X')
    log.add('emit_error', 4 * SEC, error='Y')
    log.close(8 * SEC)
    r = analyze(log)
    assert r['episodes']['unmatched'] == 1 and r['events']['unfulfilled'] == 1
    assert (r['outcomes']['catchup_error'], r['outcomes']['emit_error']) == (1, 1)
    assert r['episodes']['unmatched_lower_bound_over_budget'] == 0
    assert r['episodes']['unmatched_right_censored'] == 0  # closed window: unmatched, not censored
    assert r['validator']['unmatched_publications']


def test_duplicate_coalesced_admissions_keep_first_age_and_do_not_pool():
    log = Log()
    log.source('r1', SEC // 2)
    log.source('r2', 4 * SEC)
    a1 = log.admit('a1', ['r1'], SEC)
    log.admit('a1', ['r1'], SEC, at=SEC + 1)   # re-logged immutable admission, not independent
    # coalesced later admission preserves the original first unserved age
    a2 = log.admit('a2', ['r1', 'r2'], SEC, at=5 * SEC)
    pub, body = log.store(a2, 6 * SEC)
    log.emit(pub, body, 9 * SEC)
    log.close(10 * SEC)
    r = analyze(log)
    assert r['outcomes']['admitted_distinct'] == 2 and r['outcomes']['admitted_duplicate_observations'] == 1
    assert r['events']['total'] == 2 and r['events']['fulfilled'] == 2   # both receipt versions deduped to event
    assert r['episodes']['total'] == 1 and r['episodes']['coalesced_episodes'] == 1
    assert r['episodes']['fulfilled_series']['max_ns'] == 8 * SEC  # from first admission, not a2's arrival


def test_not_incorporating_required_receipt_does_not_fulfil():
    log = Log()
    log.admit('a1', ['r1'], SEC)
    a_other = log.admission('a9', ['r9'], 2 * SEC)
    pub, body = log.store(a_other, 3 * SEC)
    log.emit(pub, body, 4 * SEC)
    log.close(30 * SEC)
    r = analyze(log)
    assert r['episodes']['fulfilled'] == 0 and r['episodes']['unmatched'] == 1
    assert r['episodes']['unmatched_lower_bound_over_budget'] == 1


def test_wrong_chat_does_not_fulfil():
    log = Log()
    log.admit('a1', ['r1'], SEC, c='c1')
    other = log.admission('a1b', ['r1'], SEC, c='c2')
    pub, body = log.store(other, 2 * SEC, c='c2')
    log.emit(pub, body, 3 * SEC)
    log.close(5 * SEC)
    r = analyze(log)
    by = {e['chat'][2] for e in r['episodes']['unmatched_sample']}
    # c2 was never admitted, so its inclusion cannot create or fulfil anything for c1
    assert by == {'c1'} and r['episodes']['total'] == 1 and r['episodes']['fulfilled'] == 0


def test_explicit_successor_fulfils_but_unrelated_does_not():
    log = Log()
    log.admit('a1', ['r1'], SEC)
    succ = log.admit('a2', ['r2'], 2 * SEC, at=2 * SEC)
    log.add('superseded', 2 * SEC, predecessor_admission_id='a1', successor_admission_id='a2')
    pub, body = log.store(succ, 3 * SEC)
    log.emit(pub, body, 4 * SEC)
    log.close(6 * SEC)
    r = analyze(log)
    assert r['events']['fulfilled'] == 2 and r['outcomes']['superseded'] == 1
    assert r['episodes']['fulfilled'] >= 1 and r['episodes']['unmatched'] == 0
    assert r['episodes']['fulfilled_series']['max_ns'] == 3 * SEC  # keeps r1's first age


def test_forged_cross_window_payload_cannot_fulfil():
    log, a, pub, body = served(Log('w1'))
    log.close(10 * SEC)
    forged = Log('w2')
    forged.admit('a1', ['r1'], SEC)  # same admission material, other window
    forged.emit(pub, body, 3 * SEC)  # inclusion of w1's stored publication
    forged.close(10 * SEC)
    rows = log.rows + forged.rows
    r = A.analyze_records(rows, receipts=R)
    assert not r['inputs_valid'] and not r['validator']['valid']
    assert r['outcomes']['rejected_inclusion_records'] == 1
    w2 = [e for e in r['episodes']['unmatched_sample'] if e['window'] == 'w2']
    assert len(w2) == 1  # w2 episode unfulfilled
    assert r['episodes']['fulfilled'] == 1  # only w1's legitimate one


def test_missing_terminal_is_censored_not_closed_and_counters_unknown():
    log, *_ = served()
    log.admit('a2', ['r2'], 4 * SEC, c='c2')
    log.add('capture_start', 5 * SEC, capture_attempt_id='cap', domain='local')
    log.add('projection_start', 6 * SEC, projection_attempt_id='pr', admission=log.admission('a2', ['r2'], 4 * SEC, 'c2'))
    log.add('writer_request', 6 * SEC, writer_id='wr', producer_kind='full', requested_ns=6 * SEC)
    log.add('writer_request', 6 * SEC, writer_id='wr2', producer_kind='age', requested_ns=6 * SEC)
    log.add('writer_acquire', 7 * SEC, writer_id='wr2', producer_kind='age', requested_ns=6 * SEC, acquired_ns=7 * SEC)
    log.add('source_received', 30 * SEC, receipt=dict(received_mono_ns=30 * SEC, coverage=dict(capture_id='x')))
    r = analyze(log)
    assert not r['validator']['valid'] and not r['inputs_valid']
    assert r['log_gaps']['unclosed_windows'] == ['w1']
    assert r['log_gaps']['dropped_records'] is None and r['log_gaps']['counters_complete'] is False
    assert r['log_gaps']['per_window']['w1']['dropped_records'] is None
    assert r['episodes']['unmatched'] == 1 and r['episodes']['unmatched_right_censored'] == 1
    assert r['episodes']['unmatched_lower_bound_over_budget'] == 1  # 30s - 4s
    assert r['captures']['right_censored']['count'] == 1
    assert r['projections']['right_censored']['count'] == 1
    assert r['writers']['counts']['right_censored_waiting'] == 1
    assert r['writers']['counts']['right_censored_holding'] == 1



def test_over_15s_fulfilled_is_failure_and_in_p95():
    log, *_ = served(done=20 * SEC)
    log.close(30 * SEC)
    r = analyze(log)
    assert r['episodes']['fulfilled_over_budget'] == 1 and r['failures_over_budget'] == 1
    assert r['episodes']['fulfilled_series']['p95_ns'] == 19 * SEC
    assert r['acceptance'] == A.NOT_EVALUATED


def test_window_loss_is_explicit():
    log, *_ = served()
    log.close(10 * SEC, dropped=3)
    r = analyze(log)
    assert r['log_gaps']['lossy_windows'] == ['w1'] and r['log_gaps']['dropped_records'] == 3
    assert not r['inputs_valid'] and not r['validator']['valid']


@pytest.mark.parametrize('field,bad', [
    ('dropped_records', value) for value in (None, True, False, -1, 0.0, '0', [], {})
] + [
    (field, value) for field in ('write_errors', 'encoding_errors')
    for value in (None, False, 0, '', {}, [1])
])
@pytest.mark.parametrize('omit', [False, True])
def test_closed_window_malformed_loss_counters_remain_unknown(field, bad, omit):
    log, *_ = served()
    log.close(10 * SEC)
    if omit:
        del log.rows[-1][field]
    else:
        log.rows[-1][field] = bad
    # An intact window must not hide an unknown window behind a partial sum.
    intact, *_ = served(Log('intact'))
    intact.close(10 * SEC, dropped=3)
    r = A.analyze_records(log.rows + intact.rows, receipts=R)
    gaps = r['log_gaps']
    assert gaps['unclosed_windows'] == []
    assert gaps['counters_complete'] is False
    assert gaps['dropped_records'] is None
    assert gaps['per_window']['w1'][field] == (None if omit else bad)
    assert not r['inputs_valid']


@pytest.mark.parametrize('dropped,errors', [(0, []), (3, []), (0, ['sink error'])])
def test_closed_window_known_loss_counters_are_complete(dropped, errors):
    log, *_ = served()
    log.close(10 * SEC, dropped=dropped, errors=errors)
    r = analyze(log)
    assert r['log_gaps']['counters_complete'] is True
    assert r['log_gaps']['dropped_records'] == dropped
    assert r['inputs_valid'] is (dropped == 0 and not errors)


def test_nearest_rank_p95():
    assert A.stats(list(range(1, 21)))['p95_ns'] == 19
    assert A.stats(list(range(1, 101)))['p95_ns'] == 95
    assert A.stats([7])['p95_ns'] == 7
    assert A.stats([]) == dict(denominator=0, samples=0, p95_ns=None, max_ns=None)


@pytest.mark.parametrize('bad', [True, False, -1, float('nan'), float('inf'), 1.5, '5', None])
def test_clock_rejects_non_int_bool_negative_nonfinite(bad):
    assert not A.clock(bad)
    log, *_ = served()
    log.close(10 * SEC)
    log.rows[0]['monotonic_ns'] = bad
    r = analyze(log)
    assert r['records']['rejected'] >= 1 and not r['inputs_valid']


def test_bad_admission_clock_is_rejected_not_zeroed():
    log = Log()
    a = log.admission('a1', ['r1'], True)
    log.add('admitted', SEC, admission=a, chat=chat())
    log.close(2 * SEC)
    r = analyze(log)
    assert r['events']['total'] == 0 and r['records']['rejected'] == 1


def test_bad_inclusion_clock_rejected():
    log, a, pub, body = served()
    emit = next(x for x in log.rows if x['event'] == 'emit_success')
    emit['callback_completed_ns'] = float('nan')
    log.close(10 * SEC)
    r = analyze(log)
    assert r['episodes']['fulfilled'] == 0


def test_identity_and_window_shape_checks():
    log, *_ = served()
    log.close(10 * SEC)
    drift = copy.deepcopy(log.rows)
    drift[1]['pid'] = 99
    r = A.analyze_records(drift, receipts=R)
    assert not r['inputs_valid'] and r['records']['rejected'] >= 1
    r = A.analyze_records(log.rows, receipts=R, expected_identity=dict(IDENT, pid=5))
    assert r['records']['analyzed'] == 0 and r['events']['total'] == 0
    junk = [None, 3, {'schema': 'x'}, dict(log.rows[0], window_id='')]
    r = A.analyze_records(junk, receipts=R)
    assert r['records']['rejected'] == 4 and not r['inputs_valid']


def test_bounds_fail_closed():
    log, *_ = served()
    with pytest.raises(A.AnalysisBounds):
        A.analyze_records(log.rows, receipts=R, max_records=2)
    with pytest.raises(A.AnalysisBounds):
        A.analyze_records(log.rows, receipts=R, max_inclusion_checks=0)
    with pytest.raises(A.AnalysisBounds):
        A.analyze_records('not records', receipts=R)


def test_oversized_receipt_list_rejected():
    log = Log()
    a = log.admission('a1', [f'r{i}' for i in range(A.MAX_LIST + 1)], SEC)
    log.add('admitted', SEC, admission=a, chat=chat())
    r = analyze(log)
    assert r['events']['total'] == 0 and r['records']['rejected'] == 1


def test_conflicting_immutable_admission_is_integrity_failure():
    log = Log()
    log.admit('a1', ['r1'], SEC)
    log.admit('a1', ['r1'], 2 * SEC)
    log.close(3 * SEC)
    r = analyze(log)
    assert r['integrity_errors'] and not r['inputs_valid'] and r['events']['total'] == 0


def test_duplicate_record_observation_not_pooled():
    log, *_ = served()
    log.close(10 * SEC)
    dup = log.rows[:] + [copy.deepcopy(next(x for x in log.rows if x['event'] == 'emit_success'))]
    r = A.analyze_records(dup, receipts=R)
    assert r['records']['duplicate_identical'] == 1 and not r['inputs_valid']
    assert r['episodes']['fulfilled_series']['samples'] == 1


def test_stage_series_dispositions_and_writers_separate():
    log, a, *_ = served()
    log.add('capture_start', 1, capture_attempt_id='c1', domain='external')
    log.add('capture_end', SEC, capture_attempt_id='c1', disposition='accepted', elapsed_ns=SEC // 4, thread_cpu_ns=10,
            io_cost=dict(available=False, reason='preexisting_profiler'))
    log.add('capture_start', 2, capture_attempt_id='c2', domain='local')
    log.add('capture_end', SEC, capture_attempt_id='c2', disposition='error', elapsed_ns=5, thread_cpu_ns=True,
            io_cost=dict(db_opens=0, sql_calls=1, network_calls=0))
    log.add('projection_start', 2 * SEC, projection_attempt_id='p1', admission=a)
    log.add('projection_end', 2 * SEC + 7, projection_attempt_id='p1', disposition='superseded', elapsed_ns=7, thread_cpu_ns=3)
    log.add('projection_start', 2 * SEC, projection_attempt_id='p2', admission=a)
    log.add('projection_end', 2 * SEC + 9, projection_attempt_id='p2', disposition='forgotten', elapsed_ns=9, thread_cpu_ns=4)
    log.add('writer_request', 3, writer_id='w', producer_kind='full', requested_ns=100)
    log.add('writer_acquire', 4, writer_id='w', producer_kind='full', requested_ns=100, acquired_ns=250)
    log.add('writer_release', 5, writer_id='w', producer_kind='full', requested_ns=100, acquired_ns=250,
            acquired=True, released_ns=400, thread_cpu_ns=9)
    log.add('writer_request', 3, writer_id='x', producer_kind='age', requested_ns=100)
    log.add('writer_release', 5, writer_id='x', producer_kind='age', requested_ns=100, acquired_ns=None,
            acquired=False, released_ns=400)
    for e in ('no_change', 'forgotten', 'superseded'):
        log.add(e, 9 * SEC)
    log.close(10 * SEC)
    r = analyze(log)
    c = r['captures']
    assert c['started'] == c['ended'] == 2 and c['dispositions'] == {'accepted': 1, 'error': 1}
    assert c['io_cost_evidence'] == {'unavailable': 1, 'available': 1}
    assert c['thread_cpu']['samples'] == 1  # bool clock refused, not counted as 1ns
    assert c['by_domain'] == {'external': 1, 'local': 1}
    p = r['projections']
    assert (p['superseded'], p['forgotten'], p['failures']) == (1, 1, 0)
    assert r['stages']['admission_to_projection_start']['p95_ns'] == SEC
    w = r['writers']
    assert w['wait']['p95_ns'] == 150 and w['hold']['p95_ns'] == 150
    assert w['counts']['completed'] == 1 and w['counts']['refused_or_timed_out'] == 1
    assert r['outcomes']['no_change'] == r['outcomes']['forgotten'] == r['outcomes']['superseded'] == 1
    assert r['episodes']['fulfilled'] == 1  # none of the above fulfilled anything


def test_dataclass_map_entries_decode():
    assert A.pairs({'entries': [['k', 1]]}) == [('k', 1)]
    assert A.pairs({'a': 1}) == [('a', 1)]
    assert A.pairs({'entries': [['k']]}) is None and A.pairs([1]) is None


def test_sibling_loader_uses_real_validator_not_a_copy():
    assert callable(A.load_receipts(DASH / 'source_receipts.py').validate_records)
    # Analyzer delegates: a receipts double that rejects everything fulfils nothing.
    class Deny:
        SCHEMA, EVENTS = R.SCHEMA, R.EVENTS
        @staticmethod
        def validate_records(records, require_closed=False):
            return {'valid': False, 'errors': ['x'] * (1 + sum(r['event'] in ('emit_success',) for r in records)),
                    'stored': 0, 'fulfilled': 0, 'server_emitted': 0, 'authenticated_catchup': 0,
                    'unmatched': [[(r.get('publication') or {}).get(k) for k in ('feed_epoch', 'publication_id', 'session', 'model_revision')]
                                  for r in records if r['event'] == 'model_stored']}
    log, *_ = served()
    log.close(10 * SEC)
    r = A.analyze_records(log.rows, receipts=Deny)
    assert r['episodes']['fulfilled'] == 0


def test_real_coordinator_receiptlog_analysis(api, board, monkeypatch, tmp_path):
    from test_source_stage_receipts import setup, drain, kanban_db
    from test_source_receipts import receipts, identity, read_log
    conn, readers, feed, lane, _ = setup(api, board, monkeypatch)
    real = receipts()
    log = real.ReceiptLog(tmp_path / 'analyzer-real-stages.jsonl', identity=identity())
    monkeypatch.setattr(lane, '_receipt_log', lambda: log)
    feed.install_receipts(log)
    try:
        kanban_db.create_task(conn, title='Real analyzer input', session_id='S')
        drain(lane)
    finally:
        feed.stopped.set(); lane.close(); readers.release(); conn.close()
        assert log.close(), (log.dropped_records, log.write_errors, log.encoding_errors)
    rows = read_log(log.path)
    assert real.validate_records(rows, require_closed=True)['valid']
    assert any(r['event'] == 'admitted' and not r['admission']['receipt_ids'] for r in rows)
    admissions = [r['admission'] for r in rows if r['event'] == 'admitted']
    assert len({a['id'] for a in admissions}) < len(admissions), 'must exercise coalescing'
    result = A.analyze_records(rows, receipts=real)
    assert result['inputs_valid'], (result['integrity_errors'], result['records'])
    assert result['events']['total'] > 0
    # setup uses an arbitrary lambda callback, NOT the exact server-emission
    # adapter. Storage and callback return cannot certify model inclusion.
    assert result['events']['fulfilled'] == 0
    assert result['events']['unfulfilled'] == result['events']['total']
    assert result['writers']['counts']['completed'] > 0
    assert result['writers']['wait']['samples'] == result['writers']['counts']['completed']
    assert result['projections']['dispositions'].keys() <= {
        'stored', 'no_change', 'superseded', 'retired', 'forgotten', 'lineage_changed', 'error'}
    assert result['projections']['io_cost']['db_opens']['total'] == 0
    assert result['projections']['io_cost']['rows_returned']['total'] is None
    assert result['acceptance'] == result['semantic_oracle'] == result['native_desktop_p95'] == result['cpu_gate'] == A.NOT_EVALUATED


# Import the existing real fixture chain, rather than reconstructing its database/API.
from test_source_stage_receipts import api, board


@pytest.mark.parametrize('field,value', [('session', []), ('event', []), ('pid', True)])
def test_malformed_custody_fails_closed_without_crashing(field, value):
    log, _, pub, _ = served()
    log.close(10 * SEC)
    if field == 'session':
        pub[field] = value
    else:
        log.rows[0][field] = value
    result = analyze(log)
    assert not result['inputs_valid']


def test_record_after_window_end_cannot_fulfil():
    log = Log()
    a = log.admit('a1', ['r1'], SEC)
    pub, body = log.store(a, 2 * SEC)
    log.close(3 * SEC)
    log.emit(pub, body, 4 * SEC)
    result = analyze(log)
    assert result['events']['fulfilled'] == 0
    assert not result['inputs_valid']


def test_supersession_cannot_bridge_another_chat():
    log = Log()
    log.admit('a1', ['r1'], SEC, c='c1')
    log.admit('alien', ['r2'], 2 * SEC, c='c2')
    final = log.admit('a3', ['r3'], 3 * SEC, c='c1')
    log.add('superseded', 3 * SEC, predecessor_admission_id='a1', successor_admission_id='alien')
    log.add('superseded', 3 * SEC, predecessor_admission_id='alien', successor_admission_id='a3')
    pub, body = log.store(final, 4 * SEC)
    log.emit(pub, body, 5 * SEC)
    log.close(20 * SEC)
    result = analyze(log)
    assert result['events']['fulfilled'] == 1
    assert not result['inputs_valid']


def test_inclusion_after_terminal_clock_is_not_a_success():
    log, *_ = served(done=20 * SEC)
    log.close(10 * SEC)
    result = analyze(log)
    assert result['events']['fulfilled'] == 0
    assert not result['inputs_valid']


def test_nested_adversarial_input_is_bounded():
    log, *_ = served()
    nested = []
    for _ in range(100):
        nested = [nested]
    log.rows[0]['extra'] = nested
    with pytest.raises(A.AnalysisBounds):
        analyze(log)


def test_conflicting_duplicate_emit_is_not_evidence():
    log, *_ = served()
    emit = next(r for r in log.rows if r['event'] == 'emit_success')
    forged = copy.deepcopy(emit)
    forged['callback_completed_ns'] += 1
    log.rows.append(forged)
    log.close(10 * SEC)
    result = analyze(log)
    assert not result['inputs_valid']
    assert result['events']['fulfilled'] == 0


def test_inclusion_cannot_precede_its_storage():
    log, *_ = served(done=SEC + 10)
    log.close(10 * SEC)
    result = analyze(log)
    assert result['events']['fulfilled'] == 0
    assert not result['inputs_valid']


@pytest.mark.parametrize('bound', ['MAX_LINE_BYTES', 'MAX_INPUT_BYTES', 'MAX_RECORDS'])
def test_cli_input_limits_fail_closed(tmp_path, capsys, monkeypatch, bound):
    log, *_ = served()
    log.close(10 * SEC)
    p = tmp_path / 'bounded.jsonl'
    p.write_text('\n'.join(json.dumps(r) for r in log.rows))
    monkeypatch.setattr(A, bound, 1)
    assert A.main([str(p)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['error'] == 'AnalysisBounds'
    assert result['acceptance'] == A.NOT_EVALUATED


def test_encoding_errors_invalidate_window():
    log, *_ = served()
    log.close(10 * SEC)
    log.rows[-1]['encoding_errors'] = ['ValueError']
    result = analyze(log)
    assert not result['inputs_valid']
    assert result['log_gaps']['lossy_windows'] == ['w1']


def test_event_inclusion_join_work_is_bounded(monkeypatch):
    log, *_ = served()
    log.close(10 * SEC)
    monkeypatch.setattr(A, 'MAX_JOIN_WORK', 0, raising=False)
    with pytest.raises(A.AnalysisBounds):
        analyze(log)


def test_embedded_payload_recursion_fails_closed():
    log, *_ = served()
    stored = next(r for r in log.rows if r['event'] == 'model_stored')
    raw = b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'
    stored.update(payload_b64=base64.b64encode(raw).decode(),
                  payload_sha256=hashlib.sha256(raw).hexdigest(), serialized_bytes=len(raw))
    with pytest.raises(A.AnalysisBounds):
        analyze(log)


def test_cli_prints_not_evaluated(tmp_path, capsys):
    log, *_ = served()
    log.close(10 * SEC)
    p = tmp_path / 's.jsonl'
    p.write_text('\n'.join(json.dumps(x) for x in log.rows))
    assert A.main([str(p)]) == 0
    assert json.loads(capsys.readouterr().out)['acceptance'] == A.NOT_EVALUATED
