"""Immutable producer offsets, independent of caller TZ or optional inspector."""
import importlib.util
import json
import pytest
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location('offset_bt', Path(__file__).resolve().parents[1] / 'backend_timeouts.py')
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def put(home, lines):
    (home / 'logs').mkdir(exist_ok=True)
    (home / 'logs/desktop.log').write_text('\n'.join(lines) + '\n')


def test_offset_stamp_prevents_cross_midnight_false_zero(tmp_path, capsys):
    put(tmp_path, ['2026-11-03 22:30:00,000 -08:00 [hermes] [hermes:api GET /api/status] conn=remote Error: Timed out connecting to Hermes backend after 2000ms'])
    rc = bt.main(['--home', str(tmp_path), '--dry-run'],
                 now=datetime.fromisoformat('2026-11-04T01:39:00-05:00'),
                 power_probe=lambda n: {'intervals': []})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out['count'] == out['awake_count'] == 1
    assert out['window_end'].endswith('+00:00')
    assert out['by_conn'] == {'remote': 1}
    assert out['clock']['source'] == 'per-entry-offset'


def test_legacy_timeout_fails_closed_instead_of_guessing_host_zone(tmp_path, capsys):
    put(tmp_path, ['2026-11-03 22:30:00,000 [hermes] /api/status Timed out connecting to Hermes backend'])
    rc = bt.main(['--home', str(tmp_path), '--dry-run'],
                 now=datetime.fromisoformat('2026-11-04T01:39:00-05:00'),
                 power_probe=lambda n: {'intervals': []})
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out['status'] == 'clock_unresolved' and out['count'] is None
    assert out['cards'] == []


def test_old_legacy_entry_cannot_disable_current_offset_alerts(tmp_path, capsys):
    put(tmp_path, ['2026-10-26 22:30:00,000 [hermes] /api/old Timed out connecting to Hermes backend',
                   '2026-11-03 22:30:00,000 -08:00 [hermes] /api/status Timed out connecting to Hermes backend'])
    rc = bt.main(['--home', str(tmp_path), '--dry-run', '--threshold', '1'],
                 now=datetime.fromisoformat('2026-11-04T06:39:00+00:00'),
                 power_probe=lambda n: {'intervals': []}, probe=lambda: {})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out['count'] == 1 and out['over_threshold']


@pytest.mark.parametrize('stamp', ['2026-11-03 22:30:00,000 +25:00', '2026-99-03 22:30:00,000 -08:00'])
def test_invalid_stamp_cannot_hide_timeout(tmp_path, capsys, stamp):
    put(tmp_path, [stamp + ' [hermes] /api/status Timed out connecting to Hermes backend'])
    rc = bt.main(['--home', str(tmp_path), '--dry-run'],
                 now=datetime.fromisoformat('2026-11-04T06:39:00+00:00'),
                 power_probe=lambda n: {'intervals': []})
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out['count'] is None


def test_no_logs_is_no_evidence_not_raw_zero(tmp_path, capsys):
    rc = bt.main(['--home', str(tmp_path), '--dry-run'],
                 now=datetime.fromisoformat('2026-11-04T06:39:00+00:00'),
                 power_probe=lambda n: {'intervals': []})
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out['count'] is None
