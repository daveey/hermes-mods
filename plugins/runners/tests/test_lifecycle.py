"""Lifecycle evidence fixtures are fictional; no live commands or credentials."""
import json

import pytest

from plugins.runners.probes import lifecycle


def journal(message, at=1000):
    return json.dumps({'__REALTIME_TIMESTAMP': str(at * 1000000), 'MESSAGE': message})


def test_disabled_inactive_is_explicit_drain_only_when_configured():
    text = 'LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n'
    assert lifecycle.service_evidence(text, 1000, intentional_drain=True)['state'] == 'held'
    assert lifecycle.service_evidence(text, 1000, intentional_drain=False)['state'] == 'unknown'
    for replacement in ('enabled', 'masked', 'static'):
        assert lifecycle.service_evidence(text.replace('disabled', replacement), 1000, intentional_drain=True)['state'] != 'held'
    assert lifecycle.service_evidence(None, 1000, intentional_drain=True)['observed_at'] is None


def test_pressure_requires_explicit_per_lane_hold_not_low_disk_or_missing_listener():
    data = dict(pressure=True, free_bytes=10, low_bytes=20, high_bytes=30, held={'1': 42}, listeners={})
    result = lifecycle.admission_evidence(journal(json.dumps(data)), '1')
    assert result == dict(kind='disk_pressure', source='admission_journal', state='held', observed_at=1000.0, recorded_at=1000.0)
    assert lifecycle.admission_evidence(journal(json.dumps(data)), '2')['state'] == 'unknown'
    data['pressure'] = False
    assert lifecycle.admission_evidence(journal(json.dumps(data)), '1')['state'] == 'clear'
    data['pressure'] = 'true'
    assert lifecycle.admission_evidence(journal(json.dumps(data)), '1')['state'] == 'unknown'


def test_admission_does_not_scan_back_to_old_hold():
    hold = journal(json.dumps(dict(pressure=True, held={'1': 42})))
    assert lifecycle.admission_evidence(hold + '\n' + journal('collector failed', 1010), '1')['state'] == 'unknown'


@pytest.mark.parametrize('raw', [None, '', '{}', 'SECRET', '[]', journal('{}'), journal('{"pressure":true,"held":[]}')])
def test_bad_admission_is_unknown_and_never_echoes_source(raw):
    result = lifecycle.admission_evidence(raw, '1')
    assert result['state'] == 'unknown'
    assert 'SECRET' not in json.dumps(result)


def test_headroom_requires_last_record_and_preserves_source_time():
    wait = journal('single-job runner: waiting for 32 GiB root headroom', 900)
    result = lifecycle.headroom_evidence(wait)
    assert result['state'] == 'held'
    assert result['observed_at'] == 900
    assert lifecycle.headroom_evidence(wait + '\n' + journal('Listening for Jobs', 1000))['state'] == 'unknown'
    assert lifecycle.headroom_evidence(journal('unrelated waiting for space'))['state'] == 'unknown'


def test_marker_permission_failure_is_not_absence(tmp_path):
    assert lifecycle.marker_evidence(str(tmp_path / 'missing-parent' / 'maintenance'), 1000)['state'] == 'unknown'
    marker = tmp_path / 'maintenance'
    assert lifecycle.marker_evidence(str(marker), 1000)['state'] == 'clear'
    marker.write_text('private details')
    result = lifecycle.marker_evidence(str(marker), 1000)
    assert result['state'] == 'held'
    assert result['observed_at'] == 1000
    assert 'private details' not in json.dumps(result)


def test_marker_errors_and_symlinks_are_unknown(monkeypatch, tmp_path):
    marker = tmp_path / 'link'
    marker.symlink_to(tmp_path / 'missing')
    assert lifecycle.marker_evidence(str(marker), 1000)['state'] == 'unknown'
    def denied(*args, **kwargs):
        raise PermissionError()
    monkeypatch.setattr(lifecycle.os, 'lstat', denied)
    assert lifecycle.marker_evidence(str(marker), 1000)['state'] == 'unknown'


def test_withdrawal_is_a_historical_event_not_current_hold():
    malformed = 'example-runner saved custom labels: [build-ci] withdrawn 9999-12-31T23:59:59-01:00 by supervisor (disk emergency)'
    assert lifecycle.label_evidence(malformed, 'example-runner', 1000)['recorded_at'] is None
    text = 'example-runner saved custom labels: [build-ci] withdrawn 2026-01-01T00:00:00Z by supervisor (disk emergency)'
    result = lifecycle.label_evidence(text, 'example-runner', 1000)
    assert result['kind'] == 'label_withdrawn'
    assert result['state'] == 'unknown'
    assert result['recorded_at'] is not None
    assert lifecycle.label_evidence(text, 'another-runner', 1000)['recorded_at'] is None
    assert lifecycle.label_evidence(text + '\nexample-runner labels restored', 'example-runner', 1000)['recorded_at'] is None


def test_journal_invalid_timestamps_are_not_current_evidence():
    for at in ('nan', 'inf', '-1', 'bad', None, True, [], {}, '1e300', 10**400):
        raw = json.dumps({'__REALTIME_TIMESTAMP': at, 'MESSAGE': 'single-job runner: waiting for 32 GiB root headroom'})
        assert lifecycle.headroom_evidence(raw)['state'] == 'unknown'


@pytest.mark.parametrize('raw', ['null', 'true', '42', '[]', '[1]', '"secret"'])
def test_non_object_journal_is_unknown(raw):
    assert lifecycle.last_journal(raw) == (None, None)


@pytest.mark.parametrize('raw', [True, 42, [], {}, b'LoadState=loaded'])
def test_non_text_service_is_unknown(raw):
    assert lifecycle.service_evidence(raw, 1000, intentional_drain=True)['state'] == 'unknown'


def test_timestamp_expiry_bounds():
    limit = lifecycle.MAX_TIMESTAMP
    assert lifecycle.valid_timestamp(limit)
    assert lifecycle.valid_timestamp(limit - 180, 180)
    assert not lifecycle.valid_timestamp(limit - 179, 180)
    for ttl in (True, -1, float('inf'), float('nan'), 10**400, '180'):
        assert not lifecycle.valid_timestamp(1000, ttl)
