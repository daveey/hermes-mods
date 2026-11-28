"""One populated sentinel-home contract across the paired explicit acquisition APIs."""
import json
import sqlite3

import test_explicit_home_acquisition as helpers

api, worlds, tripwires = helpers.api, helpers.worlds, helpers.tripwires


def test_all_explicit_entry_points_with_populated_sources(api, worlds, tripwires, monkeypatch, tmp_path):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
    from hermes_cli import kanban_origin_local as local
    from hermes_cli import kanban_origin_ledger as ledger
    from hermes_cli import kanban_chat_links as links
    from tests.hermes_cli.test_kanban_chat_links import _replica, _ledger_row
    home = worlds['explicit']
    reader = api.CoreReaders(home_context=helpers.ctx_for(home))
    companion = reader._user_step_api()

    monkeypatch.syspath_prepend(str(helpers.ROOT.parent / 'user-steps' / 'tests'))
    from test_remote_card_state_machine import setup, depart
    from test_withdrawal import OWNER
    with tripwires.disarmed():
        monkeypatch.setitem(OWNER, 'gatewayId', 'install:' + 'a' * 32)
        monkeypatch.setitem(OWNER, 'profile', reader._home_identity()['profile'])
        monkeypatch.setitem(OWNER, 'storedSessionId', 'chat')
        seed = tmp_path / 'seed'
        seed.mkdir()
        mirror, store, row, old, operation = setup(seed)
        depart(mirror, row, departure=False)
        with sqlite3.connect(store.path) as source, sqlite3.connect(home / 'user-steps/actions.sqlite3') as target:
            source.backup(target)
        with kbc.connect_closing(home / 'kanban.db') as conn:
            parent = kb.create_task(conn, title='BLOCKED-PARENT', assignee='default', completion_contract='local-only')
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (parent,))
            conn.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (parent, worlds['task_id']))
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (worlds['task_id'],))
            binding = {'binding_id': 'populated-binding', 'version': 1,
                       'owner': {'gateway': 'gw-explicit', 'profile': 'default', 'board': 'default',
                                 'task_id': worlds['task_id']},
                       'origin': {'gateway': 'gw-explicit', 'profile': 'default', 'session': 'chat'},
                       'task_record_key': 'default/default/' + worlds['task_id']}
            conn.execute('CREATE TABLE IF NOT EXISTS origin_creation_intents(task_id TEXT,binding_json TEXT)')
            conn.execute('INSERT INTO origin_creation_intents VALUES (?,?)', (worlds['task_id'], json.dumps(binding)))
        with ledger._db(home / 'kanban/origin-ledger.db') as conn, conn:
            conn.execute('INSERT INTO origin_obligations VALUES (?,?,?,?,?,?,?)',
                         ('populated-binding', 'fixture', json.dumps(dict(binding, publication_version=1)),
                          'chat', json.dumps({'id': worlds['task_id'], 'status': 'ready'}), 1, 0))
        replica = home / 'explicit-replica.db'
        _replica(replica, [('t_remote', 'REMOTE-DATA', 'running', 'gw-remote')],
                 [('t_remote', 'gw-explicit', 'chat', 'created', 0)])
    reader.release()
    provider = helpers.load_module(helpers.ROOT / 'dashboard/producer_provider.py', '_populated_boundary_provider')
    with helpers.launch_label_read(tripwires):
        produced = provider.open_provider({'home_context': helpers.ctx_for(home)})
    sentinel = tmp_path / 'not-a-home'
    sentinel.write_text('Not a directory: no ambient fallback is possible')
    for name in ('HERMES_HOME', 'HERMES_KANBAN_HOME', 'HERMES_HUB_STATE'):
        monkeypatch.setenv(name, str(sentinel))
    failures = []
    def check(name, call):
        try:
            return call()
        except Exception as exc:
            import traceback
            failures.append(f'{name}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}')
    try:
        observed = check('real owner observation', lambda: local.observe_local_binding(
            binding, profile='default', now=100, home=home))

        for name, call in helpers.SUPPORTED.items():
            check(name, lambda call=call: call(produced.readers, worlds))
        for name, call in helpers.ACQUIRED.items():
            check(name, lambda call=call: call(produced.readers))
        check('reconcile_sources', lambda: produced.readers.reconcile_sources(['chat']))
        replicas = check('explicit replica ledger', lambda: links.replica_cards(
            ['chat'], 'gw-explicit', replicas=[('default', replica)], profile='default',
            origin_cards=[_ledger_row('t_remote', 'chat')]))

        check('bound requests', lambda: companion.panel_requests(
            home, 'chat', ['chat'], bound=True, reconcile=False,
            install_id=produced.readers._home_identity()['install_id'],
            profile=produced.readers._home_identity()['profile']))
        inputs = check('populated runtime inventory boundary', lambda: produced.readers.panel_inputs('chat', ['chat']))
        wire = check('actual provider', lambda: helpers.populated(produced, lambda: helpers.payload_for(['chat'])))
        assert not failures, '\n'.join(failures)
        assert observed is not None and observed['blocked_upstream']['id'] == parent
        assert replicas and replicas[0]['title'] == 'REMOTE-DATA'
        assert inputs and len(inputs['input_errors']) == 3
        assert wire and 'Delivering' in str(wire)
        assert tripwires == []
    finally:
        produced.close()
