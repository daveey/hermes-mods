"""Card-keyed sidebar attention from the SAME evidence as the lane totals.

These are read-only Open-card rows, never canonical decision controls. A skipped
or withdrawn consent request is not recreated by exposing its still-blocked card.
"""
import importlib.util
import sqlite3
from pathlib import Path


def panel_model():
    path = Path(__file__).resolve().parents[2] / 'session-panel' / 'model.py'
    spec = importlib.util.spec_from_file_location('_lanes_attention_model', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(cards, evidence, *, board=None):
    model = panel_model()
    rows = []
    for card in cards:
        if card.get('reason') != 'you':
            continue
        raw = card.get('attention')
        if not isinstance(raw, dict):
            return {'available': False, 'rows': [], 'reason': 'Attention needs the companion core update'}
        # The flow's raw attention object predates aggregate board stamping.
        # Qualify before ask/key/target projection, not after boards are merged.
        if board is not None:
            raw = {**raw, 'board': board}
        if not model.human_owned(raw):
            return {'available': False, 'rows': [], 'reason': 'Attention snapshot is incompatible'}
        db = evidence.connection(card['id'])
        # Membership is not a creator session_id: automation and worker sessions
        # can have those without a user-facing chat. Ignore released subscriptions.
        try:
            links = [dict(r) for r in db.execute(
                'SELECT chat_gateway,chat_profile,chat_session FROM task_chat_links '
                'WHERE task_id=? AND released=0', (card['id'],))]
        except sqlite3.Error:
            return {'available': False, 'rows': [], 'reason': 'Chat membership is unavailable'}
        view = model.ask(raw)
        rows.append({'id': card['id'], 'key': view['key'], 'title': view['title'],
                     'lane': card['lane'], 'links': links, 'unlinked': not links,
                     'target': model.target(raw)})
    return {'available': True, 'rows': rows}
