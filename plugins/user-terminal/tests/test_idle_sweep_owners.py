"""Final jobs whose owner can never be delivered to: structural no-action proof.

All boards and jobs are synthetic (the shared ``board`` fixture). The proof is that,
for a final job whose owner is absent, relayed, of an unknown kind, or a card lacking
a task id or board path, ``watch_once`` performs no reconciliation at all; only the
job's own state files decide that, so their signatures key the memo.
"""
from unittest.mock import patch
from contextlib import closing
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_core  # tests/conftest.py: skipped when no Hermes core is importable

from test_closed_asks import board, ut  # noqa: F401 -- fixture
from test_completed_jobs import bind, end


@pytest.fixture(autouse=True)
def clear_sweep_cache():
    def reset():
        ut._SWEEP_FINAL.clear()
        for entry in ut._SWEEP_DBS.values():
            entry[1].close()
        ut._SWEEP_DBS.clear()
    reset()
    yield
    reset()


def set_owner(job, shape):
    """Rewrite the fixture card job's owner to ``shape`` (meta.json + owner.json sidecar)."""
    meta = json.loads((job / 'meta.json').read_text())
    legacy = None
    if shape == 'absent':  # pre-owner jobs: neither meta owner nor sidecar
        meta.pop('owner', None)
        meta['board_db'] = None
    elif shape == 'relayed':
        meta['owner'] = {'kind': 'relayed', 'gateway': 'synthetic-gateway'}
        meta['board_db'] = None
        legacy = dict(meta['owner'])
    elif shape == 'legacy-relayed':  # only the sidecar names it
        meta.pop('owner', None)
        legacy = {'kind': 'relayed', 'gateway': 'synthetic-gateway'}
    elif shape == 'unknown':
        meta['owner'] = {'kind': 'future-kind'}
    elif shape == 'card-no-db':
        meta['board_db'] = None
        legacy = {'kind': 'kanban', 'task': meta['owner']['task_id'], 'db': None}
    elif shape == 'card-no-task':
        meta['owner'] = {k: v for k, v in meta['owner'].items() if k != 'task_id'}
        legacy = {'kind': 'kanban', 'db': meta['board_db']}
    else:
        raise AssertionError(shape)
    (job / 'meta.json').write_text(json.dumps(meta))
    if legacy is None:
        (job / 'owner.json').unlink(missing_ok=True)
    else:
        (job / 'owner.json').write_text(json.dumps(legacy))


SHAPES = ['absent', 'relayed', 'legacy-relayed', 'unknown', 'card-no-db', 'card-no-task']
FINALS = ['ran', 'declined', 'failed', 'abandoned', 'withdrawn']


def counters():
    return (patch.object(ut, 'read_status', wraps=ut.read_status),
            patch.object(ut, '_job_meta_owner', wraps=ut._job_meta_owner),
            patch.object(ut, 'watch_once', wraps=ut.watch_once))


@pytest.mark.parametrize('status', FINALS)
@pytest.mark.parametrize('shape', SHAPES)
def test_unchanged_nondeliverable_final_job_inspected_once(board, shape, status):
    path, job, _ = board
    set_owner(job, shape)
    end(job, status)
    with closing(sqlite3.connect(path)) as con:
        before = con.execute('SELECT count(*) FROM task_events').fetchone()[0]
    a, b, c = counters()
    with a as status_reads, b as owner_reads, c as watch:
        for _ in range(8):
            ut.sweep_jobs()
    assert status_reads.call_count == 1
    assert owner_reads.call_count == 1
    assert watch.call_count == 0  # the proof replaces the no-op reconcile pass
    assert job in ut._SWEEP_FINAL
    with sqlite3.connect(path) as con:  # and is truly no-action: the board is untouched
        assert con.execute('SELECT count(*) FROM task_events').fetchone()[0] == before
        assert con.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0


@pytest.mark.parametrize('shape', ['absent', 'relayed'])
def test_nondeliverable_proof_opens_no_board(board, shape):
    _, job, _ = board
    set_owner(job, shape)
    end(job)
    with patch.object(ut, '_ro_connect', side_effect=AssertionError('connect')), \
            patch.object(ut, '_sweep_db_stamp', side_effect=AssertionError('observe')):
        ut.sweep_jobs()
        ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL
    assert not ut._SWEEP_DBS


def test_steady_hit_reads_no_text(board):
    _, job, _ = board
    set_owner(job, 'absent')
    end(job)
    ut.sweep_jobs()
    with patch.object(Path, 'read_text', side_effect=AssertionError('read on idle hit')), \
            patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir on idle hit')):
        ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL


@pytest.mark.parametrize('name', ['status.json', 'meta.json', 'owner.json', 'final.lock/result.json',
                                  'attachment.json', 'completion-receipt.json',
                                  'completion-payload.json', 'completion-pending.json'])
def test_each_input_change_reinspects_once(board, name):
    _, job, _ = board
    set_owner(job, 'relayed')
    end(job)
    ut.sweep_jobs()
    target = job / name
    target.write_text(target.read_text() + ' ' if target.exists() else '{}')
    a, b, c = counters()
    with a as status_reads, b as owner_reads, c:
        ut.sweep_jobs()
        ut.sweep_jobs()
    assert status_reads.call_count == 1
    assert owner_reads.call_count == 1


@pytest.mark.parametrize('where', ['meta', 'sidecar'])
def test_owner_gaining_card_reconciles_next_sweep(board, where):
    """A proof for an ownerless job never hides a later card owner (content change)."""
    path, job, _ = board
    meta = json.loads((job / 'meta.json').read_text())
    card, board_db = meta['owner'], meta['board_db']
    set_owner(job, 'absent')
    end(job)
    bind(path, job)
    for _ in range(2):
        ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
    meta = json.loads((job / 'meta.json').read_text())
    if where == 'meta':
        meta.update(owner=card, board_db=board_db)
        (job / 'meta.json').write_text(json.dumps(meta))
    else:
        (job / 'owner.json').write_text(json.dumps({'kind': 'kanban', 'task': card['task_id'],
                                                    'run_id': card['run_id'], 'db': board_db}))
    ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
        assert con.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1


def test_card_gaining_db_reconciles_next_sweep(board):
    path, job, _ = board
    board_db = json.loads((job / 'meta.json').read_text())['board_db']
    set_owner(job, 'card-no-db')
    end(job)
    bind(path, job)
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL
    meta = json.loads((job / 'meta.json').read_text())
    meta['board_db'] = board_db
    (job / 'meta.json').write_text(json.dumps(meta))
    ut.sweep_jobs()
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'


def test_owner_becoming_session_attempts_delivery(board, monkeypatch):
    _, job, _ = board
    set_owner(job, 'relayed')
    end(job)
    ut.sweep_jobs()
    calls = []
    monkeypatch.setattr(ut._completion_module(), 'deliver', lambda *a: calls.append(True))
    meta = json.loads((job / 'meta.json').read_text())
    meta['owner'] = {'kind': 'session', 'session_id': 'synthetic-session'}
    (job / 'meta.json').write_text(json.dumps(meta))
    for _ in range(3):
        ut.sweep_jobs()
    assert len(calls) == 3  # no receipt: every pass attempts, nothing memoized
    assert job not in ut._SWEEP_FINAL


@pytest.mark.parametrize('name,text', [('meta.json', '{'), ('owner.json', '{'),
                                       ('owner.json', '[]'), ('status.json', '[')])
def test_malformed_file_keeps_legacy_pass_and_is_never_memoized(board, name, text, caplog):
    _, job, _ = board
    set_owner(job, 'relayed')
    end(job)
    (job / name).write_text(text)
    with patch.object(ut, 'watch_once', wraps=ut.watch_once) as watch:
        for _ in range(3):
            ut.sweep_jobs()
    assert watch.call_count == 3  # legacy reconcile pass kept for malformed input
    assert job not in ut._SWEEP_FINAL
    assert not [r for r in caplog.records if r.levelname in ('WARNING', 'ERROR')]


def test_non_object_meta_error_retried_not_memoized(board, caplog):
    """_job_meta_owner raises for a non-object meta.json: legacy error log, never a proof."""
    _, job, _ = board
    set_owner(job, 'absent')
    end(job)
    (job / 'meta.json').write_text('[]')
    ut.sweep_jobs()
    ut.sweep_jobs()
    assert job not in ut._SWEEP_FINAL
    assert len([r for r in caplog.records if 'reconciling' in r.getMessage()]) == 2
    (job / 'meta.json').write_text('{}')
    with patch.object(ut, '_job_meta_owner', wraps=ut._job_meta_owner) as owner_reads:
        ut.sweep_jobs()
        ut.sweep_jobs()
    assert owner_reads.call_count == 1
    assert job in ut._SWEEP_FINAL


def test_injected_owner_read_failure_retries(board, monkeypatch):
    _, job, _ = board
    set_owner(job, 'absent')
    end(job)
    with monkeypatch.context() as m:
        m.setattr(ut, '_job_meta_owner', lambda *a: (_ for _ in ()).throw(OSError('injected')))
        ut.sweep_jobs()
    assert job not in ut._SWEEP_FINAL
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL


@pytest.mark.parametrize('shape', ['absent', 'relayed'])
def test_nonfinal_ownerless_job_keeps_clock_checks(board, monkeypatch, shape):
    _, job, _ = board
    set_owner(job, shape)
    meta = json.loads((job / 'meta.json').read_text())
    monkeypatch.setattr(ut.time, 'time', lambda: meta['created'] + 1)
    with patch.object(ut, '_runner_gone', return_value=False) as process:
        for _ in range(4):
            ut.sweep_jobs()
        assert process.call_count == 4
        monkeypatch.setattr(ut.time, 'time', lambda: meta['created'] + ut.LAUNCH_GRACE_S + 1)
        ut.sweep_jobs()
    assert ut.read_status(job)['status'] == 'abandoned'
    assert job not in ut._SWEEP_FINAL  # the transitioning pass itself is not a proof
    ut.sweep_jobs()
    assert job in ut._SWEEP_FINAL


def test_mixed_corpus_only_deliverable_jobs_reinspected(board, monkeypatch):
    """Native-shaped corpus: many ownerless finals plus one pending session delivery."""
    import shutil
    _, job, _ = board
    set_owner(job, 'absent')
    end(job)
    clones = [job.with_name(f'{job.name}-{i}') for i in range(20)]
    for clone in clones:
        shutil.copytree(job, clone)
    pending = job.with_name(job.name + '-session')
    shutil.copytree(job, pending)
    meta = json.loads((pending / 'meta.json').read_text())
    meta['owner'] = {'kind': 'session', 'session_id': 'synthetic-session'}
    (pending / 'meta.json').write_text(json.dumps(meta))
    attempts = []
    monkeypatch.setattr(ut._completion_module(), 'deliver', lambda j, *a: attempts.append(j.name))
    ut.sweep_jobs()
    with patch.object(ut, '_job_meta_owner', wraps=ut._job_meta_owner) as owner_reads, \
            patch.object(ut, 'watch_once', wraps=ut.watch_once) as watch:
        for _ in range(4):
            ut.sweep_jobs()
    assert watch.call_count == 4
    assert {c.args[0] for c in watch.call_args_list} == {pending}
    assert attempts == [pending.name] * 5
    # Pending session: one decode in sweep plus reconcile passes inside watch_once.
    assert {c.args[0] for c in owner_reads.call_args_list} == {pending}
