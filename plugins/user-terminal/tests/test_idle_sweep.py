"""Final-only sweep memoization; all stores and jobs are synthetic."""
from unittest.mock import patch
import json
import sqlite3

import pytest

from test_closed_asks import board, ut
from pathlib import Path
from test_completed_jobs import bind, end


@pytest.fixture(autouse=True)
def clear_sweep_cache():
    ut._SWEEP_FINAL.clear()
    for entry in ut._SWEEP_DBS.values():
        entry[1].close()
    ut._SWEEP_DBS.clear()
    yield
    ut._SWEEP_FINAL.clear()
    for entry in ut._SWEEP_DBS.values():
        entry[1].close()
    ut._SWEEP_DBS.clear()


def settled_card(board):
    path, job, _ = board
    with sqlite3.connect(path) as con:
        con.execute("UPDATE tasks SET status='done'")
    end(job)
    return path, job


def test_unchanged_final_card_inspected_once(board):
    path, job, _ = board
    with sqlite3.connect(path) as con:
        con.execute("UPDATE tasks SET status='done'")
    end(job)
    with patch.object(ut, 'read_status', wraps=ut.read_status) as status, \
            patch.object(ut, '_completed_card_needs_sweep', wraps=ut._completed_card_needs_sweep) as inspect:
        for _ in range(8):
            ut.sweep_jobs()
    assert status.call_count == 1
    assert inspect.call_count == 1


@pytest.mark.parametrize('name', ['status.json', 'meta.json', 'owner.json', 'final.lock/result.json',
                                  'attachment.json', 'completion-receipt.json',
                                  'completion-payload.json', 'completion-pending.json'])
def test_each_input_change_invalidates_next_sweep(board, name):
    _, job = settled_card(board)
    ut.sweep_jobs()
    target = job / name
    target.write_text(target.read_text() + ' ' if target.exists() else '{}')
    with patch.object(ut, '_completed_card_needs_sweep', wraps=ut._completed_card_needs_sweep) as inspect:
        ut.sweep_jobs()
        ut.sweep_jobs()
    assert inspect.call_count == 1


def test_one_owner_db_observation_per_sweep(board):
    import shutil
    _, job = settled_card(board)
    shutil.copytree(job, job.with_name(job.name + '-second'))
    ut.sweep_jobs()
    with patch.object(ut, '_sweep_db_stamp', wraps=ut._sweep_db_stamp) as observe:
        for _ in range(4):
            ut.sweep_jobs()
    assert observe.call_count == 4


def test_idle_hit_performs_no_text_reads_mkdir_or_connect(board):
    settled_card(board)
    ut.sweep_jobs()
    with patch.object(Path, 'read_text', side_effect=AssertionError('read on idle hit')), \
            patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir on idle hit')), \
            patch.object(ut, '_ro_connect', side_effect=AssertionError('connect on idle hit')):
        ut.sweep_jobs()
    # Exceptions are swallowed by the public sweep: inspect the cache as well.
    assert len(ut._SWEEP_FINAL) == 1


def test_late_block_in_wal_is_reconciled_next_sweep(board):
    path, job = settled_card(board)
    writer = sqlite3.connect(path)
    try:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        ut.sweep_jobs()
        before = path.stat()
        writer.execute("UPDATE tasks SET status='blocked'")
        writer.commit()
        bind(path, job)
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        ut.sweep_jobs()
        assert writer.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
        assert writer.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1
    finally:
        writer.close()


def test_data_version_detects_commit_when_file_signature_is_unchanged(board, monkeypatch):
    path, job = settled_card(board)
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        ut.sweep_jobs()
        # Model the WAL-publication gap: unchanged stat is not proof of no commit.
        original = ut._sweep_stat
        frozen = {str(path) + suffix: original(Path(str(path) + suffix)) for suffix in ('', '-wal')}
        monkeypatch.setattr(ut, '_sweep_stat', lambda p: frozen[str(p)] if str(p) in frozen else original(p))
        writer.execute("UPDATE tasks SET status='blocked'")
        writer.commit()
        bind(path, job)
        ut.sweep_jobs()
        assert writer.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
    finally:
        writer.close()


def test_mutation_during_inspection_keeps_preinspection_key(board, monkeypatch):
    path, job = settled_card(board)
    original = ut._completed_card_needs_sweep
    calls = []
    def inspect(j, st):
        result = original(j, st)
        calls.append(result)
        if len(calls) == 1:
            with sqlite3.connect(path) as con:
                con.execute("UPDATE tasks SET status='blocked'")
            bind(path, job)
        return result
    monkeypatch.setattr(ut, '_completed_card_needs_sweep', inspect)
    ut.sweep_jobs()
    ut.sweep_jobs()
    assert calls == [False, True]
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'


@pytest.mark.parametrize('failure', ['inspect', 'stat', 'database', 'malformed'])
def test_failed_inspection_retries_next_sweep(board, monkeypatch, failure):
    _, job = settled_card(board)
    with monkeypatch.context() as m:
        if failure == 'inspect':
            m.setattr(ut, '_completed_card_needs_sweep', lambda *a: (_ for _ in ()).throw(RuntimeError('injected')))
        elif failure == 'stat':
            m.setattr(ut, '_sweep_stat', lambda *a: (_ for _ in ()).throw(PermissionError('injected')))
        elif failure == 'database':
            m.setattr(ut, '_ro_connect', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('busy')))
        else:
            (job / 'status.json').write_text('{')
        ut.sweep_jobs()
    assert job not in ut._SWEEP_FINAL
    if failure == 'malformed':
        (job / 'status.json').write_text('{"status":"ran"}')
    with patch.object(ut, '_completed_card_needs_sweep', wraps=ut._completed_card_needs_sweep) as inspect:
        ut.sweep_jobs()
        ut.sweep_jobs()
    assert inspect.call_count == 1


def test_nonfinal_clock_and_process_checks_are_not_cached(board, monkeypatch):
    _, job, _ = board
    meta = json.loads((job / 'meta.json').read_text())
    monkeypatch.setattr(ut.time, 'time', lambda: meta['created'] + 1)
    with patch.object(ut, '_runner_gone', return_value=False) as process:
        for _ in range(4):
            ut.sweep_jobs()
        assert process.call_count == 4
        monkeypatch.setattr(ut.time, 'time', lambda: meta['created'] + ut.LAUNCH_GRACE_S + 1)
        ut.sweep_jobs()
    assert ut.read_status(job)['status'] == 'abandoned'


@pytest.mark.parametrize('outcome', ['unavailable', 'contention', 'delivered'])
def test_session_delivery_is_not_cached_as_success(board, monkeypatch, outcome):
    _, job, _ = board
    session_owner(job)
    end(job)
    completion = ut._completion_module()
    calls = []
    def attempt(*args):
        calls.append(True)
        if outcome == 'unavailable':
            raise completion.Unavailable('test')
        if outcome == 'delivered':
            (job / 'completion-receipt.json').write_text('{"status":"admitted"}')
    monkeypatch.setattr(completion, 'deliver', attempt)
    for _ in range(4):
        ut.sweep_jobs()
    assert len(calls) == (1 if outcome == 'delivered' else 4)
    assert (job in ut._SWEEP_FINAL) == (outcome == 'delivered')
    if outcome == 'delivered':
        (job / 'completion-receipt.json').unlink()
        ut.sweep_jobs()
        assert len(calls) == 2


def test_nonfinal_transition_checks_clock_even_when_old_owner_db_unavailable(board, monkeypatch):
    path, job = settled_card(board)
    ut.sweep_jobs()
    (job / 'final.lock' / 'result.json').unlink()
    (job / 'final.lock').rmdir()
    (job / 'status.json').write_text('{"status":"waiting_for_user"}')
    monkeypatch.setattr(ut, '_sweep_db_stamp', lambda *a: (_ for _ in ()).throw(OSError('old DB unavailable')))
    with patch.object(ut, '_runner_gone', return_value=False), patch.object(ut, 'watch_once', wraps=ut.watch_once) as watch:
        ut.sweep_jobs()
    assert watch.call_count == 1
    assert ut.read_status(job)['status'] == 'abandoned'


def test_owner_db_replacement_invalidates_observer(board):
    path, job = settled_card(board)
    ut.sweep_jobs()
    old_generation = ut._SWEEP_DBS[str(path)][2]
    replacement = path.with_name('replacement.db')
    source = sqlite3.connect(path)
    destination = sqlite3.connect(replacement)
    try:
        source.backup(destination)
        destination.execute("UPDATE tasks SET status='blocked'")
        destination.commit()
    finally:
        source.close()
        destination.close()
    # Synthetic closed replacement only; never replace a live store this way.
    replacement.replace(path)
    bind(path, job)
    ut.sweep_jobs()
    assert ut._SWEEP_DBS[str(path)][2] != old_generation
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'


def test_missing_owner_db_does_not_cache_failed_check(board):
    path, job = settled_card(board)
    ut.sweep_jobs()
    saved = path.with_name('saved.db')
    path.rename(saved)
    ut.sweep_jobs()
    assert job not in ut._SWEEP_FINAL
    saved.rename(path)
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL


def test_other_thread_reuses_query_only_observer_without_transaction(board):
    from concurrent.futures import ThreadPoolExecutor
    path, job = settled_card(board)
    ut.sweep_jobs()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(ut.sweep_jobs).result(timeout=10)
    assert job in ut._SWEEP_FINAL
    observer = ut._SWEEP_DBS[str(path)][1]
    assert observer.execute('PRAGMA query_only').fetchone()[0] == 1
    assert not observer.in_transaction


def test_removed_job_pruned_and_new_root_not_created(board, monkeypatch):
    import shutil
    _, job = settled_card(board)
    ut.sweep_jobs()
    shutil.rmtree(job)
    ut.sweep_jobs()
    assert not ut._SWEEP_FINAL
    missing = job.parent / 'missing-root'
    monkeypatch.setenv('HERMES_USER_TERMINAL_DIR', str(missing))
    ut.sweep_jobs()
    assert not missing.exists()


def test_skip_leaves_job_for_next_sweep(board):
    _, job = settled_card(board)
    ut.sweep_jobs(skip=job)
    assert not ut._SWEEP_FINAL
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL


def session_owner(job):
    meta = json.loads((job / 'meta.json').read_text())
    meta['owner'] = {'kind': 'session', 'session_id': 'synthetic-session'}
    (job / 'meta.json').write_text(json.dumps(meta))


@pytest.mark.parametrize('receipt_status', ['admitted', 'missing'])
def test_settled_session_inspected_once(board, receipt_status):
    _, job, _ = board
    session_owner(job)
    end(job)
    (job / 'completion-receipt.json').write_text(json.dumps({'status': receipt_status}))
    with patch.object(ut, 'read_status', wraps=ut.read_status) as status:
        for _ in range(8):
            ut.sweep_jobs()
    assert status.call_count == 1
