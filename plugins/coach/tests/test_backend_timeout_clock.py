"""Clock regressions: immutable entry offsets replace the rejected inspector bridge.

An inspector's current Date cache cannot establish historical writer identity or
resolve repeated fall-back timestamps. No inspector fallback is supported.
"""
import importlib.util
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location('clock_bt', Path(__file__).resolve().parents[1] / 'backend_timeouts.py')
assert spec and spec.loader
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def test_aware_power_intervals_ignore_caller_timezone():
    now = datetime.fromisoformat('2026-11-04T06:39:00+00:00')
    text = '2026-11-03 22:20:00 -0800 Sleep Entering Sleep state\n2026-11-03 22:35:00 -0800 Wake Wake from Deep Idle'
    intervals = bt.power_intervals(text, now)
    assert intervals == [(datetime.fromisoformat('2026-11-04T06:20:00+00:00'), datetime.fromisoformat('2026-11-04T06:35:00+00:00'))]


def test_dst_fold_resolves_two_entries_and_excludes_outside_window(tmp_path):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/desktop.log').write_text(''.join(
        f'2026-11-01 01:30:00,000 {offset} [hermes] /api/status Timed out connecting to Hermes backend\n'
        for offset in ('-07:00', '-08:00')))
    now = datetime.fromisoformat('2026-11-01T09:40:00+00:00')
    sleep = [(datetime.fromisoformat('2026-11-01T08:00:00+00:00'), datetime.fromisoformat('2026-11-01T09:00:00+00:00'))]
    both = bt.scan(tmp_path, now, 120, sleep)
    assert both['count'] == 2
    assert both['sleep_count'] == both['awake_count'] == 1
    narrow = bt.scan(tmp_path, now, 30, sleep)
    assert narrow['count'] == narrow['awake_count'] == 1
    assert narrow['sleep_count'] == 0


def test_dst_spring_gap_is_not_a_missing_hour(tmp_path):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/desktop.log').write_text(
        '2026-03-08 01:55:00,000 -08:00 [hermes] /api/a Timed out connecting to Hermes backend\n'
        '2026-03-08 03:05:00,000 -07:00 [hermes] /api/b Timed out connecting to Hermes backend\n')
    now = datetime.fromisoformat('2026-03-08T10:10:00+00:00')
    assert bt.scan(tmp_path, now, 30)['count'] == 2
