"""SessionState v2 P0: pin what session-repair's read-only scan says today for every shared core scenario.

Each scenario of core's ``tests/fixtures/session-state-v2/scenarios.json`` (found through HERMES_CORE_SOURCE) becomes
one card on its own real board in a private HERMES_HOME (core's ``kanban_db``), and ``scan.scan`` runs over the real
``Sources`` with the real session-panel readers. Runtime scenarios use the canonical core helper's published
envelope, a real state.db and actual runtime collectors; only source IO and the clock are isolated. The scanner's
``chats`` counts linked local-card chats, not cardless outcome-audit rows: those are recorded separately below.
Remote cards are read from a real replica and remain outside the scanner's owner-only card rules. The expected values in
``session_state_v2_goldens.json`` were generated from that execution and reviewed;
``SESSION_STATE_V2_UPDATE_GOLDENS=1`` regenerates them.
"""
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location('repair_session_state_v2_support', Path(__file__).with_name('session_state_v2_support.py'))
assert _spec is not None and _spec.loader is not None
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
from sr_helpers import Env

DATA, WHY = S.load_scenarios()
pytestmark = pytest.mark.skipif(DATA is None, reason=WHY or 'no compatible core')
if DATA is not None:
    pytest.importorskip('hermes_cli.kanban_db')


def observe(tmp_path, monkeypatch, scenario):
    now = DATA['now']
    monkeypatch.setattr(time, 'time', lambda: float(now))
    canonical = S.seed_runtime(tmp_path, DATA, scenario, monkeypatch) if scenario.get('card') is None else None
    env = Env(tmp_path, monkeypatch)
    env.now = float(now)
    ids = S.materialize(env.conn, env.state, DATA, scenario, now)
    remote_id, replica_rows = S.replica_case(DATA, scenario, monkeypatch, tmp_path)
    if remote_id:
        ids['card'] = remote_id
        assert env.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    audited = []
    obligation_reads = []
    if canonical:
        obligations = env.scan_mod.Sources.chat_obligations
        def observe_obligations(src, sid):
            actual = obligations(src, sid)
            obligation_reads.append({'chat': sid, 'live': actual})
            return actual
        monkeypatch.setattr(env.scan_mod.Sources, 'chat_obligations', observe_obligations)
        def outcome_rows(src):
            # Substitute CLI acquisition only, reading the real persisted canonical envelope.
            rows = [{'id': r[0], **json.loads(r[1])} for r in src.state_conn().execute(
                'SELECT id,outcome_json FROM sessions WHERE outcome_json IS NOT NULL')]
            audited.extend(r['id'] for r in rows)
            return rows
        monkeypatch.setattr(env.scan_mod.Sources, 'outcome_rows', outcome_rows)
    result = env.scan()
    assert result['error_count'] == 0, result['errors']
    findings = [{k: f[k] for k in ('id', 'variant', 'area', 'title', 'summary', 'chat', 'fingerprint')}
                | {'fix': f['fix'].get('kind')} for f in result['findings']]
    out = {'scanned_at': result['scanned_at'], 'chats': result['chats'], 'cards': result['cards'],
           'findings': findings, 'error_count': result['error_count'], 'errors': result['errors'],
           'progress': result['progress']}
    if replica_rows:
        out['replica'] = [{k: r[k] for k in ('id', 'source', 'remote', 'gateway', 'stale', 'synced_at', 'status')}
                          for r in replica_rows]
        # The owner-only scanner must not turn a remote replica into a local broken worker.
        assert result['cards'] == 0
    if canonical:
        assert DATA['identity']['session_id'] in audited
        out['outcome_audited_chats'] = sorted(set(audited))
        assert obligation_reads, 'quiet-chat classifier must reach the real runtime obligations reader'
        out['runtime_obligations'] = obligation_reads
        from hermes_cli import process_session_activity as psa, subagent_session_activity as ssa
        row = {'id': DATA['identity']['session_id'], 'outcome': canonical['outcome']}
        ssa.annotate_subagent_activity([row])
        psa.annotate_background_activity([row])
        out['runtime_session'] = row
        if scenario.get('runtime', {}).get('kind') == 'process':
            assert result['progress']['chats'] == 1, result
            assert row['background_processes'][0]['id'] == scenario['runtime']['id']
        if scenario.get('runtime', {}).get('kind') == 'subagent':
            assert any(o['identity']['work_id'] == scenario['runtime']['id'] and o['state'] == 'running'
                       for o in row['execution_observations']), row
    env.last_sources.readers.release()
    env.conn.close()
    env.state.close()
    text = json.dumps(out, sort_keys=True)
    for label, cid in ids.items():
        if cid:
            text = text.replace(cid, f'<{label}>')
    return json.loads(text)


@pytest.mark.parametrize('scenario', S.scenario_params(DATA))
def test_scenario_matches_golden(tmp_path, monkeypatch, scenario):
    seen = observe(tmp_path, monkeypatch, scenario)
    print(scenario['id'], json.dumps(seen, sort_keys=True))
    if os.environ.get(S.UPDATE):
        golden = json.loads(S.GOLDEN.read_text()) if S.GOLDEN.exists() else {}
        golden[scenario['id']] = seen
        S.GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + '\n')
        return
    assert seen == json.loads(S.GOLDEN.read_text())[scenario['id']]


def test_support_import_ignores_same_name_module_cache(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'session_state_v2_support', object())
    spec = importlib.util.spec_from_file_location('_repair_v2_collision_probe', __file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.S.GOLDEN == S.GOLDEN
    assert module.S.materialize.__code__.co_argcount == 5


def test_golden_covers_exactly_the_core_scenarios():
    assert sorted(json.loads(S.GOLDEN.read_text())) == sorted(s['id'] for s in DATA['scenarios'])
