"""Behavioral regressions for independent source review B1–B5."""
import importlib.util
import multiprocessing
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('honest_source_review', HERE / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
core, consent = plugin.core, plugin.consent_mod
OWN, OTHER = 't_12345678', 't_87654321'
DENIED = 'BLOCKED: The user has NOT consented. Silence is not consent.'


def stop(env, session='chat-a', target=OWN):
    consent.record('kanban_comment', {'task_id': target}, DENIED, session, env=env)


@pytest.mark.parametrize('target', ['t_1234""5678', "t_1234'5678'", 't_1234\\5678'])
def test_b1_semantic_quoted_target_is_stopped(tmp_path, target):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    assert consent.check_relay('terminal', {'command': f"hermes kanban comment {target} 'retry'"}, ['chat-a'], env)


def test_b1_quoted_denial_records_semantic_target(tmp_path):
    env = {'HERMES_HOME': str(tmp_path)}
    consent.record('terminal', {'command': 'hermes kanban comment t_1234""5678 retry'}, DENIED, 'chat-a', env)
    assert consent.check_relay('kanban_comment', {'task_id': OWN}, ['chat-a'], env)


@pytest.mark.parametrize('command', [
    'hermes-hub request hub tasks default/t_1234""5678 comment retry',
    "bash -c 'hermes kanban comment t_1234\"\"5678 retry'",
    "bash -c 'hermes-hub request hub tasks default/t_1234\"\"5678 comment retry'",
])
def test_b1_nested_hub_and_shell_targets(tmp_path, command):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env)


def test_b1_unresolved_write_cannot_skip_active_stop(tmp_path):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    assert consent.check_relay('terminal', {'command': 'hermes kanban comment "$TARGET" retry'}, ['chat-a'], env)
    assert consent.check_relay('terminal', {'command': f'hermes kanban comment {OTHER} retry'}, ['chat-a'], env) is None
    assert consent.check_relay('terminal', {'command': f"printf '%s' 'hermes kanban comment {OWN} retry'"}, ['chat-a'], env) is None


def board(tmp_path, body=''):
    db = tmp_path / 'board.db'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE tasks(id TEXT, title TEXT, body TEXT, status TEXT, created_at INTEGER, session_id TEXT); CREATE TABLE task_comments(task_id TEXT, body TEXT);')
        conn.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (OWN, 'test', body, 'running', 0, None))
    return db


def test_b3_sent_comment_is_not_delivery(tmp_path, monkeypatch):
    sid = '20261127_abcdef'
    body = f'origin @session:default@othergw/{sid}'
    db = board(tmp_path, body)
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO task_comments VALUES (?,?)', (OWN, f'follow-through: SENT via cross_session to @session:default@othergw/{sid}; delivery verified'))
    monkeypatch.setattr(core, 'replica_paths', lambda *a: [])
    monkeypatch.setattr(core, '_origin_receipt_context', lambda *a: None)
    assert core.check_origin_reached(OWN, db, body)


@pytest.mark.parametrize('extra', [{}, {'threshold': '5%', 'comparator': '>='}])
def test_b4_threshold_is_bound_to_body(tmp_path, extra):
    db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
    md = {'acceptance_results': [dict(check='check 4', status='pass', observed='6.1%', **extra)]}
    assert plugin.classify('kanban_complete', {'summary': 'Finished.', 'metadata': md}, OWN, lambda: [db])


def test_b4_numeric_without_body_definition_is_not_self_authorizing(tmp_path):
    db = board(tmp_path, 'Run checks.')
    md = {'acceptance_results': [{'check': 'disk', 'status': 'pass', 'observed': '6.1%', 'threshold': '5%', 'comparator': '>='}]}
    assert plugin.classify('kanban_request_review', {'summary': 'Finished.', 'metadata': md}, OWN, lambda: [db])


def test_b4_overflow_does_not_erase_prior_refusal(tmp_path):
    db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
    md = {'acceptance_results': [
        {'check': 'check 4', 'status': 'fail', 'observed': '6.1%'},
        {'check': 'huge', 'status': 'pass', 'observed': 10 ** 400, 'threshold': 1, 'comparator': '<='},
    ]}
    assert plugin.classify('kanban_complete', {'summary': 'Finished.', 'metadata': md}, OWN, lambda: [db])


@pytest.mark.parametrize('flag', ['--title narrowed', "--body 'Reduced scope.'"])
def test_b5_variable_own_target_is_not_exempt(flag):
    assert core.check_shell(f'T={OWN}; hermes kanban edit "$T" {flag}', OWN, lambda: [])


@pytest.mark.parametrize('command,gate', [
    ('hermes kanban comment t_1234""5678 retry', 'consent'),
    (f'T={OWN}; hermes kanban edit "$T" --title narrowed', 'own'),
])
def test_b1_b5_real_shell_operand_equivalence(tmp_path, command, gate):
    fake = tmp_path / 'hermes'
    capture = tmp_path / 'argv'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o700)
    subprocess.run(['/bin/sh', '-c', command], check=True,
                   env={'PATH': str(tmp_path) + os.pathsep + '/usr/bin:/bin', 'CAPTURE': str(capture)})
    assert capture.read_text().splitlines()[2] == OWN
    if gate == 'consent':
        env = {'HERMES_HOME': str(tmp_path)}
        stop(env)
        assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env)
    else:
        assert core.check_shell(command, OWN, lambda: [])


def _racing_update(home, operation, loaded, release, done):
    env = {'HERMES_HOME': home}
    original = consent._load
    if loaded is not None:
        def paused(env=None):
            value = original(env)
            loaded.set()
            assert release.wait(10)
            return value
        consent._load = paused
    if operation == 'clear':
        consent.clear('chat-a', env)
    else:
        stop(env, operation, OWN if operation == 'chat-a' else OTHER)
    done.set()


@pytest.mark.parametrize('operation', ['chat-a', 'clear'])
def test_b2_record_and_clear_preserve_other_session(tmp_path, operation):
    env = {'HERMES_HOME': str(tmp_path)}
    if operation == 'clear':
        stop(env)
    ctx = multiprocessing.get_context('fork')
    loaded, release, done_a, done_b = [ctx.Event() for _ in range(4)]
    a = ctx.Process(target=_racing_update, args=(str(tmp_path), operation, loaded, release, done_a))
    b = ctx.Process(target=_racing_update, args=(str(tmp_path), 'chat-b', None, release, done_b))
    try:
        a.start()
        assert loaded.wait(5)
        b.start()
        done_b.wait(0.5)
        release.set()
        a.join(10)
        b.join(10)
        assert a.exitcode == b.exitcode == 0
        assert consent.active_stops('chat-b', env)
        assert bool(consent.active_stops('chat-a', env)) == (operation != 'clear')
    finally:
        release.set()
        for process in (a, b):
            if process.pid and process.is_alive():
                process.terminate()
                process.join()
