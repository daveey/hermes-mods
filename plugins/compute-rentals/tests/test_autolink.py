import importlib.util
import json
import sqlite3
from test_ownership import ROOT, load
from test_inventory import machine as _machine, result


def machine(provider_id, display_name, status):
    return dict(_machine(provider_id=provider_id), display_name=display_name, status=status)


def autolink():
    spec = importlib.util.spec_from_file_location('compute_autolink_test', ROOT / 'autolink.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_state_db(home, calls):
    """calls: list of (session_id, parent, tool_name, arguments_dict)."""
    db = sqlite3.connect(home / 'state.db')
    db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT)')
    db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, tool_calls TEXT)')
    for sid, parent, name, args in calls:
        db.execute('INSERT OR IGNORE INTO sessions VALUES (?,?)', (sid, parent))
        tc = json.dumps([dict(type='function', function=dict(name=name, arguments=json.dumps(args)))])
        db.execute('INSERT INTO messages (session_id, role, tool_calls) VALUES (?,?,?)', (sid, 'assistant', tc))
    db.commit()
    db.close()


def service_with(tmp_path, machines):
    service = load('inventory').InventoryService(tmp_path / 'compute-rentals', providers=('vast',))
    service.register_adapter('vast', lambda: result(machines))
    service.inventory(refresh=True)
    return service


def test_only_machines_referenced_by_session_tool_calls_are_linked(tmp_path):
    mine = machine(provider_id='55327483', display_name='bb-oww-forge-baby-bear', status='running')
    theirs = machine(provider_id='55999999', display_name='someone-else-student', status='running')
    make_state_db(tmp_path, [
        ('s1', None, 'terminal', {'command': 'vastai create instance ... --label bb-oww-forge-baby-bear'}),
        ('s2', None, 'terminal', {'command': 'ls'}),
    ])
    service = service_with(tmp_path, [mine, theirs])
    al = autolink()
    assert [json.loads(k)[3] for k in al.auto_link(service, 's1', 'default', home=tmp_path)] == ['55327483']
    assert al.auto_link(service, 's2', 'default', home=tmp_path) == []
    owned = {m['provider_id']: m['ownership'] for m in service.inventory()['machines']}
    assert [o['session_id'] for o in owned['55327483']] == ['s1']
    assert owned['55999999'] == []


def test_subagent_evidence_links_to_parent_and_inventory_calls_are_ignored(tmp_path):
    box = machine(provider_id='55284656', display_name='ptu-campaign-v1-attempt2', status='running')
    make_state_db(tmp_path, [
        ('parent', None, 'compute_rentals_list', {'session_id': 'parent 55284656'}),
        ('child', 'parent', 'terminal', {'command': 'vastai destroy instance 55284656'}),
        ('lister', None, 'compute_rentals_attach', {'machine_key': '55284656'}),
    ])
    service = service_with(tmp_path, [box])
    al = autolink()
    assert len(al.auto_link(service, 'parent', 'default', home=tmp_path)) == 1
    assert al.auto_link(service, 'lister', 'default', home=tmp_path) == []


def test_id_must_match_as_whole_token_stopped_links_and_shared_nodes_skip(tmp_path):
    running = machine(provider_id='123456', display_name='x', status='running')
    stopped = machine(provider_id='654321', display_name='y', status='stopped')
    make_state_db(tmp_path, [('s', None, 'terminal', {'command': 'echo 91234567 654321'})])
    service = service_with(tmp_path, [running, stopped])
    assert [json.loads(k)[3] for k in autolink().auto_link(service, 's', 'default', home=tmp_path)] == ['654321']
    al = autolink()
    assert al.machine_tokens(dict(status='running', resource_kind='physical_node', provider_id='lab-fabric-b300-1')) == []


def test_file_edit_mentions_are_not_evidence(tmp_path):
    box = machine(provider_id='55327483', display_name='bb-oww-forge-baby-bear', status='running')
    make_state_db(tmp_path, [('s', None, 'write_file', {'path': 'x.py', 'content': "pid='55327483'"})])
    service = service_with(tmp_path, [box])
    assert autolink().auto_link(service, 's', 'default', home=tmp_path) == []


def plugin_init():
    spec = importlib.util.spec_from_file_location('compute_init_test', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_attach_by_provider_id_refreshes_until_new_rental_appears(tmp_path):
    seen = []
    later = machine(provider_id='77777777', display_name='daveey-new-box', status='starting')
    def collect():
        seen.append(1)
        return result([later] if len(seen) >= 3 else [])
    service = load('inventory').InventoryService(tmp_path / 'compute-rentals', providers=('vast',))
    service.register_adapter('vast', collect)
    init = plugin_init()
    key = init.resolve_machine_key(service, 'vast', '77777777', attempts=5, sleep=lambda s: None)
    assert json.loads(key)[3] == '77777777'
    out = init.binding(service, 'attach', dict(provider='vast', provider_id='77777777', profile='default', session_id='s1'))
    assert out['ownership'] == [dict(gateway_id=service.gateway_id, profile='default', session_id='s1')]
    with service.ownership.connect() as db:
        assert db.execute('select source from bindings').fetchall() == [('manual',)]


def test_attach_unknown_provider_id_raises_lookup(tmp_path):
    import pytest
    service = service_with(tmp_path, [])
    with pytest.raises(LookupError):
        plugin_init().resolve_machine_key(service, 'vast', '123', attempts=2, sleep=lambda s: None)
