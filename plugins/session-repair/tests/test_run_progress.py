"""BS-no-progress rollout in the tick: snapshots persisted by run.py (never by the scan), and the rule soaks in
shadow (log only) whatever mode / nudge_mode say until config.json `shadow_rules` drops it."""
import json
import subprocess

from sr_helpers import PLUGIN, load

run = load('_sr_run_progress', PLUGIN / 'run.py')


def np_finding(kind, variant, card='t_00000000000000aa'):
    return {'id': 'BS-no-progress', 'variant': variant, 'card': card, 'board': 'default', 'chat': 'S1',
            'title': 'c', 'summary': 'no progress', 'area': 'card', 'fingerprint': f'BS-no-progress:default/{card}',
            'evidence': {'status': 'running', 'last_event': {'id': 1}, 'expect': 'an event', 'waited_s': 1900},
            'fix': {'kind': kind, 'commands': []}}


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


class Boom:
    """Any board/chat write or repair turn would come through here: the soak must make none."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')


def scan_with(findings, snapshots=None, verdicts=None):
    return lambda: {'findings': [dict(f) for f in findings], 'errors': [], 'chats': 1, 'cards': 1,
                    'progress': {'chats': 1, 'verdicts': verdicts or {'missed_once': 1}},
                    '_progress_snapshots': snapshots or {}}


def config(home, **kw):
    (home / 'session-repair').mkdir(parents=True, exist_ok=True)
    (home / 'session-repair' / 'config.json').write_text(json.dumps(kw))


def test_soak_logs_only_even_when_everything_else_is_live(tmp_path, monkeypatch):
    monkeypatch.setenv('SESSION_REPAIR_HERMES', 'hermes')
    config(tmp_path, mode='live', nudge_mode='live')
    boom = Boom()
    found = [np_finding('nudge', 'missed_once'), np_finding('card', 'missed_twice', card='t_00000000000000bb')]
    out = run.tick(home=tmp_path, scanner=scan_with(found), runner=boom, run_mode='live')
    assert boom.calls == [], 'no comment, wake or repair turn while the rule soaks'
    rows = lines(tmp_path / 'session-repair' / 'actions-shadow.jsonl')
    assert [(r['rule'], r['variant'], r['would'], r['outcome']) for r in rows] == [
        ('BS-no-progress', 'missed_once', 'nudge', 'rule_shadow'),
        ('BS-no-progress', 'missed_twice', 'file', 'rule_shadow')]
    assert 'BS-no-progress:missed_once 1' in out['line'] and 'BS-no-progress:missed_twice 1' in out['line']
    tick = lines(tmp_path / 'session-repair' / 'ticks.jsonl')[-1]
    assert tick['progress'] == {'missed_once': 1} and len(tick['rule_shadow']) == 2
    state = json.loads((tmp_path / 'session-repair' / 'nudges-live.json').read_text())
    assert not any(fp.startswith('BS-no-progress') for fp in state['fps']), 'the nudge lane never saw it'


def test_soak_is_silent_without_findings_but_journals_verdicts(tmp_path):
    out = run.tick(home=tmp_path, scanner=scan_with([], verdicts={'healthy_running': 3, 'valid_red': 1}),
                   run_mode='shadow')
    assert out['line'] == ''
    assert lines(tmp_path / 'session-repair' / 'ticks.jsonl')[-1]['progress'] == {'healthy_running': 3, 'valid_red': 1}


def test_tick_persists_snapshots_and_keeps_them_out_of_last_json(tmp_path):
    snap = {'version': 1, 'chat': 'S1', 'at': 5.0, 'cards': {}, 'predictions': []}
    run.tick(home=tmp_path, scanner=scan_with([], snapshots={'S1': snap}), now=5.0, run_mode='shadow')
    assert json.loads((tmp_path / 'state' / 'session-progress' / 'S1.json').read_text()) == snap
    assert '_progress_snapshots' not in json.loads((tmp_path / 'session-repair' / 'last.json').read_text())


def test_ending_the_soak_routes_the_rule_like_any_other(tmp_path, monkeypatch):
    config(tmp_path, mode='shadow', nudge_mode='shadow', shadow_rules=[])
    out = run.tick(home=tmp_path, scanner=scan_with([np_finding('card', 'missed_twice')]), run_mode='shadow')
    rows = lines(tmp_path / 'session-repair' / 'actions-shadow.jsonl')
    assert [(r['rule'], r['would']) for r in rows] == [('BS-no-progress', 'file')], 'the ordinary path took it'
    assert out['handed'] == ['BS-no-progress:default/t_00000000000000aa']


def test_malformed_shadow_rules_keeps_the_soak(tmp_path):
    config(tmp_path, shadow_rules='none')
    assert run.shadow_rules(tmp_path) == run.DEFAULT_SHADOW_RULES
    config(tmp_path, live_rules=['BS-long-wait:too_long'])
    assert 'BS-no-progress' in run.shadow_rules(tmp_path)
