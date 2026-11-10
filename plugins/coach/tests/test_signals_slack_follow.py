"""Follower stall records are machine signals, not ordinary warning text."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('slack_signals_test', Path(__file__).parents[1] / 'signals.py')
signals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signals)


def test_stall_signal_and_false_positive_boundaries():
    msg = 'SLACK_FOLLOW_STALLED engagement=eng_0123abcd fingerprint=' + 'a' * 64 + ' failures=3 rc=2: Error: frozen policy'
    prefix = '2026-11-01 10:00:00,000 ERROR hermes_plugins.slack_workspace.follow: '
    rows = signals.parse_errors([prefix + msg], host='example', prof='bot')
    assert len(rows) == 1
    assert rows[0]['component'] == 'plugin'
    assert 'eng_0123abcd' in rows[0]['symptom']
    assert 'frozen policy' in rows[0]['details']
    assert signals.to_defect(rows[0])
    for line in [msg, prefix.replace('ERROR', 'WARNING') + msg,
                 prefix.replace('hermes_plugins.slack_workspace.follow', 'agent.tool_executor') + msg,
                 prefix + 'quoted: ' + msg,
                 prefix + 'slack-follow eng_0123abcd turn failed rc=2: banner']:
        assert signals.parse_errors([line]) == []


def test_collector_reads_stall_once_from_profile_log(tmp_path):
    now = signals._ts('2026-11-01 10:00:00,000')
    log = tmp_path / 'profiles' / 'bot' / 'logs' / 'errors.log'
    log.parent.mkdir(parents=True)
    log.write_text('2026-11-01 10:00:00,000 ERROR hermes_plugins.slack_workspace.follow: '
                   'SLACK_FOLLOW_STALLED engagement=eng_0123abcd fingerprint=' + 'b' * 64 +
                   ' failures=3 rc=2: Error: frozen policy\n')
    cursor = {}
    got = signals.collect_errors(tmp_path, cursor, now, 'example')
    assert len(got) == 1 and got[0]['profile'] == 'bot'
    assert signals.collect_errors(tmp_path, cursor, now, 'example') == []
