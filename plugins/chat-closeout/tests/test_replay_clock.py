"""Replay must age production deadlines, not just schedule calls faster."""
import importlib.util
from pathlib import Path
import time
import sys
import os

import pytest


spec = importlib.util.spec_from_file_location(
    'closeout_replay_clock_tests', Path(__file__).resolve().parents[1] / 'bench' / 'replay_bench.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.mark.parametrize('diagnostic, expected', [(False, 1), (True, 0)])
def test_cli_budget_exit(world, monkeypatch, diagnostic, expected, tmp_path):
    # Real fixture/replay path; force only the budget verdict, never the clocks or CPU timers.
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'must-not-open.db'))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'must-not-read'))
    previous = dict(os.environ)
    load = bench.load_plugin
    def isolated_load(path):
        from hermes_cli import kanban_session_activity as ksa
        home = Path(os.environ['HERMES_HOME'])
        assert Path(os.environ['HERMES_KANBAN_DB']) == home / 'kanban.db'
        assert ksa._hub_db_path() == home / 'hub.db'
        return load(path)
    monkeypatch.setattr(bench, 'load_plugin', isolated_load)
    monkeypatch.setattr(bench, 'budget_failures', lambda values: ['forced overrun'])
    argv = ['replay_bench', '--sessions', '4', '--messages', '8', '--tasks', '3',
            '--replica-rows', '3', '--pending', '2']
    if diagnostic:
        argv.append('--diagnostic')
    monkeypatch.setattr(sys, 'argv', argv)
    assert bench.main() == expected
    assert dict(os.environ) == previous


@pytest.fixture
def world():
    pytest.importorskip('hermes_state')


def test_replay_clock_reaches_imported_readers_and_restores_on_error():
    from test_closeout import plugin, core

    original = {name: getattr(time, name) for name in (
        'time', 'time_ns', 'monotonic', 'monotonic_ns',
        'process_time', 'thread_time', 'perf_counter', 'perf_counter_ns')}
    with pytest.raises(RuntimeError, match='restore'):
        with bench.ReplayClock(wall=1_800_000_000, mono=100) as clock:
            # All modules imported before installation see the same module clock.
            assert plugin.time.time() == core.time.time() == 1_800_000_000
            assert plugin.time.monotonic() == 100
            clock.advance(600)
            assert core.time.time() == 1_800_000_600
            assert plugin.time.monotonic() == 700
            assert time.time_ns() == 1_800_000_600_000_000_000
            assert time.monotonic_ns() == 700_000_000_000
            for name in ('process_time', 'thread_time', 'perf_counter', 'perf_counter_ns'):
                assert getattr(time, name) is original[name]
            with pytest.raises(ValueError):
                clock.advance(-1)
            raise RuntimeError('restore')
    assert all(getattr(time, name) is value for name, value in original.items())


def test_budgets_fail_closed_unless_diagnostic():
    assert bench.BUDGET_PCT == {'waiting': 0.2, 'host': 0.5}
    assert bench.budget_failures({'waiting': 0.2, 'host': 0.5}) == []
    over = bench.budget_failures({'waiting': 0.201, 'host': 0.6})
    assert len(over) == 2 and over[0].startswith('waiting') and over[1].startswith('host')
    assert bench.budget_failures({'waiting': 0.1, 'host': 0.51}) == [
        'host 0.510% > 0.5% of one core']
