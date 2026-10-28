"""Fleet view: one test per data path (registry, live leases, CI runners, unreachable machine)."""
import sqlite3

from plugins.runners import fleet

NOW = 1_800_000_000_000
MIN = 60_000


def _registry(**machines):
    base = dict(state='active', labels=[], pinned_only=False, max_card_runs=None, hold=False, last_seen=NOW - 1000)
    return dict(me='hub', machines={k: dict(base, name=k, **v) for k, v in machines.items()})


def _shadow(tmp_path, board, rows):
    db = sqlite3.connect(tmp_path / f'{board}.db')
    db.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, started_at INTEGER, '
               'lease_holder TEXT, lease_expires INTEGER)')
    db.executemany('INSERT INTO tasks VALUES (?,?,?,?,?,?)', rows)
    db.commit()
    db.close()


def test_registry_sets_slots_flags_and_drain():
    reg = _registry(hub={}, **{'worker-1': dict(max_card_runs=16, labels=['linux', 'machine:worker-1'])},
                    mini=dict(pinned_only=True), spare=dict(hold=True, max_card_runs=4))
    out = fleet.gateway_workers(reg, [], NOW, local_cap=16)
    m = {r['name']: r for r in out['machines']}
    assert out['state'] == 'ready'
    assert (m['worker-1']['slots'], m['worker-1']['slots_source']) == (16, 'registry')
    assert (m['hub']['slots'], m['hub']['slots_source']) == (16, 'local')  # this gateway's own cap
    assert m['mini']['slots'] is None and m['mini']['pinned_only'] is True  # "slots not set"
    assert m['spare']['state'] == 'draining'
    assert all(r['state'] == 'active' for k, r in m.items() if k != 'spare')


def test_live_leases_fill_slots_across_boards(tmp_path):
    _shadow(tmp_path, 'default', [
        ('t_a', 'Card A', 'running', 100, 'hub', NOW + MIN),
        ('t_b', 'Card B', 'running', 200, 'worker-1', NOW + MIN),
        ('t_old', 'Expired', 'running', 50, 'hub', NOW - 1),
        ('t_free', 'Ready', 'ready', None, None, None)])
    _shadow(tmp_path, 'ops', [('t_c', 'Card C', 'running', 300, 'hub', NOW + MIN)])
    leases = fleet.live_leases(tmp_path, NOW)
    assert sorted(l['task'] for l in leases) == ['t_a', 't_b', 't_c']
    out = fleet.gateway_workers(_registry(hub={}, **{'worker-1': dict(max_card_runs=16)}), leases, NOW, local_cap=16)
    m = {r['name']: r for r in out['machines']}
    assert [(r['board'], r['task'], r['title']) for r in m['hub']['running']] == [
        ('default', 't_a', 'Card A'), ('ops', 't_c', 'Card C')]
    assert [r['task'] for r in m['worker-1']['running']] == ['t_b']


def test_ci_runners_grouped_into_machines_with_one_slot_each():
    def row(name, registration='online', activity='idle', labels=('self-hosted', 'Linux', 'X64'), **kw):
        return dict(dict(display_name=name, registration=registration, activity=activity, freshness='fresh',
                         labels=list(labels), readiness='pass'), **kw)
    inventory = dict(meta=dict(freshness='fresh', observed_at='2026-10-28T00:00:00Z'), data=dict(runners=[
        row('ec2-gwgen-1', activity='busy', labels=('self-hosted', 'Linux', 'ci-capacity-slot-1', 'ci-fast')),
        row('ec2-gwgen-2', labels=('self-hosted', 'Linux', 'ci-capacity-slot-2', 'ci-fast')),
        row('metta4', registration='offline'), row('metta4-12', registration='offline'),
        row('asg-ci-i-0a6c6cfc48937944e-e38f2300'),
        row('tfgar-probe_i-01036fc4371112a7f', registration='missing'),
        row('mac-mini', labels=('self-hosted', 'macOS', 'bb-sign'), readiness='fail',
            readiness_evidence=dict(failed=['tool:xcode']))]))
    out = fleet.ci_workers(inventory)
    m = {g['name']: g for g in out['machines']}
    assert out['state'] == 'ready' and out['hidden_deregistered'] == 1
    assert [s['state'] for s in m['ec2-gwgen']['slots']] == ['busy', 'idle']
    assert m['ec2-gwgen']['labels'] == ['ci-fast']
    assert [s['state'] for s in m['metta4']['slots']] == ['offline', 'offline']
    assert m['asg-ci']['ephemeral'] is True and out['machines'][-1]['name'] == 'asg-ci'
    # A failed check is a warning on an idle slot, never a red machine.
    assert m['mac-mini']['slots'][0]['state'] == 'idle'
    assert m['mac-mini']['warnings'] == ['mac-mini: tool:xcode failed']
    stale = fleet.ci_workers(dict(meta=dict(freshness='stale'), data=inventory['data']))
    assert stale['state'] == 'stale'
    assert {s['state'] for g in stale['machines'] for s in g['slots']} == {'unknown'}


def test_unreachable_registry_and_unseen_machines_are_unknown_not_failed():
    leases = [dict(board='default', task='t_a', title='A', holder='macbook', started_at=1)]
    out = fleet.gateway_workers(None, leases, NOW)
    assert out['state'] == 'unknown'
    assert [(r['name'], r['state'], r['reason']) for r in out['machines']] == [
        ('macbook', 'unknown', 'machine registry unreachable')]
    reg = _registry(hub={}, mini=dict(last_seen=NOW - 30 * MIN), macbook=dict(last_seen=NOW - 30 * MIN))
    m = {r['name']: r for r in fleet.gateway_workers(reg, leases, NOW)['machines']}
    assert (m['mini']['state'], m['mini']['reason']) == ('asleep', 'not seen for 30 min')
    assert m['macbook']['state'] == 'lost'  # unseen while it still holds a card


def test_stale_registry_keeps_slots_but_not_liveness():
    reg = _registry(**{'worker-1': dict(max_card_runs=16, last_seen=NOW - 30 * MIN)})
    out = fleet.gateway_workers(reg, [], NOW, registry_age_s=600)
    assert out['state'] == 'stale'
    (m,) = out['machines']
    assert m['slots'] == 16
    assert (m['state'], m['reason']) == ('unknown', 'Board not answering; registry read 10 min ago')
