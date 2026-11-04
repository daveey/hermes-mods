"""Command entry-path and bounded raw-zero evidence regressions."""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'backend_timeouts.py'
spec = importlib.util.spec_from_file_location('coverage_bt', MODULE)
assert spec and spec.loader
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)
NOW = datetime.fromisoformat('2026-11-04T06:39:00+00:00')


@pytest.mark.parametrize('content', ['', '2026-11-03 22:38:00,000 -08:00 [hermes] started\n'])
def test_zero_needs_log_coverage_before_window(tmp_path, capsys, content):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/desktop.log').write_text(content)
    rc = bt.main(['--home', str(tmp_path), '--dry-run'], now=NOW,
                 power_probe=lambda n: {'intervals': []})
    assert rc == 2
    assert json.loads(capsys.readouterr().out)['count'] is None


def test_bounded_offset_zero_is_valid(tmp_path, capsys):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/desktop.log').write_text(
        '2026-11-03 22:00:00,000 -08:00 [hermes] started\n'
        '2026-11-03 22:38:00,000 -08:00 [hermes] healthy\n')
    assert bt.main(['--home', str(tmp_path), '--dry-run'], now=NOW,
                   power_probe=lambda n: {'intervals': []}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['count'] == 0
    assert out['clock']['coverage_start'] == '2026-11-04T06:00:00+00:00'


@pytest.mark.parametrize('zone', ['America/New_York', 'America/Los_Angeles'])
def test_literal_cli_ignores_callers_timezone(tmp_path, zone):
    (tmp_path / 'logs').mkdir()
    ts = datetime.now(timezone(timedelta(hours=-8))) - timedelta(minutes=1)
    (tmp_path / 'logs/desktop.log').write_text(
        ts.strftime('%Y-%m-%d %H:%M:%S,000 -08:00') +
        ' [hermes] /api/status conn=remote Timed out connecting to Hermes backend after 2000ms\n')
    result = subprocess.run([sys.executable, str(MODULE), '--home', str(tmp_path), '--dry-run', '--window', '30'],
                            env={**os.environ, 'TZ': zone}, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr + result.stdout
    out = json.loads(result.stdout)
    assert out['count'] == 1
    assert out['window_start'].endswith('+00:00')


def test_daily_key_keeps_host_local_date(monkeypatch):
    import time
    old = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ', 'America/Los_Angeles')
        time.tzset()
        assert bt.card_key('test-host', datetime.fromisoformat('2026-11-05T00:01:00+00:00')) == 'backend-timeouts:test-host:2026-11-04'
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()


def test_hours_separates_legacy_wall_hours_from_utc(tmp_path):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/desktop.log').write_text(
        '2026-11-03 22:30:00,000 [hermes] Timed out connecting to Hermes backend\n'
        '2026-11-03 22:30:00,000 -08:00 [hermes] Timed out connecting to Hermes backend\n')
    assert bt.hours(tmp_path) == {'2026-11-03 22': 1, '2026-11-04 06+00:00': 1}
