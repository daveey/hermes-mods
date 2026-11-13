"""Scratch-board end-to-end watcher tests; CLI boundary records its moved event."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from test_worker_share_watch import w, home, registry, go, Runner, NOW_MS


def audit(home, task, value=None):
    con = sqlite3.connect(home / 'kanban.db')
    con.execute('INSERT INTO task_events(task_id,kind,payload) VALUES (?, ?, ?)',
                (task, 'created', json.dumps({'portable_reason': value})))
    con.execute('UPDATE tasks SET requires=NULL WHERE id=?', (task,))
    con.commit()
    con.close()


class Mover(Runner):
    def __init__(self, home, fail=False):
        super().__init__()
        self.home, self.fail = home, fail

    def __call__(self, argv, **kw):
        if 'move' not in argv:
            return super().__call__(argv, **kw)
        self.calls.append(argv)
        assert kw['env']['HERMES_KANBAN_DB'] == str(self.home / 'kanban.db')
        if self.fail:
            return SimpleNamespace(returncode=1, stdout='', stderr='refused')
        task = argv[argv.index('move') + 1]
        labels = [argv[argv.index('--runs-on') + 1]]
        con = sqlite3.connect(self.home / 'kanban.db')
        con.execute('UPDATE tasks SET runs_on=? WHERE id=?', (json.dumps(labels), task))
        con.execute('INSERT INTO task_events(task_id,kind,payload) VALUES (?, ?, ?)',
                    (task, 'moved', json.dumps({'runs_on': labels})))
        con.commit()
        con.close()
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}), stderr='')


def test_spill_moves_only_audited_portable_after_threshold(home):
    audit(home, 't_a1')
    audit(home, 't_a2', 'home gateway marker')
    runner = Mover(home)
    assert go(home, runner, registry(), 0)['moved'] == []
    assert go(home, runner, registry(), 1)['moved'] == []
    out = go(home, runner, registry(), 2)
    assert out['moved'] == [{'task_id': 't_a1', 'machine': 'worker-1'}]
    assert out['filed'] is None
    con = sqlite3.connect(home / 'kanban.db')
    assert con.execute("SELECT task_id FROM task_events WHERE kind='moved'").fetchall() == [('t_a1',)]
    assert con.execute("SELECT runs_on FROM tasks WHERE id='t_a2'").fetchone()[0] is None
    con.close()
    assert go(home, runner, registry(), 3)['moved'] == []
    create = next(c for c in runner.calls if 'create' in c)
    assert create[create.index('--runs-on') + 1] == 'machine:worker-1'
    assert 'host_repo' not in create
    body = create[create.index('--body') + 1]
    evidence = body.split('<!-- placement:evidence -->', 1)[1].split('<!-- /placement:evidence -->', 1)[0]
    assert 't_a2' in evidence


@pytest.mark.parametrize('fence', ['prior_run', 'claimed_event', 'current_reason', 'missing_audit', 'held', 'stale_hub'])
def test_spill_fences(home, fence):
    if fence != 'missing_audit':
        audit(home, 't_a1')
    reg = registry()
    con = sqlite3.connect(home / 'kanban.db')
    if fence == 'prior_run':
        con.execute("INSERT INTO task_runs(task_id) VALUES ('t_a1')")
    if fence == 'claimed_event':
        con.execute("INSERT INTO task_events(task_id,kind,payload) VALUES ('t_a1','claimed','{}')")
    if fence == 'current_reason':
        con.execute("UPDATE tasks SET requires='[\"host_repo\"]' WHERE id='t_a1'")
    if fence == 'held':
        reg['machines']['worker-1']['hold'] = True
    if fence == 'stale_hub':
        reg['machines']['hub']['metrics']['at'] = NOW_MS - w.FRESH_MS - 1
    con.commit()
    con.close()
    out = go(home, Mover(home), reg, 0, streak_needed=1)
    assert out['moved'] == []


def test_spill_cap_pending_reservations_and_failure(home):
    audit(home, 't_a1')
    audit(home, 't_a2')
    runner = Mover(home)
    out = go(home, runner, registry(), 0, streak_needed=1, max_moves=1)
    assert len(out['moved']) == 1
    # Registry has not caught up; ready pins reserve its last free slot.
    out = go(home, runner, registry(worker_runs=15), 1, streak_needed=1)
    assert out['moved'] == []
    out = go(home, Mover(home, fail=True), registry(), 2, streak_needed=1)
    assert out['moved'] == [] and out['move_errors']


def test_dry_run_never_moves(home):
    audit(home, 't_a1')
    r = Mover(home)
    out = go(home, r, registry(), 0, streak_needed=1, dry_run=True)
    assert out['moved'] == [] and r.calls == []


def test_success_without_readback_is_not_a_move(home):
    audit(home, 't_a1')
    out = go(home, Runner(), registry(), 0, streak_needed=1)
    assert out['moved'] == [] and out['move_errors']


def test_old_move_event_does_not_confirm_success(home):
    audit(home, 't_a1')
    con = sqlite3.connect(home / 'kanban.db')
    con.execute("UPDATE tasks SET runs_on='[\"machine:worker-1\"]' WHERE id='t_a1'")
    con.execute("INSERT INTO task_events(task_id,kind,payload) VALUES ('t_a1','moved',?)",
                (json.dumps({'runs_on': ['machine:worker-1']}),))
    after = con.execute('SELECT MAX(id) FROM task_events').fetchone()[0]
    con.commit()
    con.close()
    assert not w.move_verified(home / 'kanban.db', 't_a1', 'worker-1', after)


def test_timeout_stops_batch(home):
    import subprocess
    audit(home, 't_a1')
    audit(home, 't_a2')
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 120)

    out = go(home, runner, registry(), 0, streak_needed=1)
    assert len(calls) == 1 and len(out['move_errors']) == 1 and not out['moved']


class Refuser(Mover):
    """Core refuses the first card's move (stdout ``--json`` refusal, nothing written); the rest move."""

    def __init__(self, home, refuse, code='unpushed_state_here'):
        super().__init__(home)
        self.refuse, self.code = set(refuse), code

    def __call__(self, argv, **kw):
        if 'move' in argv and argv[argv.index('move') + 1] in self.refuse:
            self.calls.append(argv)
            return SimpleNamespace(returncode=1, stderr='', stdout=json.dumps(
                {'ok': False, 'reason_code': self.code, 'error': 'dirty worktree ~/code/_wt/x on hub'}))
        return super().__call__(argv, **kw)


def test_a_refused_move_skips_that_card_and_spills_the_rest(home):
    """A card core refuses (unpushed_state_here: its trees are here) must not stop the tick's other spills."""
    audit(home, 't_a1')
    audit(home, 't_a2')
    out = go(home, Refuser(home, {'t_a1'}), registry(), 0, streak_needed=1)
    assert out['moved'] == [{'task_id': 't_a2', 'machine': 'worker-1'}], out
    assert [e['task_id'] for e in out['move_errors']] == ['t_a1']
    assert 'unpushed_state_here' in out['move_errors'][0]['error']


def test_an_unknown_outcome_still_stops_the_batch(home):
    """A Board repin whose outcome is unknown may have reserved the slot: stop, never blind retry."""
    audit(home, 't_a1')
    audit(home, 't_a2')
    runner = Refuser(home, {'t_a1'}, code='outcome_unknown')
    out = go(home, runner, registry(), 0, streak_needed=1)
    assert out['moved'] == [] and len(out['move_errors']) == 1
    assert sum('move' in c for c in runner.calls) == 1
