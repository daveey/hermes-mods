"""The cron tick: modes, 24 h dedupe, card caps, the live repair turn (a fake `hermes chat`), its result block,
retry with backoff, bounded logs, and the turn argv parsed by core's own CLI parser."""
import json
import subprocess

import pytest

from sr_helpers import PLUGIN, load

run = load('_sr_run', PLUGIN / 'run.py')


def finding(i, kind='auto', rule='BS-overdue-wake', area='hermes'):
    card = f't_{i:016x}'
    return {'id': rule, 'variant': 'x', 'card': card, 'board': 'default', 'chat': 'S1', 'title': f'card {i}',
            'summary': 's', 'evidence': {}, 'fix': {'kind': kind, 'commands': [f'hermes kanban unblock {card}']},
            'area': area, 'fingerprint': f'{rule}:default/{card}'}


def scanner(findings):
    return lambda: {'findings': [dict(f) for f in findings], 'scanned_at': 1.0, 'errors': []}


def result_block(rows):
    return f'done.\n```session-repair-result\n{json.dumps({"results": rows})}\n```\n'


class FakeHermes:
    """`hermes chat` that reports `outcome` for every finding in the prompt (or prints `stdout` / exits `rc`)."""

    def __init__(self, rc=0, outcome='fixed', stdout=None, card_outcome='card'):
        self.calls, self.rc, self.outcome, self.stdout, self.card_outcome = [], rc, outcome, stdout, card_outcome

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out = ''
        if argv[1] == 'chat':
            if self.stdout is not None:
                out = self.stdout
            else:
                prompt = open(argv[argv.index('--query-file') + 1]).read()
                found = json.loads(prompt.rsplit('```json', 1)[1].rsplit('```', 1)[0])
                out = result_block([{'fingerprint': f['fingerprint'],
                                     'outcome': self.card_outcome if f['fix']['kind'] == 'card' else self.outcome,
                                     'card': 't_new' if f['fix']['kind'] == 'card' else None} for f in found])
        return subprocess.CompletedProcess(argv, self.rc, stdout=out, stderr='')

    def chats(self):
        return [c for c in self.calls if c[1] == 'chat']


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture(autouse=True)
def fake_bin(monkeypatch):
    monkeypatch.setenv('SESSION_REPAIR_HERMES', 'hermes')


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
    assert out['line'].startswith('session-repair (shadow): 2 new finding(s); would fix 1, file 1 card(s)')
    again = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                     now=2000.0, run_mode='shadow')
    assert again['new'] == 0 and again['line'] == '', 'deduped: silent'


def test_dedupe_expires_after_24_hours(tmp_path):
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0, run_mode='shadow')
    assert run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0 + 23 * 3600, run_mode='shadow')['new'] == 0
    assert run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0 + 25 * 3600, run_mode='shadow')['new'] == 1


def cards_in(handed):
    return sum(1 for fp in handed if int(fp.rsplit('_', 1)[1], 16) < 100)


def test_caps_five_cards_per_run_and_twenty_per_day(tmp_path):
    many = [finding(i, 'card') for i in range(30)] + [finding(100 + i) for i in range(3)]
    now = 10_000.0
    handed = [run.tick(home=tmp_path, scanner=scanner(many), now=now + k * 900, run_mode='shadow')['handed']
              for k in range(6)]
    assert [cards_in(h) for h in handed] == [5, 5, 5, 5, 0, 0], 'five per run, twenty per 24 h'
    assert len(handed[0]) - cards_in(handed[0]) == 3, 'safe fixes are not card-capped'
    later = run.tick(home=tmp_path, scanner=scanner(many), now=now + 25 * 3600, run_mode='shadow')
    assert cards_in(later['handed']) == 5, 'a new day: room again'
    assert len(run.select([finding(i) for i in range(25)], 0)[0]) == run.FIXES_PER_RUN


def test_live_cards_filed_for_failed_safe_fixes_count_against_the_day_cap(tmp_path):
    fixes = [finding(200 + i) for i in range(20)]
    hermes = FakeHermes(outcome='card')  # every safe fix "did not take" and became a card
    run.tick(home=tmp_path, scanner=scanner(fixes), runner=hermes, now=1000.0, run_mode='live')
    out = run.tick(home=tmp_path, scanner=scanner([finding(1, 'card')]), runner=hermes, now=2000.0, run_mode='live')
    assert out['handed'] == [] and out['deferred'] == 1, '20 cards today already: the card finding waits'


def test_live_starts_one_turn_with_the_prompt_and_marks_only_what_the_turn_handled(tmp_path):
    hermes = FakeHermes()
    out = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                   now=1000.0, run_mode='live')
    assert len(hermes.chats()) == 1
    argv = hermes.chats()[0]
    assert argv[:7] == ['hermes', 'chat', '-c', 'Session repair', '--create-if-missing', '--oneshot', '-Q']
    assert '-s' not in argv and '-t' not in argv, 'a resumed chat refuses creation flags: the skill is named in the prompt'
    prompt = (tmp_path / 'session-repair' / 'last-prompt.md').read_text()
    assert '{{' not in prompt and 'broken-sessions' in prompt
    assert 'session-repair:BS-overdue-wake:default/t_0000000000000002:19700101' in prompt, 'key with a UTC day bucket'
    assert sorted(out['handed']) == sorted([finding(1)['fingerprint'], finding(2, 'card')['fingerprint']])
    ledger = lines(tmp_path / 'session-repair' / 'ledger-live.jsonl')
    assert sorted(r['outcome'] for r in ledger) == ['card', 'fixed']
    assert run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2, 'card')]), runner=hermes,
                    now=2000.0, run_mode='live')['line'] == ''
    assert len(hermes.chats()) == 1, 'handled: no second turn'


def test_a_deferred_or_unreported_finding_is_not_marked_seen(tmp_path):
    fp1, fp2 = finding(1)['fingerprint'], finding(2)['fingerprint']
    hermes = FakeHermes(stdout=result_block([{'fingerprint': fp1, 'outcome': 'deferred', 'card': None}]))
    out = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2)]), runner=hermes, now=1000.0, run_mode='live')
    assert out['handed'] == []
    seen = json.loads((tmp_path / 'session-repair' / 'seen-live.json').read_text())
    assert fp1 not in seen and fp2 not in seen
    again = run.tick(home=tmp_path, scanner=scanner([finding(1), finding(2)]), runner=FakeHermes(), now=1100.0,
                     run_mode='live')
    assert again['handed'] == [fp1], 'deferred comes straight back; the unreported one waits out its backoff'


def test_failed_turns_back_off_exponentially_and_give_up_after_max_attempts(tmp_path):
    fps = [finding(1)]
    bad = FakeHermes(rc=1)
    t = 1000.0
    out = run.tick(home=tmp_path, scanner=scanner(fps), runner=bad, now=t, run_mode='live')
    assert out['failed'] and 'repair turn failed (rc=1)' in out['line']
    assert run.tick(home=tmp_path, scanner=scanner(fps), runner=bad, now=t + 600, run_mode='live')['handed'] == []
    assert len(bad.chats()) == 1, 'inside the 15 min backoff: no turn'
    delays = []
    for n in range(2, run.MAX_ATTEMPTS + 1):
        att = json.loads((tmp_path / 'session-repair' / 'attempts-live.json').read_text())
        nxt = att['fps'][fps[0]['fingerprint']]['next_at']
        delays.append(nxt - t)
        t = nxt + 1
        out = run.tick(home=tmp_path, scanner=scanner(fps), runner=bad, now=t, run_mode='live')
    assert delays == [900, 1800, 3600, 7200]
    assert len(bad.chats()) == run.MAX_ATTEMPTS and 'given up after 5 attempts' in out['line']
    assert fps[0]['fingerprint'] in json.loads((tmp_path / 'session-repair' / 'seen-live.json').read_text())
    assert [r['outcome'] for r in lines(tmp_path / 'session-repair' / 'ledger-live.jsonl')] == ['gave_up']


def test_a_turn_with_no_result_block_counts_as_failed(tmp_path):
    out = run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=FakeHermes(stdout='all done!'), now=1.0,
                   run_mode='live')
    assert out['failed'] and 'no result block' in out['line']


def test_shadow_and_live_dedupe_separately(tmp_path):
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), now=1000.0, run_mode='shadow')
    out = run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=FakeHermes(), now=1100.0, run_mode='live')
    assert out['handed'] == [finding(1)['fingerprint']], 'switching to live hands over what shadow only logged'


def test_logs_are_bounded(tmp_path):
    p = tmp_path / 'x.jsonl'
    for i in range(30):
        run._append(p, [{'i': i}], max_lines=10)
    assert [r['i'] for r in lines(p)] == list(range(20, 30))


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


def test_live_pins_the_repair_chat_once_it_exists(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('CREATE TABLE sessions (id TEXT, title TEXT, archived INTEGER, pinned INTEGER, started_at REAL)')
    c.execute("INSERT INTO sessions VALUES ('R1', 'Session repair', 0, 0, 5.0)")
    c.commit()
    hermes = FakeHermes()
    run.tick(home=tmp_path, scanner=scanner([finding(1)]), runner=hermes, now=1000.0, run_mode='live')
    assert ['hermes', 'sessions', 'pin', 'R1'] in hermes.calls
    assert json.loads((tmp_path / 'session-repair' / 'chat.json').read_text()) == {'session': 'R1'}
    c.execute("UPDATE sessions SET pinned = 1")
    c.commit()
    hermes2 = FakeHermes()
    run.tick(home=tmp_path, scanner=scanner([finding(2)]), runner=hermes2, now=2000.0, run_mode='live')
    assert not any(cmd[1] == 'sessions' for cmd in hermes2.calls), 'already pinned'


def test_parse_result_takes_the_last_block():
    text = result_block([{'fingerprint': 'a', 'outcome': 'deferred'}]) + result_block([{'fingerprint': 'a', 'outcome': 'fixed'}])
    assert run.parse_result(text) == {'a': {'outcome': 'fixed', 'card': None}}
    assert run.parse_result('no block') is None and run.parse_result('```session-repair-result\nnot json\n```') is None


def test_the_turn_argv_parses_with_cores_own_cli_parser(tmp_path):
    _parser = pytest.importorskip('hermes_cli._parser')
    parser, _subs, _chat = _parser.build_top_level_parser()
    args = parser.parse_args(run.turn_argv(tmp_path / 'p.md')[1:])
    assert args.command == 'chat' and args.continue_last == 'Session repair' and args.create_if_missing
    assert args.oneshot_exit and args.oneshot is None and args.quiet and args.source == 'cli' and args.query_file == str(tmp_path / 'p.md')
    notice_cmd = ['chat', '--resume', 'S9', '--oneshot', '-Q', '-q', '[Kanban close-out] x']
    a2 = parser.parse_args(notice_cmd)
    assert a2.resume == 'S9' and a2.oneshot_exit and a2.query == '[Kanban close-out] x'


def test_prompt_states_the_card_policy():
    text = (PLUGIN / 'prompt.md').read_text()
    for must in ("Never add\n     `self_work`", "self_work={version:1, repository:'hermes-mods', area:'session-repair', kind:'sweep'}",
                 'only when the\n     `kanban_create` schema exposes `self_work`', 'queued acknowledgment',
                 'never retry it undeclared', 'Hermes itself is the\n     exception', 'ONE comment on it',
                 'dispatcher, credential, quota/pacing or money path', 'why none of it\n     replaces this card',
                 "carries the UTC day", 'At most {{CARD_BUDGET}} cards this run in total,\n   cards for failed safe fixes included',
                 'never wake a\n   probe park without running its probe first', 'Never delete or archive',
                 '```{{RESULT_FENCE}}'):
        assert must in text, must
