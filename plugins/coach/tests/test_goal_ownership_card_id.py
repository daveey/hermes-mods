"""Owner readback accepts current 16-hex card ids, not only legacy 8-hex ones.

Card ids grew from ``t_`` + 8 hex to ``t_`` + 16 hex. ``authoritative_owner`` validated ``continuation_ref.task_id``
with ``t_[0-9a-f]{8}`` fullmatch, so every ref to a current card raised ``owner_readback_unavailable``.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from test_goal_ownership import owner, certify, SID, TID
from goal_consumer_fixture import consumer

spec = importlib.util.spec_from_file_location('ownership_card_id', Path(__file__).parents[1] / 'goal_ownership.py')
ownership = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ownership)

CURRENT = 't_01a19e2574fa8b00'


def _rename(owner, new_id):
    db, v, _ = owner
    with sqlite3.connect(db) as c:
        for table in ('tasks', 'task_runs', 'kanban_notify_subs'):
            col = 'id' if table == 'tasks' else 'task_id'
            c.execute(f'UPDATE {table} SET {col}=? WHERE {col}=?', (new_id, TID))
    v['continuation_ref']['task_id'] = new_id


def test_current_16_hex_continuation_ref_certifies(consumer, owner):
    _rename(owner, CURRENT)
    assert certify(owner)['disposition'] == 'owned_continuing'


def test_legacy_8_hex_continuation_ref_still_certifies(consumer, owner):
    assert TID == 't_1234abcd'
    assert certify(owner)['disposition'] == 'owned_continuing'


@pytest.mark.parametrize('bad', ['t_01a19e2574fa', 't_01a19e2574fa8b00ff', 't_01A19E2574FA8B00'])
def test_cut_short_or_overlong_ref_is_refused(owner, bad):
    _rename(owner, bad)
    with pytest.raises(ValueError, match='owner_readback_unavailable'):
        ownership.authoritative_owner(owner[1], owner[2], SID)
