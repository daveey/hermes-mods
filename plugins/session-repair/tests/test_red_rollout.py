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


def test_valid_ask_keeps_valid_red_but_uncovered_hold_does_not():
    cards = {'c':{'prediction':{'terminal':True}}}
    assert progress.classify(cards, False) == 'valid_red'
    assert progress.classify(cards, False, red_no_ask=True) == 'red_no_ask'
