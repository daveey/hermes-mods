"""Producer clock differs from both the host and Intl's refreshed timezone."""
import importlib.util
import json
import pytest
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.request
import io
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('clock_bt', Path(__file__).resolve().parents[1] / 'backend_timeouts.py')
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def test_producer_offsets_prevent_midnight_false_zero(tmp_path):
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'desktop.log').write_text('2026-11-03 22:30:00,000 [hermes] [hermes:api GET /api/status] conn=remote Error: Timed out connecting to Hermes backend after 2000ms\n')
    now = datetime.fromisoformat('2026-11-04T01:39:00-05:00')
    segments = [(datetime.fromisoformat('2026-11-04T06:09:00+00:00').timestamp(), now.timestamp(), 480)]
    result = bt.scan(tmp_path, now, 30, clock_segments=segments)
    assert result['count'] == result['awake_count'] == 1
    assert result['window_end'].endswith('+00:00')
    assert result['by_conn'] == {'remote': 1}


def test_dst_fold_keeps_raw_entry_once_without_ambiguous_sleep_suppression(tmp_path):
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'desktop.log').write_text('2026-11-01 01:30:00,000 [hermes] /api/status Timed out connecting to Hermes backend\n')
    lo = datetime.fromisoformat('2026-11-01T08:00:00+00:00')
    change = datetime.fromisoformat('2026-11-01T09:00:00+00:00')
    now = datetime.fromisoformat('2026-11-01T10:00:00+00:00')
    segments = [(lo.timestamp(), change.timestamp() - 0.001, 420), (change.timestamp(), now.timestamp(), 480)]
    result = bt.scan(tmp_path, now, 120, [(lo, change)], clock_segments=segments)
    assert result['count'] == result['awake_count'] == result['ambiguous_count'] == 1
    assert result['sleep_count'] == 0


def test_aware_power_intervals_ignore_caller_timezone():
    now = datetime.fromisoformat('2026-11-04T06:39:00+00:00')
    text = '2026-11-03 22:20:00 -0800 Sleep Entering Sleep state\n2026-11-03 22:35:00 -0800 Wake Wake from Deep Idle'
    intervals = bt.power_intervals(text, now)
    assert intervals == [(datetime.fromisoformat('2026-11-04T06:20:00+00:00'), datetime.fromisoformat('2026-11-04T06:35:00+00:00'))]


def test_main_missing_producer_clock_fails_closed(tmp_path, capsys, monkeypatch):
    def unavailable(*args):
        raise ValueError('producer unavailable')
    monkeypatch.setattr(bt, 'producer_clock', unavailable, raising=False)
    rc = bt.main(['--home', str(tmp_path), '--dry-run'], now=datetime.now(timezone.utc))
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out['status'] == 'clock_unresolved' and out['count'] is None
    assert out['cards'] == []


def test_main_uses_actual_date_offsets_not_intl_or_system(tmp_path, capsys, monkeypatch):
    now = datetime.fromisoformat('2026-11-04T06:39:00+00:00')
    lo = now.timestamp() - 1800
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'desktop.log').write_text('2026-11-03 22:30:00,000 [hermes] /api/status Timed out connecting to Hermes backend\n')
    monkeypatch.setattr(bt, 'producer_clock', lambda *a: {'segments': [[lo, now.timestamp(), 480]], 'intl_zone': 'America/Cancun', 'source': 'desktop-main-Date'}, raising=False)
    assert bt.main(['--home', str(tmp_path), '--dry-run'], now=now, power_probe=lambda n: {'intervals': []}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['count'] == 1 and out['clock']['source'] == 'desktop-main-Date'
    assert out['clock']['segments'][0][2] == 480


def test_producer_clock_queries_date_offset_segments(monkeypatch):
    now = datetime.fromisoformat('2026-11-04T06:39:00+00:00')
    def evaluate(expression):
        assert 'getTimezoneOffset' in expression
        return {'segments': [[now.timestamp() - 1800, now.timestamp(), 480]],
                'pid': 42, 'intl_zone': 'America/Cancun', 'date': 'GMT-0800',
                'started_at': now.timestamp() - 7200}
    monkeypatch.setattr(bt, '_desktop_evaluate', evaluate, raising=False)
    out = bt.producer_clock(now, 30)
    assert out['source'] == 'desktop-main-Date'
    assert out['segments'][0][2] == 480


def test_producer_clock_refuses_window_before_producer_start(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(bt, '_desktop_evaluate', lambda e: {'segments': [[now.timestamp()-1800, now.timestamp(), 480]], 'started_at': now.timestamp()-60}, raising=False)
    with pytest.raises(ValueError, match='producer start'):
        bt.producer_clock(now, 30)


def test_inspector_transport_is_loopback_and_closes(monkeypatch):
    target = {'type': 'node', 'title': '/Applications/Hermes.app/Contents/MacOS/Hermes[42]',
              'webSocketDebuggerUrl': 'ws://127.0.0.1:9229/test'}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps([target]).encode()))
    ws = Mock()
    ws.recv.side_effect = [json.dumps({'method': 'event'}), json.dumps({'id': 1, 'result': {'result': {'value': {'pid': 42}}}})]
    connect = Mock(return_value=ws)
    monkeypatch.setitem(sys.modules, 'websocket', SimpleNamespace(create_connection=connect))
    assert bt._desktop_evaluate('1') == {'pid': 42}
    assert connect.call_args.args == ('ws://127.0.0.1:9229/test',)
    assert json.loads(ws.send.call_args.args[0])['params']['expression'] == '1'
    ws.close.assert_called_once()
    target['webSocketDebuggerUrl'] = 'ws://example.com:9229/test'
    with pytest.raises(ValueError, match='loopback'):
        bt._desktop_evaluate('1')
