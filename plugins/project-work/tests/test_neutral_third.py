"""Independent desired-invariant review regressions for 29fb5bd."""
from dataclasses import asdict
import importlib
from conftest import PACKAGE, start
from test_native import bridge, native_card, native_project


def test_gateway_only_restart_uses_latest_owner_live_id(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    stored = ports.get_session(row['stored_session_id'])
    params = dict(row['cards']['implement']['params'])
    calls = []
    def owner_tool(name, args, live):
        calls.append(live)
        assert live == 'new-live', 'owner_tool must not use expired gateway ID'
        return {'task_id': 'native-task'}
    native = bridge(env, sessions=lambda q: [stored],
        rpc=lambda *a: {'session_id': 'new-live', 'stored_session_id': stored['id']},
        owner_tool=owner_tool, projects=lambda q: native_project(service),
        card_keys=lambda q: [], cards=lambda cid: native_card(service, row, params, cid,
            f"project-work:{row['workflow_id']}:1:implement\n"))
    # Runtime survives while its gateway restarts. _advance resumes before create.
    native.live[row['session_id']] = stored['id']
    resumed = native.recover_session(row['workflow_id'])
    params['session_id'] = resumed['session_id']
    assert native.create_card(row['workflow_id'], 'implement', params) == 'native-task'
    assert calls == ['new-live']


def test_unknown_workflow_approval_is_terminal(env, tmp_path):
    service, ctx, _ = env
    runtime = importlib.import_module(PACKAGE + '.runtime')
    client = importlib.import_module(PACKAGE + '.client')
    controller = runtime.Controller(service)
    class Direct:
        def call(self, operation, **args):
            return controller.call(operation, args)
    queue = client.DurableIngressQueue(tmp_path / 'journal' / 'q.db', Direct())
    queue.append('approve', context=asdict(ctx),
        workflow_id='00000000-0000-0000-0000-000000000000', plan_hash='a' * 64)
    assert queue.drain_once()
    assert not queue.drain_once()
    with queue.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM rejected').fetchone()[0] == 1
