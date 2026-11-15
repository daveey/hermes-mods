"""The Python producer's stored views must satisfy the real Desktop consumer.

Run by scripts/test-all -k user-steps. Requires Node >= 18.19 (module.register),
not a Desktop install: the companion runner stubs only unused UI imports. node_pick
finds a qualifying Node even when an older one is first on PATH.
"""
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import uuid

import pytest

from node_pick import find_node

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('desktop_contract_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
OWNER = dict(gatewayId='test-gateway', profile='default', storedSessionId='contract-session')
KINDS = ('approval', 'choice', 'input', 'external_step')
PERMISSIONS = ('absent', 'true', 'false', 'legacy')


def test_backend_views_satisfy_desktop_contract(tmp_path):
    node = find_node()  # first Node >= 18.19; fails (never skips) listing candidates if none
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    cases = list(itertools.product(KINDS, PERMISSIONS, (False, True)))
    for kind, permission, withdrawn in cases:
        title = f'{kind}/{permission}/{"withdrawn" if withdrawn else "open"}'
        limit = dict(key='access', value='Synthetic resource only')
        if permission in ('true', 'false'):
            limit['permissionRequired'] = permission == 'true'
        effects = ('approve', 'revise', 'decline') if kind == 'approval' else ('answer',)
        request = dict(
            schemaVersion=2, owner=OWNER.copy(), requestId=str(uuid.uuid4()), revision=1,
            kind=kind, title=title,
            scope=dict(markdown='Update example.txt only.', complete=True, sourceRefs=[]),
            limits=[limit], exclusions=['No external changes'],
            outcomes={effect: f'Record {effect}' for effect in effects},
            choices=[dict(id=effect, label=effect.title(), effect=effect) for effect in effects],
            aliases=[], state='open',
        )
        # create calls the real validate_action; no mock or hand-written JS request.
        created = store.create(OWNER, request, created_at=1_800_000_000)
        if permission == 'legacy':
            # New writes reject strings, but immutable pre-boolean revisions still
            # exist on disk. Seed that historical representation in this temp DB,
            # then let the real read/view path expose it (never patch the view).
            created['limits'][0]['permissionRequired'] = 'resource owner permission'
            with pytest.raises(core.ActionError, match='permissionRequired must be a boolean'):
                core.validate_action(created)
            with store._tx() as connection:
                cursor = connection.execute(
                    'UPDATE revisions SET body=? WHERE namespace=? AND id=? AND revision=?',
                    (json.dumps(created), store._ns(OWNER), created['requestId'], 1),
                )
                assert cursor.rowcount == 1
        if withdrawn:
            store.withdraw(OWNER, created['requestId'], by='agent', reason='Synthetic ask superseded')

    views = store.views(OWNER, include_withdrawn=True)
    assert len(views) == len(cases)
    assert len({view['requestId'] for view in views}) == len(cases)
    assert {view['title'] for view in views} == {
        f'{kind}/{permission}/{"withdrawn" if withdrawn else "open"}'
        for kind, permission, withdrawn in cases
    }
    assert all(view.get('createdAt') for view in views)
    withdrawn_views = [view for view in views if view['state'] == 'withdrawn']
    assert len(withdrawn_views) == len(cases) // 2
    assert all(view['withdrawal']['reason'] == 'Synthetic ask superseded' for view in withdrawn_views)
    assert len(store.views(OWNER)) == len(cases) // 2
    result = subprocess.run(
        [node, str(Path(__file__).with_name('validate_desktop_views.mjs'))],
        input=json.dumps(views), text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'validated': len(cases)}
