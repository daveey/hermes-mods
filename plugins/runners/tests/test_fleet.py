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
    entity = 'runner_' + 'ab' * 16
    def row(name, registration='online', activity='idle', labels=('self-hosted', 'Linux', 'X64'), **kw):
        return dict(dict(display_name=name, registration=registration, activity=activity, freshness='fresh',
                         labels=list(labels), readiness='pass'), **kw)
    inventory = dict(meta=dict(freshness='fresh', observed_at='2026-10-28T00:00:00Z'), data=dict(runners=[
        row('ec2-gwgen-1', id=entity, activity='busy', labels=('self-hosted', 'Linux', 'ci-capacity-slot-1', 'ci-fast')),
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
    # The Desktop repair action addresses the collector entity, not the display name.
    assert [s['id'] for s in m['ec2-gwgen']['slots']] == [entity, None]
    assert m['ec2-gwgen']['labels'] == ['ci-fast']
    assert [s['state'] for s in m['metta4']['slots']] == ['offline', 'offline']
    assert m['asg-ci']['ephemeral'] is True and out['machines'][-1]['name'] == 'asg-ci'
    # A failed check is a warning on an idle slot, never a red machine.
    assert m['mac-mini']['slots'][0]['state'] == 'idle'
    assert m['mac-mini']['warnings'] == ['mac-mini: tool:xcode failed']
    assert {k: g['category'] for k, g in m.items()} == {
        'ec2-gwgen': 'EC2 Linux', 'metta4': 'Metta', 'asg-ci': 'Ephemeral', 'mac-mini': 'Macs'}
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


def test_gateway_load_reported_absent_and_stale():
    reg = _registry(hub=dict(metrics=dict(cpu_load1=8.0, cpus=16, mem_used_pct=61.2, disk_free_gb=57, disk_total_gb=387, at=NOW - 30_000)),
                    mini=dict(metrics=dict(cpu_load1=1.0, cpus=8, at=NOW - 10 * MIN)),
                    macbook={}, junk=dict(metrics=dict(cpus=4)))  # no server time: ignored
    m = {r['name']: r for r in fleet.gateway_workers(reg, [], NOW)['machines']}
    assert m['hub']['load'] == dict(cpu_load1=8.0, cpus=16, mem_used_pct=61.2, disk_free_gb=57, disk_total_gb=387,
                                    at=NOW - 30_000, stale=False)
    assert m['mini']['load']['stale'] is True
    assert m['macbook']['load'] is None and m['junk']['load'] is None


def test_ci_machines_take_load_from_collector_host_probes():
    snapshot = dict(host_results={
        'h-mini': dict(at=1000.0, facts=dict(load=[30.35, 21.0, 17.5], disk_free_bytes=24 * 1024 ** 3)),
        'h-old': dict(at=100.0, facts=dict(load=[1.0])),
        'h-none': dict(at=1000.0, facts={})})
    loads = fleet.host_load(snapshot, 1010.0)
    assert loads['h-mini'] == dict(cpu_load1=30.35, disk_free_gb=24.0, at=1_000_000, stale=False)
    assert loads['h-old']['stale'] is True and 'h-none' not in loads
    inv = dict(meta=dict(freshness='fresh'), data=dict(runners=[
        dict(display_name='mac-mini', host_id='h-mini', registration='online', activity='idle', freshness='fresh', labels=[]),
        dict(display_name='ec2-x-1', host_id=None, registration='online', activity='idle', freshness='fresh', labels=[])]))
    m = {g['name']: g for g in fleet.ci_workers(inv, loads)['machines']}
    assert m['mac-mini']['load']['cpu_load1'] == 30.35
    assert m['ec2-x']['load'] is None


def test_ci_machine_categories_for_the_panel_groups():
    c = fleet.category
    assert c('davids-mac-mini', False, ['self-hosted']) == 'Macs'
    assert c('bb-sign', False, ['macOS']) == 'Macs'
    assert c('ec2-f18cf3d2-stable', False, ['Linux']) == 'EC2 Linux'
    assert c('hermes-hub-canary', False, []) == 'EC2 Linux'
    assert c('metta-fabric-b200-1-gpu', False, ['Linux']) == 'Metta'
    assert c('metta1-cogamer', False, ['Linux']) == 'Metta'
    assert c('tfgar-probe', True, ['Linux']) == 'Ephemeral'
    assert c('machine', False, ['self-hosted']) == 'Other'
    assert c('macaroni-box', False, ['Linux']) == 'EC2 Linux'
