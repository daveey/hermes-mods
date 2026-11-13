"""Receipt hint (self-heal agent-f82d813de9): a delivery-claiming kanban_complete without
metadata.completion_receipt gets a non-blocking hint appended to its tool result. Never a refusal: legacy
completions stay valid."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('honest_receipt_hint_test', Path(__file__).parents[1] / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

OK = json.dumps({'ok': True, 'task_id': 't_12345678'})
REFUSED = json.dumps({'ok': False, 'error': 'goal judge: not done'})
RECEIPT = {'version': 1, 'items': [{'scope': 'fix', 'disposition': 'delivered', 'remaining': [],
                                    'evidence': [{'ref': 'commit abc', 'observed_at': '2026-11-13T00:00:00Z',
                                                  'verification': 'independently_checked'}]}]}


def hint(args, result=OK, name='kanban_complete', env=None):
    env = {'HERMES_KANBAN_TASK': 't_12345678'} if env is None else env
    return plugin.receipt_hint(name, args, result, env=env)


def test_delivery_claim_without_receipt_gets_a_hint_appended_not_a_block():
    out = hint({'summary': 'Fixed the parser and merged PR #12; deployed on the hub.'})
    assert out is not None and out.startswith(OK)
    assert 'metadata.completion_receipt' in out and 'not refused' in out


def test_refused_completion_hint_says_add_it_on_the_retry():
    out = hint({'summary': 'Shipped the fix.'}, result=REFUSED)
    assert out is not None and 'retry' in out


def test_no_hint_with_a_receipt_or_without_a_delivery_claim_or_outside_a_worker():
    assert hint({'summary': 'Shipped the fix.', 'metadata': {'completion_receipt': RECEIPT}}) is None
    assert hint({'summary': 'Investigated; not a defect: expected behaviour.'}) is None
    assert hint({'summary': 'Shipped the fix.'}, env={}) is None
    assert hint({'summary': 'Shipped the fix.', 'task_id': 't_other999'}) is None
    assert hint({'summary': 'Shipped the fix.'}, name='kanban_comment') is None


def test_registered_as_transform_tool_result():
    hooks = {}

    class Ctx:
        def register_hook(self, name, fn):
            hooks.setdefault(name, []).append(fn)

    plugin.register(Ctx())
    assert hooks.get('transform_tool_result')
