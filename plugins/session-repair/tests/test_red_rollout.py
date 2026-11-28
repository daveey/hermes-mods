"""Scoped rollout must not promote neighboring nudge rules."""
import json
from types import SimpleNamespace
import pytest
from sr_helpers import PLUGIN, load

run = load('_sr_red_rollout', PLUGIN/'run.py')
progress = load('_sr_red_progress', PLUGIN/'progress.py')
RED = 'BS-red-no-ask'
OTHER = 'BS-long-wait'


@pytest.mark.parametrize('mode,override,allowed,soak,live', [
    ('live', [RED], [RED], [], True),
    ('live', [RED], [RED+':uncovered_red_cards'], [], True),
    ('shadow', [RED], [RED], [], False),
    ('off', [RED], [RED], [], False),
    ('live', RED, [RED], [], False),
    ('live', [RED], [OTHER], [], False),
    ('live', [RED], [RED], [RED], False),
])
def test_only_explicit_red_override(tmp_path, monkeypatch, mode, override, allowed, soak, live):
    sd = tmp_path/'session-repair'
    sd.mkdir()
    (sd/'config.json').write_text(json.dumps({'mode':mode,'nudge_mode':'shadow',
        'nudge_live_rules':override,'live_rules':allowed,'shadow_rules':soak}))
    calls = []
    def process(findings, **kw):
        selected = [f['id'] for f in findings if f['id'] not in kw['hold']]
        calls.append((kw.get('run_mode','shadow'), selected, kw['hold']))
        return [], [], kw.get('run_mode','shadow')
    monkeypatch.setattr(run, 'load_nudge', lambda: SimpleNamespace(config_mode=lambda _: 'shadow', process=process))
    monkeypatch.setattr(run, 'load_scan', lambda: SimpleNamespace(RULES=[RED, OTHER]))
    monkeypatch.setattr(run, 'hermes_argv', lambda: ['hermes'])
    findings = [{'id':r,'fingerprint':r+':chat/s','variant':'uncovered_red_cards',
                 'fix':{'kind':'nudge'},'area':'card','chat':'s','evidence':{}} for r in (RED,OTHER)]
    run.tick(home=tmp_path, scanner=lambda: {'findings':findings,'errors':[]}, now=100)
    writes = [ids for m,ids,_ in calls if m=='live']
    assert writes == ([[RED]] if live else [])
    if live:
        assert RED in calls[0][2]  # no duplicate shadow send/cadence
        assert OTHER in calls[1][2]  # no unrelated live pending recovery


@pytest.mark.parametrize('scan_errors', [[], ['source unreadable']])
def test_pending_delivery_is_not_a_scanner_error_and_modes_are_scoped(tmp_path, monkeypatch, scan_errors):
    sd = tmp_path/'session-repair'
    sd.mkdir()
    (sd/'config.json').write_text(json.dumps({'mode':'live','nudge_mode':'shadow',
        'nudge_live_rules':[RED], 'live_rules':[RED]}))
    def process(findings, **kw):
        mode = kw.get('run_mode', 'shadow')
        rule = RED if mode == 'live' else OTHER
        return [{'fingerprint':rule+':chat/s', 'mode':mode,
                 'outcome':'delivery_unknown' if mode == 'live' else 'would_nudge',
                 'pending_at':100}], [], mode
    monkeypatch.setattr(run, 'load_nudge', lambda: SimpleNamespace(config_mode=lambda _: 'shadow', process=process))
    monkeypatch.setattr(run, 'load_scan', lambda: SimpleNamespace(RULES=[RED, OTHER]))
    monkeypatch.setattr(run, 'hermes_argv', lambda: ['hermes'])
    out = run.tick(home=tmp_path, scanner=lambda: {'findings':[], 'errors':scan_errors}, now=100)
    assert not out.get('failed', False)  # scanner errors remain in their own journal field
    assert out['pending_delivery'] == [{'fingerprint':RED+':chat/s', 'mode':'live', 'pending_at':100}]
    assert 'BS-red-no-ask (live): 1' in out['line']
    assert 'BS-long-wait (shadow): 1' in out['line']
    tick = json.loads((sd/'ticks.jsonl').read_text().splitlines()[-1])
    assert tick['pending_delivery'] == out['pending_delivery']
    assert tick['error_count'] == len(scan_errors)
    assert tick['errors'] == scan_errors
    last = json.loads((sd/'last.json').read_text())
    assert last['pending_delivery'] == out['pending_delivery']


def test_valid_ask_keeps_valid_red_but_uncovered_hold_does_not():
    cards = {'c':{'prediction':{'terminal':True}}}
    assert progress.classify(cards, False) == 'valid_red'
    assert progress.classify(cards, False, red_no_ask=True) == 'red_no_ask'
