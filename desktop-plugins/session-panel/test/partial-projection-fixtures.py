"""Portable real partial-projector/reducer inputs; no live core or source reads."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'plugins/session-panel/dashboard' / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cases():
    m = load('_partial_dom_snapshot', 'board_snapshot.py')
    feed = load('_partial_dom_feed', 'feed.py')
    result = {}
    for name, status in [('partial_known', 'scheduled'), ('partial_empty', None), ('partial_stale', 'blocked')]:
        store = m.SnapshotStore()
        local = m.SourceKey('gateway', 'default', 'default', 'local', '/example/board.db')
        remote = m.SourceKey('remote', 'default', 'default', 'shadow', '/example/shadow.db')
        store.add_source(local); store.add_source(remote)
        chat = m.ChatKey('gateway', 'default', 'chat')
        store.register([m.Watch(chat, '', ('chat',), 0)])
        ds, version = store.demands(local), store.version(local)
        d = next(iter(ds.values()))
        key = m.CardKey('gateway', 'default', 'default', 't_aaaaaa1')
        row = dict(id=key.card_id, title='Known partial work', status=status, session_id='chat',
                   gateway='gateway', profile='default', board='default')
        selected = [row] if status else []
        receipt = m.Receipt(local, m.SourceVersion(store.epoch, 0, 1), None, None, None,
            m.Coverage('1', ds, frozenset(ds), frozenset()), 990000000000, 1, 990000000000, 990000000001,
            store.owner_token(local))
        part = m.SourcePart(receipt, rows={key: row} if status else {},
            details={key: {'task': row, 'links': {'parents': []}, 'events': []}} if status else {},
            scope_rows={d.id: {'linked': selected, 'done': selected}},
            scope_keys={d.id: frozenset({key}) if status else frozenset()},
            scope_memberships={d.id: (m.Membership(key, 'chat', 'created'),) if status else ()})
        store.accept_local(part, version)
        if name == 'partial_stale':
            store.fail(local, 0, 'fixture unavailable', ds)
        f = feed.Feed(object(), emit=lambda _: None, clock=lambda: 1000.)
        f.watch(['chat'])
        base = dict(session='chat', lineage=['chat'], requests=[], outcome_json={}, canonical_seq=1, computed_at=900.)
        p = f.project_board(store, chat, canonical_base=base)
        f.publish_projections(store, {'chat': p})
        result[name] = {'model': f.bundles['chat']['bundle']['model'], 'now': 1000., 'status': status}
    return result


if __name__ == '__main__':
    print(json.dumps(cases()))
