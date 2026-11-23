"""Persisted, isolated reader -> reducer fixtures; also used by the real Node renderer."""
import contextlib
import copy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def persisted():
    # Outside inherited worker fences; retain the descendant marker itself.
    with tempfile.TemporaryDirectory(prefix='panel-actionability-', dir=Path.home()) as home:
        with patch.dict(os.environ, {'HERMES_HOME': home, 'HERMES_KANBAN_HOME': home}):
            os.environ.pop('HERMES_KANBAN_DB', None)
            from hermes_cli.kanban_db_connect import connect
            from hermes_cli import kanban_db
            path = Path(home) / 'kanban.db'
            with contextlib.closing(connect(path)) as writer:
                tid = kanban_db.create_task(writer, title='Portable work', assignee='executor', session_id='chat')
                writer.execute("UPDATE tasks SET status='triage',created_at=? WHERE id=?", (NOW-100, tid))
                api = load('_panel_actionability_api', ROOT / 'dashboard/plugin_api.py')
                model = load('_panel_actionability_model', ROOT / 'model.py')
                readers = api.CoreReaders(state_db=Path(home) / 'state.db')
                readers.boards = lambda: [('default', path)]
                readers.ksc = type('LocalBoards', (), {'shadow_path': staticmethod(lambda board: Path(home)/'missing-shadow.db')})
                try:
                    yield api, model, readers, writer, path, tid
                finally:
                    readers.release()


def bundle(values, *, replica=False, now=NOW):
    _, _, readers, _, path, tid = values
    d = readers._detail(readers.conn(path), tid, 'default', replica)
    row = copy.deepcopy(d['task'])
    row.update(gateway='owner' if replica else 'local', profile='default', board='default')
    if replica:
        row.update(source='replica', remote=True)
        d['owner'] = 'owner'
    return {'session': 'chat', 'gateway': 'local', 'profile': 'default', 'computed_at': now,
            'tasks': [row], 'done': [], 'cards': {tid: {'detail': d, 'board': 'default',
            'gateway': row['gateway'], 'profile': 'default'}}}


def view(values, b=None, now=NOW):
    return values[1].reduce_panel(bundle(values) if b is None else b, now=now, wire=True)


def rows(model):
    return [r for section in model['sections'].values() for r in section]


def schedule(values):
    from hermes_cli import kanban_db
    _, _, _, writer, _, tid = values
    writer.execute("UPDATE tasks SET status='scheduled' WHERE id=?", (tid,))
    kanban_db._append_event(writer, tid, 'scheduled', {'wake_at': NOW+100, 'awaiting': {'kind':'clock','why':'fixture release slot'}})


def dom_cases():
    result = {}
    with persisted() as v:
        result['owner'] = view(v)
        schedule(v)
        result['blocker'] = view(v)
        b = bundle(v)
        b['cards'][v[-1]]['detail']['actionability'] = None
        result['unavailable'] = view(v, b)
        b['cards'][v[-1]]['detail']['actionability'] = {'version': 99, 'task_id': v[-1]}
        result['unsupported'] = view(v, b)
        b['cards'][v[-1]]['detail'].pop('actionability', None)
        result['legacy'] = view(v, b)
        b = bundle(v, replica=True)
        result['replica'] = view(v, b)
        b = bundle(v)
        b['tasks'][0].update(status='done', stale=True)
        result['terminal'] = view(v, b)
        b = bundle(v)
        h = {'action':'Choose the release. Unblock = continue; archive = stop.', 'why_human':'Only owner can choose.',
             'scope':'A bounded release choice.', 'evidence':'Fixture readback.', 'interaction':'reply'}
        b['tasks'][0].update(status='blocked', human_request=h, wait={'wait_state':'human'})
        result['human'] = view(v, b)
        # Old wire payload remains renderable without additive fields.
        legacy = copy.deepcopy(result['owner'])
        for r in rows(legacy):
            r.pop('actionability', None)
            r['details'] = [d for d in r['details'] if not d.startswith('Actionability')]
        result['old_wire'] = legacy
    return {name: {'model': model, 'now': NOW} for name, model in result.items()}
