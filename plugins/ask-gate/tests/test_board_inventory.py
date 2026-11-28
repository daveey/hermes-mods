"""Non-board SQLite artifacts must not poison global create admission."""
import sqlite3

import pytest

from test_superseding_work import _board, _home, FILLER


@pytest.fixture(autouse=True)
def enable_create_preflight(gate, monkeypatch):
    monkeypatch.setattr(gate.superseding_create, 'off', lambda: False)


@pytest.mark.parametrize('name', ['_archived', 'empty-board'])
def test_empty_artifact_does_not_hold_create(gate, name):
    home = _home(gate)
    _board(home / 'kanban.db', FILLER)
    artifact = home / 'kanban/boards' / name / 'kanban.db'
    artifact.parent.mkdir(parents=True)
    artifact.touch()
    assert gate.pre_tool_call(tool_name='kanban_create', args={
        'title': 'Sort recipes', 'body': 'Group lentil recipes by ingredient and spice.'}, session_id='s1') is None
    assert artifact not in gate.core.boards()
    assert artifact.stat().st_size == 0


def test_inventory_does_not_create_missing_archive_database(gate):
    home = _home(gate)
    _board(home / 'kanban.db', FILLER)
    archive = home / 'kanban/boards/_archived'
    archive.mkdir(parents=True)
    gate.core.boards()
    assert not (archive / 'kanban.db').exists()


def test_archive_container_with_tasks_is_still_not_a_board(gate):
    home = _home(gate)
    _board(home / 'kanban.db', FILLER)
    artifact = home / 'kanban/boards/_archived/kanban.db'
    _board(artifact, FILLER)
    assert artifact not in gate.core.boards()


def test_real_corrupt_board_stays_fail_closed(gate):
    home = _home(gate)
    _board(home / 'kanban.db', FILLER)
    broken = home / 'kanban/boards/broken/kanban.db'
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b'not sqlite')
    held = gate.pre_tool_call(tool_name='kanban_create', args={
        'title': 'Sort recipes', 'body': 'Group lentil recipes by ingredient and spice.'}, session_id='s1')
    assert held and 'could not be verified' in held['message']
