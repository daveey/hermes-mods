"""Deferred error records use today's classifier, not yesterday's decision."""
import pytest
from test_detector_recheck_pending import sg, sh, NOW, store, _retry, _skips

TB = ['Traceback (most recent call last):',
      '  File "/app/hermes-agent/run_agent.py", line 12, in run',
      '    raise ValueError("broken")', 'ValueError: broken']


def signal(logger='run_agent', msg='turn failed'):
    return sg.parse_errors([f'2026-11-13 00:00:00 ERROR {logger}: {msg}', *TB])[0]


def test_old_classifier_pending_record_is_withdrawn(store, monkeypatch):
    # Queue with the pre-fix rule, then retry using the freshly loaded classifier.
    import re
    with monkeypatch.context() as old:
        old.setattr(sg, 'MONITOR_SOURCE_FAILED_RE', re.compile(r'(?!)'))
        s = signal('cron.scheduler', "Job 'probe': monitor source failed: command exited 1")
    d = sg.to_defect({**s, 'host': 'example-host'})
    out, filed, left = _retry(store, d)
    assert not filed and left == 0
    assert out[0]['why'].startswith('detector_exempts_now:')
    assert _skips(store)[0]['fingerprint'] == d['fingerprint']


def test_unchanged_exception_files(store):
    s = signal()
    assert s['recheck']['kind'] == 'errors_record'
    assert s['recheck']['cont'] == TB
    assert sg.recheck_exempts(s['recheck']) is None
    out, filed, left = _retry(store, sg.to_defect({**s, 'host': 'example-host'}))
    assert len(filed) == 1 and left == 0 and not _skips(store)


@pytest.mark.parametrize('bad', [{}, {'logger': None}, {'cont': 'not lines'},
                                {'cont': [None]}, {'at': float('nan')},
                                {'truncated': True}, {'level': 'BOGUS'}])
def test_malformed_record_fails_open(bad):
    rc = {'kind': 'errors_record', 'logger': 'cron.scheduler',
          'msg': "Job 'probe': monitor source failed: command exited 1",
          'cont': TB, 'level': 'ERROR', 'at': NOW, 'sid': '',
          'host': '', 'profile': 'default'}
    if not bad:
        rc = {'kind': 'errors_record'}
    else:
        rc.update(bad)
    assert sg.recheck_exempts(rc) is None


def test_bounded_record_never_exempts_from_partial_evidence():
    s = sg.parse_errors(['2026-11-13 00:00:00 ERROR run_agent: turn failed',
                         *TB, *['x' * 10000] * 300])[0]
    rc = s['recheck']
    assert len(rc['cont']) <= 128 and sum(map(len, rc['cont'])) <= 65536
    assert rc['truncated'] is True
    assert sg.recheck_exempts(rc) is None


def test_worker_storm_carries_replay():
    crashes = sg.parse_worker_crashes([f'[pid {pid}] {line}' for pid in range(1, 5) for line in TB])
    assert crashes and crashes[0]['recheck']['kind'] == 'errors_record'
    signals = sg.worker_storms({}, crashes, NOW, 'example-host')
    assert signals and sg.recheck_exempts(signals[0]['recheck']) is None
    rc = {**signals[0]['recheck'], 'cont': [*TB[:-1], 'SubscriptionQuotaUnavailable: booked']}
    assert sg.recheck_exempts(rc)
