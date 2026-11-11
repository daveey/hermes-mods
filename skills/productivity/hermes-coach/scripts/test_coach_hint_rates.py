"""Hint rates join outcomes to their fire cohort, never missing == ignored."""
import importlib.util
from pathlib import Path


def module():
    p = Path(__file__).with_name('coach_hint_rates.py')
    s = importlib.util.spec_from_file_location('hint_rates_test', p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def fire(hid='h1', ts=100, rule='poll_loop', source='host/a'):
    return dict(event='agent_hint', hint_id=hid, ts=ts, rule=rule, session_id='s', source=source)


def outcome(hid='h1', heeded=True, ts=110, **kw):
    return dict(fire(hid, ts, **kw), event='agent_hint_outcome', heeded=heeded, evidence='five tool calls')


def test_cohort_dedupe_missing_legacy_and_orphans():
    rows = [fire(), fire(), outcome(), outcome(), fire('h2'), fire(None),
            outcome('orphan'), fire('old', 10), outcome('old'), outcome('h2', ts=500)]
    stats = module().stats(rows, 50, 200)['poll_loop']
    assert stats == dict(fires=3, measured=1, heeded=1, unresolved=1, legacy=1, rate=1.0)


def test_conflicting_bool_and_wrong_session_never_count():
    wrong = outcome('h2'); wrong['session_id'] = 'other'
    rows = [fire(), outcome(), outcome(heeded=False), fire('h2'), wrong,
            fire('h3'), dict(outcome('h3'), heeded='true')]
    assert module().stats(rows, 50, 200)['poll_loop']['measured'] == 0


def test_host_identity_isolation_and_false_rate():
    rows = [fire(), outcome(), fire(source='host/b'), outcome(heeded=False, source='host/b')]
    s = module().stats(rows, 50, 200)['poll_loop']
    assert (s['fires'], s['measured'], s['heeded'], s['rate']) == (2, 2, 1, .5)


def test_render_no_data_is_na_not_zero():
    text = '\n'.join(module().section([], 200, 7))
    assert 'no_checkpoint' in text and 'poll_loop' in text and 'n/a' in text
    assert 'unmeasured' in text and 'not evidence of ineffectiveness' in text


def test_read_logs_profiles_and_duplicate_homes(tmp_path):
    import json
    p = tmp_path / 'coach/log.jsonl'; p.parent.mkdir()
    p.write_text(json.dumps(fire())+'\nmalformed\n'+json.dumps(outcome())+'\n')
    rows = module().load([tmp_path, tmp_path])
    assert len(rows) == 2
    assert module().stats(rows, 50, 200)['poll_loop']['measured'] == 1


def test_weekly_cli_contains_hint_section(tmp_path):
    import subprocess
    import sys
    script = Path(__file__).with_name('coach_weekly.py')
    p = subprocess.run([sys.executable, str(script), '--data', str(tmp_path),
                        '--home', str(tmp_path), '--guard-home', str(tmp_path),
                        '--no-report-only'], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert '## Agent hint outcomes' in p.stdout
    assert '| no_checkpoint | 0 | 0 | 0 | n/a |' in p.stdout
