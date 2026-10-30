"""Exact directive adapter against real user-steps ledgers and session databases."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ASK_GATE_LIFECYCLE_DB", str(tmp_path / "shared.sqlite"))
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(get_hermes_home=lambda: Path(__import__('os').environ['HERMES_HOME'])))
    source = load("exact_source_test", HERE.parent / "user-steps" / "core.py")
    adapter = load("exact_adapter_test", HERE / "user_step_lifecycle.py")
    make_sessions(tmp_path)
    return adapter, source, tmp_path


def make_sessions(home):
    home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(home / "state.db") as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, source TEXT)")
        db.executemany("INSERT INTO sessions VALUES (?,?,?,?)", [
            ("root", None, "compression", "desktop"),
            ("current", "root", None, "desktop"),
            ("foreign", None, None, "desktop"),
        ])


def seed(source, home, statuses, root="root"):
    ledger = source.Ledger(home / "user-steps", home / "state.db")
    with ledger.edit(root) as d:
        d['steps'] = [dict(id=i, status=s, resolved_by="model", explicit_reopen=True) for i, s in statuses]
    return ledger


def check(adapter, source, ids=("us_abcdef",), s="current", **extra):
    return adapter.preflight(dict(session_id="current", draft=source.directive(ids, s, 'Please choose {one} "now".'), **extra))


def blocked(result, reason):
    assert result['action'] == 'block'
    assert result['reason'] == reason
    assert result['retry_message'] == result['message']
    assert 'not execution authorization' in result['message']


@pytest.mark.parametrize('status', ['resolved', 'missing'])
@pytest.mark.parametrize('attempt', [0, 99])
def test_final_hook_reconciles_exact_directive_before_delivery(env, gate, monkeypatch, status, attempt):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved")] if status == 'resolved' else [])
    monkeypatch.setattr(gate, '_gate_eligible', lambda *a: True)
    draft = source.directive(['us_abcdef'], 'foreign', 'Choose a setting.')
    result = gate.pre_final_gate(session_id='current', final_response=draft, attempt=attempt, hold_budget=0)
    assert result and 'replace' in result
    assert '::user-step' not in result['replace']
    assert 'authorization' in result['replace']


@pytest.mark.parametrize('fault', ['history', 'verdict'])
def test_exact_suppression_survives_unrelated_failures(env, gate, monkeypatch, fault):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved")])
    monkeypatch.setattr(gate, '_gate_eligible', lambda *a: True)
    def fail(*a, **k):
        raise ValueError('unrelated corrupt storage')
    monkeypatch.setattr(gate, '_user_msgs' if fault == 'history' else 'record_verdict', fail)
    draft = source.directive(['us_abcdef'], 'current')
    held = gate.pre_final_gate(session_id='current', final_response=draft)
    assert held and held.get('hold') and 'replace' not in held
    result = gate.pre_final_gate(session_id='current', final_response=draft, attempt=2)
    assert result and 'replace' in result and '::user-step' not in result['replace']


def test_resolved_real_source_suppresses_and_never_reopens(env):
    adapter, source, home = env
    ledger = seed(source, home, [("us_abcdef", "resolved")])
    before = ledger.path('root').read_bytes()
    blocked(check(adapter, source), 'user_step_retired')
    assert ledger.path('root').read_bytes() == before
    seed(source, home, [("us_abcdef", "open")])
    # A fresh module/process view and a newer model-claimed reopen cannot clear retirement.
    fresh = load('exact_adapter_fresh', HERE / 'user_step_lifecycle.py')
    blocked(check(fresh, source), 'user_step_retired')


def test_open_is_only_continuation_not_permission(env):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "open")])
    assert check(adapter, source) is None


@pytest.mark.parametrize('claimed', ['foreign', 'root', 'nonexistent'])
def test_forged_s_cannot_select_other_ledger(env, claimed):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "open")])
    seed(source, home, [("us_abcdef", "resolved")], root='foreign')
    assert check(adapter, source, s=claimed) is None
    seed(source, home, [("us_abcdef", "resolved")])
    blocked(check(adapter, source, s=claimed), 'user_step_retired')


def test_unknown_id_does_not_scan_other_roots(env):
    adapter, source, home = env
    seed(source, home, [])
    seed(source, home, [("us_abcdef", "resolved")], root='foreign')
    blocked(check(adapter, source), 'user_step_unavailable')


def test_multiple_ids_all_observed_despite_unknown_first(env):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved"), ("us_123456", "open")])
    blocked(check(adapter, source, ['us_000000', 'us_123456', 'us_abcdef']), 'user_step_retired')
    seed(source, home, [("us_abcdef", "open"), ("us_123456", "open")])
    blocked(check(adapter, source), 'user_step_retired')


def test_multiple_directives(env):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "open"), ("us_123456", "resolved")])
    draft = source.directive(['us_abcdef'], 'foreign') + '\n\n' + source.directive(['us_123456'], 'current')
    blocked(adapter.preflight({'session_id': 'current', 'draft': draft}), 'user_step_retired')


@pytest.mark.parametrize('bad', ['us_ABCDEF', 'us_abcde', 'us_' + 'a' * 33, ' us_abcdef', 'us_abcdef,', '../us_abcdef'])
def test_invalid_ids_are_not_normalized(env, bad):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved")])
    blocked(check(adapter, source, [bad]), 'user_step_unavailable')


@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'unrevisioned', 'duplicate', 'oversize', 'symlink', 'status'])
def test_read_failures_are_operational_holds(env, fault):
    adapter, source, home = env
    ledger = seed(source, home, [("us_abcdef", "open")])
    p = ledger.path('root')
    if fault == 'missing':
        p.unlink()
    elif fault == 'corrupt':
        p.write_text('{broken')
    elif fault == 'unrevisioned':
        d = json.loads(p.read_text()); del d['revision']; p.write_text(json.dumps(d))
    elif fault == 'duplicate':
        seed(source, home, [("us_abcdef", "open"), ("us_abcdef", "resolved")])
    elif fault == 'oversize':
        with ledger.edit('root') as d:
            d['padding'] = 'x' * source.MAX_LEDGER_BYTES
    elif fault == 'symlink':
        target = p.with_suffix('.backup'); p.rename(target); p.symlink_to(target)
    else:
        seed(source, home, [("us_abcdef", "approved")])
    result = check(adapter, source)
    blocked(result, 'user_step_unavailable')
    assert 'agent-owned recovery' in result['message']
    assert str(home) not in result['message']


@pytest.mark.parametrize('fault', ['missing_db', 'missing_session', 'missing_parent', 'cycle'])
def test_session_authority_failure_never_falls_back(env, fault):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved")], root='current')
    if fault == 'missing_db':
        (home / 'state.db').unlink()
    else:
        with sqlite3.connect(home / 'state.db') as db:
            if fault == 'missing_session':
                db.execute("DELETE FROM sessions WHERE id='current'")
            elif fault == 'missing_parent':
                db.execute("DELETE FROM sessions WHERE id='root'")
            else:
                db.execute("UPDATE sessions SET parent_session_id='current' WHERE id='root'")
                db.execute("UPDATE sessions SET end_reason='compression' WHERE id='current'")
    blocked(check(adapter, source), 'user_step_unavailable')


def test_profile_is_runtime_bound_even_with_shared_store(env, monkeypatch):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved")])
    blocked(check(adapter, source), 'user_step_retired')
    other = home / 'profiles' / 'other'
    make_sessions(other)
    seed(source, other, [("us_abcdef", "open")])
    monkeypatch.setenv('HERMES_HOME', str(other))
    assert check(adapter, source, profile='default', root='root', home=str(home)) is None
    monkeypatch.setenv('HERMES_HOME', str(home))
    blocked(check(adapter, source), 'user_step_retired')


def test_plain_prose_and_invented_grammar_are_unmanaged(env):
    adapter, source, home = env
    seed(source, home, [("us_abcdef", "resolved")])
    for draft in ['Please choose one.', '[[user-step id="us_abcdef" s="current"]]']:
        assert adapter.preflight({'session_id': 'current', 'draft': draft}) is None


def test_source_bootstrap_is_callable_and_real(env):
    adapter, source, home = env
    loaded = adapter.bootstrap()
    assert Path(loaded.__file__).resolve() == (HERE.parent / 'user-steps' / 'core.py').resolve()
    assert callable(loaded.Ledger.read_exact)


def test_malformed_directive_is_held(env):
    adapter, _, _ = env
    blocked(adapter.preflight({'session_id': 'current', 'draft': '::user-step{id="us_abcdef"'}), 'user_step_unavailable')


def test_real_process_reply_and_model_resolution(env):
    adapter, source, home = env
    ledger = source.Ledger(home / 'user-steps', home / 'state.db')
    reply = 'Which option do you want?'
    result = source.process_reply(reply, 'current', ledger=ledger, llm=lambda *_: json.dumps({
        'steps': [{'text': reply, 'kind': 'input', 'verbatim_span': reply}], 'resolved': []}))
    assert len(result['steps']) == 1
    context = {'session_id': 'current', 'draft': result['text']}
    assert adapter.preflight(context) is None
    ids = [s['id'] for s in result['steps']]
    assert source.resolve(ledger, 'root', ids, 'answered', 'model') == ids
    blocked(adapter.preflight(context), 'user_step_retired')


@pytest.mark.parametrize('change', ['rollback', 'conflict'])
def test_source_revision_cannot_erase_retirement(env, change):
    adapter, source, home = env
    ledger = seed(source, home, [('us_abcdef', 'resolved')])
    blocked(check(adapter, source), 'user_step_retired')
    original = ledger.path('root').read_text()
    seed(source, home, [('us_abcdef', 'open')])
    blocked(check(adapter, source), 'user_step_retired')
    if change == 'rollback':
        ledger.path('root').write_text(original)
    else:
        d = json.loads(ledger.path('root').read_text())
        d['steps'][0]['status'] = 'resolved'
        ledger.path('root').write_text(json.dumps(d))
    blocked(check(adapter, source), 'user_step_unavailable')
    # Recovery to a valid newer open snapshot still cannot reopen.
    seed(source, home, [('us_abcdef', 'open')])
    blocked(check(adapter, source), 'user_step_retired')


def test_noncompression_parent_is_not_the_owner(env):
    adapter, source, home = env
    with sqlite3.connect(home / 'state.db') as db:
        db.execute("UPDATE sessions SET end_reason='completed' WHERE id='root'")
    seed(source, home, [('us_abcdef', 'resolved')])
    seed(source, home, [('us_abcdef', 'open')], root='current')
    assert check(adapter, source) is None


@pytest.mark.parametrize('fault', ['source', 'store', 'home'])
def test_bootstrap_and_storage_failures_are_closed(env, monkeypatch, fault):
    adapter, source, home = env
    seed(source, home, [('us_abcdef', 'resolved')])
    def fail(*args, **kwargs):
        raise OSError('private operational detail')
    if fault == 'source':
        monkeypatch.setattr(adapter, 'bootstrap', fail)
    elif fault == 'store':
        monkeypatch.setenv('ASK_GATE_LIFECYCLE_DB', str(home))
    else:
        monkeypatch.setitem(sys.modules, 'hermes_constants', SimpleNamespace(get_hermes_home=fail))
    result = check(adapter, source)
    blocked(result, 'user_step_unavailable')
    assert 'private operational detail' not in result['message']


@pytest.mark.parametrize('bound', ['draft', 'ids'])
def test_work_is_bounded(env, bound):
    adapter, source, _ = env
    draft = (source.directive(['us_abcdef'], 'current', 'x' * (256 * 1024)) if bound == 'draft'
             else source.directive([f'us_{i:06x}' for i in range(129)], 'current'))
    blocked(adapter.preflight({'session_id': 'current', 'draft': draft}), 'user_step_unavailable')


def test_embedded_directive_text_is_not_another_identity(env):
    adapter, source, home = env
    seed(source, home, [('us_abcdef', 'open'), ('us_123456', 'resolved')])
    draft = source.directive(['us_abcdef'], 'current', source.directive(['us_123456'], 'current'))
    assert adapter.preflight({'session_id': 'current', 'draft': draft}) is None


def test_runtime_profile_context_beats_environment(env, monkeypatch):
    adapter, source, home = env
    seed(source, home, [('us_abcdef', 'resolved')])
    other = home / 'profiles' / 'other'
    make_sessions(other)
    seed(source, other, [('us_abcdef', 'open')])
    # A context-bound gateway profile may differ from process-global HERMES_HOME.
    monkeypatch.setitem(sys.modules, 'hermes_constants', SimpleNamespace(get_hermes_home=lambda: other))
    monkeypatch.delenv('ASK_GATE_LIFECYCLE_DB')
    assert check(adapter, source) is None
    assert (other / 'state' / 'ask-lifecycle.sqlite').is_file()
    assert not (home / 'state' / 'ask-lifecycle.sqlite').exists()
    monkeypatch.setitem(sys.modules, 'hermes_constants', SimpleNamespace(get_hermes_home=lambda: home))
    blocked(check(adapter, source), 'user_step_retired')


def test_draft_claims_cannot_supply_missing_runtime_session(env):
    adapter, source, home = env
    seed(source, home, [('us_abcdef', 'resolved')])
    blocked(adapter.preflight({'draft': source.directive(['us_abcdef'], 'current'),
                              'args': {'session_id': 'current'}}), 'user_step_unavailable')
