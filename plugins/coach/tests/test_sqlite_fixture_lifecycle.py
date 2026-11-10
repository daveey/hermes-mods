"""SQLite transaction contexts do not close connections; fixtures must do both."""
import ast
import gc
import os
from pathlib import Path
import sys

import pytest
from test_goal_ownership import owner
from sqlite_fixture import transaction
import sqlite3


@pytest.mark.parametrize('fail', [False, True])
def test_transaction_commits_or_rolls_back_and_closes(tmp_path, fail):
    path = tmp_path / 'transaction.db'
    with transaction(path) as db:
        db.execute('CREATE TABLE example (value)')
    try:
        with transaction(path) as db:
            db.execute('INSERT INTO example VALUES (1)')
            if fail:
                raise ValueError('rollback')
    except ValueError:
        assert fail
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    with transaction(path) as check:
        assert check.execute('SELECT count(*) FROM example').fetchone()[0] == (0 if fail else 1)


def test_sqlite_fixture_contexts_close_explicitly():
    violations = []
    for path in Path(__file__).parent.glob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            for item in node.items:
                call = item.context_expr
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == 'sqlite3' and call.func.attr == 'connect'):
                    violations.append(f'{path.name}:{node.lineno}')
    assert not violations, 'transaction-only SQLite contexts: ' + ', '.join(violations)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux fd ownership probe')
def test_owner_fixture_releases_board_without_gc(request):
    enabled = gc.isenabled()
    gc.disable()
    try:
        board, _, _ = request.getfixturevalue('owner')
        remaining = []
        for fd in Path('/proc/self/fd').iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            if target == str(board):
                remaining.append(target)
        assert not remaining, f'fixture retained {len(remaining)} board descriptors'
    finally:
        if enabled:
            gc.enable()
