"""Portable scanner coverage runs even without an installed Desktop checkout."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from test_confirm_plan import desktop_sources

PARSER = Path(__file__).resolve().parents[1] / 'parser'


def test_portable_desktop_parser_smoke():
    assert shutil.which('node'), 'Node is required for the shipped Desktop parser bridge'
    result = subprocess.run(['node', str(PARSER / 'smoke.mjs')], cwd=PARSER, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '16 scanner smoke cases passed' in result.stdout


def test_bundle_matches_recorded_provenance():
    manifest = json.loads((PARSER / 'manifest.json').read_text())
    assert len(manifest['modules']) == 12
    assert hashlib.sha256((PARSER / 'scanner.mjs').read_bytes()).hexdigest() == manifest['bundle_sha256']


@pytest.mark.parametrize('fault', ['stale-turn', 'multiple-decide', 'forged-id', 'wrong-session', 'mixed-raw'])
def test_producer_identity_and_cardinality_never_authorize(tmp_path, fault, desktop_sources):
    from test_confirm_plan import core, OWNER, PLAN, ASK, directive
    ledger = core.Ledger(tmp_path / 'ledger', db=tmp_path / 'missing.db')
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    with ledger.edit(ledger.root(OWNER['storedSessionId'])) as data:
        data['publication_turn'] = 'first'
    step = core.add_step(OWNER['storedSessionId'], ASK, ['Go', 'Change plan'], ledger=ledger)
    text = directive(step)
    if fault == 'stale-turn':
        with ledger.edit(ledger.root(OWNER['storedSessionId'])) as data:
            data['publication_turn'] = 'second'
    elif fault == 'multiple-decide':
        core.add_step(OWNER['storedSessionId'], 'Approve a different build?', ['Go', 'Change plan'], ledger=ledger)
    elif fault == 'forged-id':
        text = text.replace(step['id'], 'forged-id')
    elif fault == 'wrong-session':
        text = text.replace(OWNER['storedSessionId'], 'other-session')
    else:
        text = directive()
    core.attach_confirmation_plans(PLAN + '\n\n' + text, OWNER['storedSessionId'], ledger, store, OWNER)
    raw = ledger.open_steps(OWNER['storedSessionId'])
    assert not any(s.get('confirmation_plan') for s in raw)
    assert not any(r['scope']['complete'] for r in store.requests(OWNER))


def test_current_turn_source_hash_drift_is_not_an_optional_skip(tmp_path, monkeypatch):
    from test_confirm_plan import core, PLAN, ASK
    root = tmp_path / 'core'
    source = root / 'apps/desktop/src/lib/transcript-directives.ts'
    source.parent.mkdir(parents=True)
    source.write_text('// divergent source')
    monkeypatch.setenv('HERMES_CORE_CHECKOUT', str(root))
    assert core.structured_confirmation(PLAN + '\n\n::user-step{t="' + core.enc(ASK) + '"}') is None
