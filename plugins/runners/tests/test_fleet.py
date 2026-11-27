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


def test_ci_identity_dimensions_from_raw_labels():
    cases = [
        ('blacksmith-4vcpu-abcd', ['self-hosted', 'Linux'], 'linux', 'blacksmith', '4 vCPU'),
        ('nsc-mac-build', ['namespace-macos-arm64', 'self-hosted'], 'mac', 'namespace', 'namespace-macos-arm64'),
        ('nsc-linux-build', ['namespace-linux-amd64'], 'linux', 'namespace', 'namespace-linux-amd64'),
        ('mac-mini-1', ['self-hosted', 'bb-sign'], 'mac', None, None),
        ('custom-1', ['hermes-mac-session'], 'mac', None, None),
        ('custom-2', ['host-macos-arm64'], 'mac', None, None),
        ('hermes-hub-canary-1', ['linux'], 'linux', None, None),
        ('ci_i-0123456789abcdef', ['linux'], 'linux', 'hub-ci', None),
    ]
    rows = [dict(id=str(i), display_name=name, labels=labels, registration='online',
                 activity='busy', freshness='fresh') for i, (name, labels, *_rest) in enumerate(cases)]
    out = fleet.ci_workers(dict(meta=dict(freshness='fresh'), data=dict(runners=rows)))
    machines = {g['name']: g for g in out['machines']}
    for name, labels, os_name, provider, size in cases:
        key, _ = fleet.machine_of(name)
        m = machines[key]
        assert (m['os'], m['provider'], m['size']) == (os_name, provider, size)
    # 'mac' must be a dash token, not an arbitrary substring.
    assert fleet.ci_identity('custom', False, ['machine-fast'])['os'] == 'linux'


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


def test_held_machine_keeps_hold_metadata_from_registry():
    reg = _registry(hub={}, spare=dict(hold=True, hold_reason='disk cleanup', hold_by='operator-a', hold_at=NOW - 5 * MIN),
                    old=dict(hold=True), live=dict(hold=False, hold_reason='stale', hold_by='x', hold_at=1))
    out = fleet.gateway_workers(reg, [], NOW)
    m = {r['name']: r for r in out['machines']}
    assert m['spare']['state'] == 'draining'
    assert (m['spare']['hold_reason'], m['spare']['hold_by'], m['spare']['hold_at']) == ('disk cleanup', 'operator-a', NOW - 5 * MIN)
    # A hold recorded before the metadata columns existed has none of it.
    assert m['old']['state'] == 'draining'
    assert (m['old']['hold_reason'], m['old']['hold_by'], m['old']['hold_at']) == (None, None, None)
    # Metadata of a released machine is never reported as a current hold.
    assert m['live']['state'] == 'active'
    assert (m['live']['hold_reason'], m['live']['hold_by'], m['live']['hold_at']) == (None, None, None)
    assert reg['machines']['spare']['hold_reason'] == 'disk cleanup'  # never mutate registry


def test_held_machine_unseen_with_nothing_running_is_stopped_not_draining():
    held = dict(hold=True, hold_reason='shutdown', hold_by='admin', hold_owner='session:s@hub', hold_at=NOW - 500 * MIN)
    reg = _registry(hub={}, off=dict(held, last_seen=NOW - 500 * MIN), busy=dict(held, last_seen=NOW - 500 * MIN),
                    recent=dict(held, last_seen=NOW - 5 * MIN), never=dict(held, last_seen=None))
    lease = dict(holder='busy', board='default', task='t_a', title='Card A', started_at=100)
    m = {r['name']: r for r in fleet.gateway_workers(reg, [lease], NOW)['machines']}
    assert (m['off']['state'], m['off']['reason']) == ('stopped', 'stopped: held, not seen for 500 min')
    # The hold metadata still explains why it is off.
    assert (m['off']['hold_reason'], m['off']['hold_owner'], m['off']['hold_at']) == ('shutdown', 'session:s@hub', NOW - 500 * MIN)
    assert m['busy']['state'] == 'draining'  # still holds a card: not finished
    assert m['recent']['state'] == 'draining'  # still seen: not off yet
    assert m['never']['state'] == 'draining'  # no liveness evidence: do not claim it is off


def test_held_machine_reports_hold_owner_only_while_draining():
    reg = _registry(hub={}, spare=dict(hold=True, hold_reason='roll', hold_by='admin', hold_owner='card:t_01a1ce31e56561be',
                                       hold_at=NOW - MIN),
                    old=dict(hold=True, hold_by='admin'), live=dict(hold=False, hold_owner='card:t_stale'))
    m = {r['name']: r for r in fleet.gateway_workers(reg, [], NOW)['machines']}
    assert (m['spare']['hold_by'], m['spare']['hold_owner']) == ('admin', 'card:t_01a1ce31e56561be')
    assert m['old']['hold_owner'] is None  # Board older than hold_owner
    assert m['live']['hold_owner'] is None  # released: never a current owner


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


def test_waiting_cards_excludes_non_dispatched_assignees(tmp_path):
    db = sqlite3.connect(tmp_path / 'default.db')
    db.execute('CREATE TABLE tasks (id TEXT, assignee TEXT, status TEXT, lease_holder TEXT, '
               'runs_on TEXT, created_on TEXT, workspace_kind TEXT)')
    db.executemany('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', [
        ('t_any', 'default', 'ready', None, '[]', 'hub', 'scratch'),
        ('t_hub', 'reviewer', 'ready', None, '["machine:hub", "skill:build"]', 'hub', 'scratch'),
        ('t_held', 'default', 'ready', 'hub', None, None, None),
        ('t_todo', 'default', 'todo', None, None, None, None),
        ('t_run', 'default', 'running', 'hub', None, None, None),
        *[(f't_{i}', assignee, 'ready', None, None, None, None)
          for i, assignee in enumerate(['queue', 'dev', 'nobody', 'coach', None, ''])]])
    db.commit()
    db.close()
    _shadow(tmp_path, 'legacy', [('t_unknown', 'No assignee column', 'ready', None, None, None)])
    db = sqlite3.connect(tmp_path / 'old.db')
    db.execute('CREATE TABLE tasks (id TEXT, assignee TEXT, status TEXT, lease_holder TEXT)')
    db.execute("INSERT INTO tasks VALUES ('t_old', 'default', 'ready', NULL)")
    db.commit()
    db.close()
    waiting = fleet.waiting_cards(tmp_path, lambda p: p in {'default', 'reviewer'})
    assert sorted(w['task'] for w in waiting) == ['t_any', 't_hub', 't_old']
    pinned = next(w for w in waiting if w['task'] == 't_hub')
    assert pinned['runs_on'] == '["machine:hub", "skill:build"]'
    assert pinned['created_on'] == 'hub' and pinned['workspace_kind'] == 'scratch'
    assert fleet.waiting_cards(tmp_path, lambda p: False) == []
    assert fleet.waiting_cards(tmp_path, None) is None


def test_waiting_split_uses_core_placement_and_skill_labels():
    import pytest
    node = pytest.importorskip('hermes_cli.kanban_worker_node')
    waiting = [
        dict(runs_on=[], created_on='hub'),
        dict(runs_on=['machine:hub'], created_on='hub'),
        dict(runs_on=['linux', 'skill:build'], created_on='hub'),
        dict(runs_on=['macos'], created_on='hub'),
        dict(runs_on=None, created_on='hub', workspace_kind='worktree'),
    ]
    reg = _registry(hub=dict(labels=['linux']), worker=dict(labels=['linux', 'skill:build']),
                    mini=dict(labels=['macos'], pinned_only=True), held=dict(hold=True, labels=['linux']))
    out = fleet.gateway_workers(reg, [], NOW, waiting=waiting, eligible=node.eligible)
    m = {r['name']: r for r in out['machines']}
    assert out['waiting'] == 5  # not the sum of overlapping machine counts
    assert (m['hub']['waiting'], m['hub']['ineligible']) == (3, 2)
    assert (m['worker']['waiting'], m['worker']['ineligible']) == (2, 3)
    assert (m['mini']['waiting'], m['mini']['ineligible']) == (0, 5)
    assert (m['held']['waiting'], m['held']['ineligible']) == (1, 4)
    assert reg['machines']['held']['hold'] is True  # never mutate registry
    assert m['held']['state'] == 'draining'


def test_waiting_split_respects_pickup_mode_for_other_gateways_cards():
    """A default-placement card (runs_on NULL) made on another gateway is never copied by a machine whose pickup
    is 'explicit' (core ``kanban_worker_node`` pickup), so it must not count as waiting there."""
    import pytest
    node = pytest.importorskip('hermes_cli.kanban_worker_node')
    waiting = [
        dict(runs_on=None, created_on='hub'),             # default placement: stays with its creator
        dict(runs_on=None, created_on='explicit'),        # the explicit worker's own card
        dict(runs_on=['linux'], created_on='hub'),        # placed: any linux machine
        dict(runs_on=[], created_on='hub'),               # explicit empty pin: placed anywhere
    ]
    reg = _registry(hub=dict(labels=['linux'], pickup='explicit'), explicit=dict(labels=['linux'], pickup='explicit'),
                    every=dict(labels=['linux'], pickup='all'))
    out = fleet.gateway_workers(reg, [], NOW, waiting=waiting, eligible=node.eligible)
    m = {r['name']: r for r in out['machines']}
    assert (m['hub']['waiting'], m['hub']['ineligible']) == (3, 1)
    assert (m['explicit']['waiting'], m['explicit']['ineligible']) == (3, 1)
    assert (m['every']['waiting'], m['every']['ineligible']) == (4, 0)


def test_waiting_unreadable_replica_does_not_report_zero(tmp_path):
    (tmp_path / 'broken.db').write_bytes(b'not a sqlite database')
    assert fleet.waiting_cards(tmp_path, lambda p: True) is None


def test_waiting_unknown_is_not_zero():
    out = fleet.gateway_workers(_registry(hub={}), [], NOW)
    assert out['waiting'] is None
    assert out['machines'][0]['waiting'] is None
    assert out['machines'][0]['ineligible'] is None
    out = fleet.gateway_workers(None, [dict(holder='missing', board='default', task='t_a', title='A', started_at=1)],
                                NOW, waiting=[], eligible=lambda *args: None)
    assert out['machines'][0]['waiting'] is None


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


def test_ci_uses_real_inventory_provenance_not_snapshot_generation_time():
    # Service.inventory emits timestamps in meta.provenance, NOT in runner rows.
    row = dict(id='runner_one', display_name='new-mini-ci', registration='online',
               activity='busy', freshness='fresh', labels=['self-hosted', 'macOS'])
    observed = '2026-11-20T04:09:25Z'
    inventory = dict(meta=dict(freshness='fresh', observed_at='2026-11-20T04:23:25Z',
        provenance=[dict(source_id='runner_one', observed_at=observed)]), data=dict(runners=[row]))
    out = fleet.ci_workers(inventory)
    slot = out['machines'][0]['slots'][0]
    assert slot['state'] == 'busy'
    assert slot['last_observed'] == dict(state='busy', observed_at=observed)
    # A new registration is discovered from the whole source inventory, not bindings.
    inventory['data']['runners'].append(dict(row, id='runner_two', display_name='another-new-mini-ci'))
    assert len(fleet.ci_workers(inventory)['machines']) == 2


def test_ci_stale_display_history_does_not_restore_authoritative_state():
    rows = [dict(display_name=name, registration=registration, activity=activity,
                 freshness=freshness, observed_at='2026-11-20T04:09:25Z', labels=[])
            for name, registration, activity, freshness in [
                ('mini-1', 'online', 'busy', 'fresh'),
                ('mini-2', 'online', 'idle', 'stale'),
                ('mini-3', 'offline', 'unknown', 'stale'),
                ('unseen', 'unknown', 'unknown', 'unknown'),
                ('unmeasured', 'online', 'unknown', 'stale'),
                ('blacksmith-4vcpu-live', 'online', 'busy', 'stale')]]
    for freshness in ('fresh', 'stale'):
        out = fleet.ci_workers(dict(meta=dict(freshness=freshness), data=dict(runners=rows)))
        slots = {s['runner']: s for m in out['machines'] for s in m['slots']}
        assert slots['mini-1']['last_observed']['state'] == 'busy'
        assert slots['mini-2']['last_observed']['state'] == 'idle'
        assert slots['mini-3']['last_observed']['state'] == 'offline'
        assert slots['blacksmith-4vcpu-live']['last_observed']['state'] == 'busy'
        assert slots['unseen']['last_observed'] is None
        assert slots['unmeasured']['last_observed'] is None
        assert slots['mini-2']['state'] == 'unknown'
        if freshness == 'stale':
            assert {s['state'] for s in slots.values()} == {'unknown'}


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
    assert c('mac-mini-1', False, ['self-hosted']) == 'Macs'
    assert c('bb-sign', False, ['macOS']) == 'Macs'
    assert c('ec2-f18cf3d2-stable', False, ['Linux']) == 'EC2 Linux'
    assert c('hermes-hub-canary', False, []) == 'EC2 Linux'
    assert c('metta-fabric-b200-1-gpu', False, ['Linux']) == 'Metta'
    assert c('metta1-cogamer', False, ['Linux']) == 'Metta'
    assert c('tfgar-probe', True, ['Linux']) == 'Ephemeral'
    assert c('machine', False, ['self-hosted']) == 'Other'
    assert c('macaroni-box', False, ['Linux']) == 'EC2 Linux'


# ── worker lifecycle events (the autoscaler's ledger) ────────────────────────────────────────────

NOW_S = NOW / 1000
HOUR = 3600


def _ledger(path, rows, schema=None):
    db = sqlite3.connect(path)
    db.execute(schema or 'CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, '
               'slot TEXT NOT NULL, iid TEXT, kind TEXT NOT NULL, reason TEXT NOT NULL)')
    db.executemany('INSERT INTO events (at, slot, iid, kind, reason) VALUES (?,?,?,?,?)', rows)
    db.commit()
    db.close()
    return path


def _untouched(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_mtime_ns, sorted(p.name for p in path.parent.iterdir())


def test_worker_events_in_window_oldest_first_known_kinds_only(tmp_path):
    path = _ledger(tmp_path / 'scaler.db', [
        (NOW_S - 2 * HOUR, 'worker-2', 'i-0b', 'ready', 'bootstrap finished'),
        (NOW_S - 3 * HOUR, 'worker-2', 'i-0b', 'launched', '6 cards waiting'),
        (NOW_S - 30 * HOUR, 'worker-1', 'i-0a', 'launched', 'before the window'),
        (NOW_S - HOUR, 'worker-2', None, 'draining', 'consolidating spare capacity'),
        (NOW_S - HOUR / 2, 'worker-2', '', 'removed', 'drained'),
        (NOW_S - HOUR, 'worker-3', 'i-0c', 'rebooted', 'a kind this view does not know'),
        (NOW_S + HOUR, 'worker-3', 'i-0c', 'ready', 'after now'),
        (NOW_S - 24 * HOUR, 'worker-1', 'i-0a', 'removed', 'exactly at the window start')])
    before = _untouched(path)
    out = fleet.worker_events(path, NOW_S - 24 * HOUR, NOW_S)
    assert out == [
        dict(at=NOW_S - 24 * HOUR, worker='worker-1', kind='removed', reason='exactly at the window start', instance='i-0a'),
        dict(at=NOW_S - 3 * HOUR, worker='worker-2', kind='launched', reason='6 cards waiting', instance='i-0b'),
        dict(at=NOW_S - 2 * HOUR, worker='worker-2', kind='ready', reason='bootstrap finished', instance='i-0b'),
        dict(at=NOW_S - HOUR, worker='worker-2', kind='draining', reason='consolidating spare capacity', instance=None),
        dict(at=NOW_S - HOUR / 2, worker='worker-2', kind='removed', reason='drained', instance=None)]
    assert all(isinstance(e['at'], float) for e in out)
    assert _untouched(path) == before  # read-only: same bytes, same mtime, no new files beside it


def test_worker_events_limit_keeps_the_newest(tmp_path):
    path = _ledger(tmp_path / 'scaler.db', [(NOW_S - 100 + i, f'worker-{i}', None, 'ready', f'r{i}') for i in range(10)])
    out = fleet.worker_events(path, NOW_S - HOUR, NOW_S, limit=3)
    assert [e['worker'] for e in out] == ['worker-7', 'worker-8', 'worker-9']  # newest three, oldest first
    assert len(fleet.worker_events(path, NOW_S - HOUR, NOW_S)) == 10
    assert fleet.worker_events(path, NOW_S - HOUR, NOW_S, limit=0) == []
    assert fleet.worker_events(path, NOW_S - HOUR, NOW_S, limit='many') == []


def test_worker_events_missing_file_or_table_is_no_events_and_creates_nothing(tmp_path):
    missing = tmp_path / 'state' / 'worker-scaler.db'
    assert fleet.worker_events(missing, 0, NOW_S) == []
    assert not missing.exists() and not missing.parent.exists()
    assert fleet.worker_events(tmp_path, 0, NOW_S) == []  # a directory
    assert fleet.worker_events(None, 0, NOW_S) == []
    db = sqlite3.connect(tmp_path / 'old.db')  # the ledger before the scaler writes events
    db.execute('CREATE TABLE launches (id INTEGER)')
    db.commit()
    db.close()
    before = _untouched(tmp_path / 'old.db')
    assert fleet.worker_events(tmp_path / 'old.db', 0, NOW_S) == []
    assert _untouched(tmp_path / 'old.db') == before
    (tmp_path / 'junk.db').write_bytes(b'not a sqlite database')
    assert fleet.worker_events(tmp_path / 'junk.db', 0, NOW_S) == []
    assert (tmp_path / 'junk.db').read_bytes() == b'not a sqlite database'
    # An events table of another shape (a column this reader selects is absent).
    db = sqlite3.connect(tmp_path / 'shape.db')
    db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, at REAL, slot TEXT, iid TEXT, kind TEXT)')
    db.execute("INSERT INTO events (at, slot, kind) VALUES (?, 'worker-1', 'ready')", (NOW_S - 5,))
    db.commit()
    db.close()
    assert fleet.worker_events(tmp_path / 'shape.db', 0, NOW_S) == []


def test_worker_events_malformed_rows_are_no_events(tmp_path):
    loose = 'CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at, slot, iid, kind, reason)'
    bad = [('yesterday', 'worker-1', None, 'ready', 'text time'), (None, 'worker-1', None, 'ready', 'no time'),
           (NOW_S - 5, None, None, 'ready', 'no worker'), (NOW_S - 5, '  ', None, 'ready', 'blank worker'),
           (NOW_S - 5, 7, None, 'ready', 'numeric worker'), (NOW_S - 5, 'worker-1', None, 'ready', None),
           (NOW_S - 5, 'worker-1', None, None, 'no kind'), (NOW_S - 5, 'worker-1', None, b'ready', 'blob kind')]
    path = _ledger(tmp_path / 'bad.db', bad, schema=loose)
    assert fleet.worker_events(path, NOW_S - HOUR, NOW_S) == []
    # Beside well-formed rows only the malformed ones are left out; a non-text instance id is dropped, not the event.
    mixed = _ledger(tmp_path / 'mixed.db', bad + [(NOW_S - 9, 'worker-2', 12, 'launched', 'ok')], schema=loose)
    assert fleet.worker_events(mixed, NOW_S - HOUR, NOW_S) == [
        dict(at=NOW_S - 9, worker='worker-2', kind='launched', reason='ok', instance=None)]


def test_worker_events_locked_ledger_is_no_events_within_about_a_second(tmp_path):
    import time
    path = _ledger(tmp_path / 'scaler.db', [(NOW_S - 5, 'worker-1', None, 'ready', 'up')])
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute('BEGIN EXCLUSIVE')
    try:
        started = time.monotonic()
        assert fleet.worker_events(path, NOW_S - HOUR, NOW_S) == []
        assert time.monotonic() - started < 1.5
    finally:
        writer.execute('ROLLBACK')
        writer.close()
    assert [e['worker'] for e in fleet.worker_events(path, NOW_S - HOUR, NOW_S)] == ['worker-1']  # released: read again


def test_worker_events_survive_a_failed_mode_ro_open_without_writing(tmp_path, monkeypatch):
    path = _ledger(tmp_path / 'scaler.db', [(NOW_S - 5, 'worker-1', None, 'ready', 'up')])
    before = _untouched(path)
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if 'mode=ro' in str(target):
            raise sqlite3.OperationalError('unable to open database file')
        return real(target, *a, **kw)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    assert [e['kind'] for e in fleet.worker_events(path, NOW_S - HOUR, NOW_S)] == ['ready']
    assert fleet.worker_events(tmp_path / 'missing.db', 0, NOW_S) == [] and not (tmp_path / 'missing.db').exists()
    assert _untouched(path) == before


def test_gateway_section_carries_events_and_the_window_start(tmp_path):
    path = _ledger(tmp_path / 'scaler.db', [
        (NOW_S - 25 * HOUR, 'worker-1', None, 'launched', 'older than the window'),
        (NOW_S - 60, 'worker-1', 'i-0a', 'draining', 'idle for 20 min')])
    section = fleet.gateway_workers(_registry(hub={}), [], NOW)
    out = fleet.with_worker_events(section, path, NOW_S)
    assert out['events_since'] == NOW_S - fleet.EVENTS_WINDOW_S == NOW_S - 24 * HOUR
    assert out['events'] == [dict(at=NOW_S - 60, worker='worker-1', kind='draining', reason='idle for 20 min', instance='i-0a')]
    assert {k: v for k, v in out.items() if k not in ('events', 'events_since', 'launch_ready_s')} == section  # nothing else changes
    assert 'events' not in section  # a new dict, not a mutation
    none = fleet.with_worker_events(section, tmp_path / 'absent.db', NOW_S)
    assert (none['events'], none['events_since']) == ([], NOW_S - 24 * HOUR)


def test_registered_enrolling_worker_keeps_launch_clock_without_duplicate(tmp_path):
    path = _ledger(tmp_path / 'scaler.db', [
        (NOW_S - 600, 'worker-new', 'i-example', 'launched', 'boot')])
    row = dict(name='worker-new', slots=6, state='draining', running=[],
               hold_reason='worker-scaler: enrolling i-example: readiness pending')
    section = dict(machines=[row])
    out = fleet.with_worker_events(section, path, NOW_S)
    assert out['machines'] == [dict(row, launched_at=NOW_S - 600)]
    assert 'launched_at' not in row  # preserve the registry and popover state
    assert out['machines'][0]['state'] == 'draining'


def test_launching_workers_lifecycle_registry_precedence_and_pool_slots():
    machines = [dict(name='hub', slots=8), dict(name='worker-1', slots=4),
                dict(name='worker-2', slots=6), dict(name='worker-3', slots=6)]
    def event(at, worker, kind, instance='i-example'):
        return dict(at=at, worker=worker, kind=kind, instance=instance)
    launch = event(100, 'worker-new', 'launched')
    rows, typical = fleet.launching_workers(machines, [launch])
    assert rows == [dict(name='worker-new', state='launching', launched_at=100, slots=6, running=[])]
    assert typical is None
    assert fleet.launching_workers(machines, [launch, event(130, 'worker-new', 'ready')]) == ([], 30)
    assert fleet.launching_workers(machines, [launch, event(130, 'worker-new', 'removed')]) == ([], None)
    assert fleet.launching_workers(machines, [event(100, 'worker-1', 'launched')]) == ([], None)
    assert fleet.launching_workers(machines, []) == ([], None)
    assert fleet.launching_workers([], [launch])[0][0]['slots'] == 6
    # Repeated ready is not another sample; removal closes an unsuccessful attempt.
    events = [launch, event(130, 'worker-new', 'ready'), event(135, 'worker-new', 'ready'),
              event(140, 'worker-new', 'launched'), event(230, 'worker-new', 'ready'),
              event(240, 'worker-new', 'launched'), event(260, 'worker-new', 'removed'),
              event(280, 'worker-new', 'ready')]
    assert fleet.launching_workers(machines, list(reversed(events))) == ([], 60)
    # A relaunched worker is booting again; completed historical boots still inform the estimate.
    rows, typical = fleet.launching_workers(machines, events + [event(300, 'worker-new', 'launched')])
    assert rows[0]['launched_at'] == 300 and typical == 60
    assert machines[0] == dict(name='hub', slots=8)  # no input mutation


def test_scaler_ledger_path_env_override_else_home_state(tmp_path):
    from pathlib import Path
    assert fleet.scaler_ledger_path({'HERMES_WORKER_SCALER_LEDGER': str(tmp_path / 'x.db')}) == tmp_path / 'x.db'
    assert fleet.scaler_ledger_path({}) == Path.home() / '.hermes' / 'state' / 'worker-scaler.db'
    assert fleet.scaler_ledger_path({'HERMES_WORKER_SCALER_LEDGER': ''}) == fleet.scaler_ledger_path({})


def test_unsynced_board_replica_is_not_waiting_or_running(tmp_path):
    """A board dropped from kanban.sync.boards keeps a frozen replica in the shadow dir (hub, 2026-11-21: an
    archived askeve card still 'ready' there). Only synced boards count toward waiting and running."""
    _shadow(tmp_path, 'default', [('t_live', 'Live', 'ready', None, None, None),
                                  ('t_run', 'Running', 'running', 100, 'hub', NOW + MIN)])
    _shadow(tmp_path, 'dropped', [('t_frozen', 'Frozen', 'ready', None, None, None),
                                  ('t_ghost', 'Ghost', 'running', 100, 'hub', NOW + MIN)])
    for board in ('default', 'dropped'):
        db = sqlite3.connect(tmp_path / f'{board}.db')
        db.execute('ALTER TABLE tasks ADD COLUMN assignee TEXT')
        db.execute("UPDATE tasks SET assignee = 'default'")
        db.commit()
        db.close()
    assert [w['task'] for w in fleet.waiting_cards(tmp_path, lambda p: True, boards={'default'})] == ['t_live']
    assert [l['task'] for l in fleet.live_leases(tmp_path, NOW, boards={'default'})] == ['t_run']
    # Unknown synced set: the old every-replica scan, never zero.
    assert sorted(w['task'] for w in fleet.waiting_cards(tmp_path, lambda p: True)) == ['t_frozen', 't_live']
    assert sorted(l['task'] for l in fleet.live_leases(tmp_path, NOW)) == ['t_ghost', 't_run']
    # An unreadable replica of an unsynced board no longer blanks the count.
    (tmp_path / 'broken.db').write_bytes(b'not a sqlite database')
    assert [w['task'] for w in fleet.waiting_cards(tmp_path, lambda p: True, boards={'default'})] == ['t_live']
