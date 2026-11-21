"""The cron tick: modes, 24 h dedupe, card caps, the live repair turn (a fake `hermes chat`), and retry on failure."""
import json
import subprocess

from sr_helpers import PLUGIN, load

run = load('_sr_run', PLUGIN / 'run.py')


def finding(i, kind='auto', rule='BS-overdue-wake'):
    card = f't_{i:016x}'
    return {'id': rule, 'variant': 'x', 'card': card, 'board': 'default', 'chat': 'S1', 'title': f'card {i}',
            'summary': 's', 'evidence': {}, 'fix': {'kind': kind, 'commands': [f'hermes kanban unblock {card}']},
            'fingerprint': f'{rule}:default/{card}'}


def scanner(findings):
    return lambda: {'findings': list(findings), 'scanned_at': 1.0, 'errors': []}


class FakeHermes:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, stdout='ok', stderr='')


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_off_does_nothing(tmp_path):
    out = run.tick(home=tmp_path, scanner=lambda: 1 / 0, run_mode='off')
    assert out['line'] == '' and not (tmp_path / 'session-repair').exists()


def test_mode_comes_from_env_then_config_then_shadow(tmp_path, monkeypatch):
    monkeypatch.delenv('SESSION_REPAIR', raising=False)
    assert run.mode(tmp_path) == 'shadow'
    (tmp_path / 'session-repair').mkdir()
    (tmp_path / 'session-repair' / 'config.json').write_text('{"mode": "live"}')
    assert run.mode(tmp_path) == 'live'
    monkeypatch.setenv('SESSION_REPAIR', 'off')
    assert run.mode(tmp_path) == 'off'
    monkeypatch.setenv('SESSION_REPAIR', 'bogus')
    assert run.mode(tmp_path) == 'shadow'


def test_shadow_logs_would_fix_and_would_file_and_never_starts_a_turn(tmp_path):
    hermes = FakeHermes()
    out = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                   now=1000.0, run_mode='shadow')
    assert hermes.calls == [], 'shadow never starts the repair turn'
    rows = lines(tmp_path / 'session-repair' / 'actions-shadow.jsonl')
    assert sorted(r['would'] for r in rows) == ['file', 'fix']
    assert out['line'].startswith('session-repair (shadow): 2 new finding(s); would fix 1 fix(es), 1 card(s)')
    again = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                     now=2000.0, run_mode='shadow')
    assert again['new'] == 0 and again['line'] == '', 'deduped: silent'
    assert len(lines(tmp_path / 'session-repair' / 'actions-shadow.jsonl')) == 2


def test_dedupe_expires_after_24_hours(tmp_path):
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0, run_mode='shadow')
    assert run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0 + 23 * 3600, run_mode='shadow')['new'] == 0
    assert run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0 + 25 * 3600, run_mode='shadow')['new'] == 1


def test_caps_five_cards_per_run_and_twenty_per_day(tmp_path):
    many = [finding(i, 'card') for i in range(30)] + [finding(100 + i) for i in range(3)]
    now = 10_000.0
    handed = []
    for k in range(6):
        out = run.tick(home=tmp_path, scanner=scanner(many), now=now + k * 900, run_mode='shadow')
        handed.append(out['handed'])
    per_run_cards = [sum(1 for fp in h if int(fp.rsplit('_', 1)[1], 16) < 100) for h in handed]
    assert per_run_cards == [5, 5, 5, 5, 0, 0], 'five per run, twenty per 24 h'
    assert sum(1 for fp in handed[0] if int(fp.rsplit('_', 1)[1], 16) >= 100) == 3, 'safe fixes are not card-capped'
    later = run.tick(home=tmp_path, scanner=scanner(many), now=now + 25 * 3600, run_mode='shadow')
    assert sum(1 for fp in later['handed'] if int(fp.rsplit('_', 1)[1], 16) < 100) == 5, 'a new day: room again'
    assert run.select([finding(i) for i in range(25)], 0)[0].__len__() == run.FIXES_PER_RUN


def test_live_starts_one_turn_in_the_session_repair_chat_with_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv('SESSION_REPAIR_HERMES', 'hermes')
    hermes = FakeHermes()
    out = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                   now=1000.0, run_mode='live')
    chat = [c for c in hermes.calls if c[1] == 'chat']
    assert len(chat) == 1
    argv = chat[0]
    assert argv[:7] == ['hermes', 'chat', '-c', 'Session repair', '--create-if-missing', '--oneshot', '-Q']
    assert '-s' not in argv and '-t' not in argv, 'a resumed chat refuses creation flags: the skill is named in the prompt'
    prompt = (tmp_path / 'session-repair' / 'last-prompt.md').read_text()
    assert argv[argv.index('--query-file') + 1].endswith('last-prompt.md')
    assert 'broken-sessions' in prompt and finding(2, 'card')['fingerprint'] in prompt
    assert "idempotency_key='session-repair:<fingerprint>'" in prompt and "area:'session-repair'" in prompt
    assert '{{' not in prompt
    assert out['line'].startswith('session-repair (live): 2 new finding(s); handed to the repair chat:')
    assert run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                    now=2000.0, run_mode='live')['line'] == ''
    assert len([c for c in hermes.calls if c[1] == 'chat']) == 1, 'deduped: no second turn'


def test_live_turn_failure_keeps_findings_for_the_next_tick(tmp_path, monkeypatch):
    monkeypatch.setenv('SESSION_REPAIR_HERMES', 'hermes')
    bad = FakeHermes(rc=1)
    out = run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=bad, now=1000.0, run_mode='live')
    assert out.get('failed') and 'repair turn failed' in out['line']
    good = FakeHermes()
    out = run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=good, now=1900.0, run_mode='live')
    assert out['handed'] == [finding(1)['fingerprint']] and [c[1] for c in good.calls][0] == 'chat'


def test_shadow_and_live_dedupe_separately(tmp_path, monkeypatch):
    monkeypatch.setenv('SESSION_REPAIR_HERMES', 'hermes')
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0, run_mode='shadow')
    out = run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=FakeHermes(), now=1100.0, run_mode='live')
    assert out['handed'] == [finding(1)['fingerprint']], 'switching to live hands over what shadow only logged'


def test_last_json_is_written_for_the_findings_route(tmp_path):
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0, run_mode='shadow')
    last = json.loads((tmp_path / 'session-repair' / 'last.json').read_text())
    assert last['mode'] == 'shadow' and last['findings'][0]['fingerprint'] == finding(1)['fingerprint']
    assert last['new'] == last['handed'] == [finding(1)['fingerprint']]


def test_a_tick_holding_the_lock_makes_a_second_one_skip(tmp_path):
    sd = run.state_dir(tmp_path)
    with run._lock(sd) as held:
        assert held
        out = run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1.0, run_mode='shadow')
    assert out.get('skipped') == 'locked' and out['line'] == ''


def test_live_pins_the_repair_chat_once_it_exists(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv('SESSION_REPAIR_HERMES', 'hermes')
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('CREATE TABLE sessions (id TEXT, title TEXT, archived INTEGER, pinned INTEGER, started_at REAL)')
    c.execute("INSERT INTO sessions VALUES ('R1', 'Session repair', 0, 0, 5.0)")
    c.commit()
    hermes = FakeHermes()
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=hermes, now=1000.0, run_mode='live')
    assert ['hermes', 'sessions', 'pin', 'R1'] in hermes.calls
    c.execute("UPDATE sessions SET pinned = 1")
    c.commit()
    hermes2 = FakeHermes()
    run.tick(home=tmp_path, scanner=scanner([finding(2)]), runner=hermes2, now=2000.0, run_mode='live')
    assert not any(cmd[1] == 'sessions' for cmd in hermes2.calls), 'already pinned'
