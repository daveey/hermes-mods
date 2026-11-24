"""Review-only behavioral probe for real populated captured presentation."""
from test_decisions import core, OWNER
from test_remote_card_state_machine import setup, depart
import importlib


def test_bound_remote_resolved_presentation(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=False)
    from test_withdrawal import load_module, ROOT
    module = load_module('review_presentation', ROOT / 'kanban_mirror.py')
    reader = core.ActionStore(store.path, read_only=True)
    actual = module.Presentation(core, reader).views([OWNER], include_withdrawn=True)
    assert actual and actual[0]['delivery']['text'] == 'Delivering…'
