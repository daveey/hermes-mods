"""Owner preflight and durable one-shot handoff via session-mentions' existing courier.

A handoff is an outbox request, not proof of delivery. Keep its ID and receipt;
never automatically replay an uncertain send. The watchdog escalates missing receipts.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import sys
import time


_fallback_cache: dict[str, tuple[float, dict | None]] = {}


def moved_owner(sid: str, db_path: Path | None = None) -> str | None:
    try:
        from hermes_cli import session_owner, kanban_db, kanban_db_sync, kanban_sync_client
        from hermes_cli.kanban_db_connect import connect_closing
    except ImportError:
        return None  # older core has no session moves
    if db_path is not None:
        from hermes_state import SessionDB
        db = SessionDB(db_path=db_path, read_only=True)
        try:
            root = session_owner.lineage_root(sid, db=db)
        finally:
            db.close()
    else:
        root = session_owner.lineage_root(sid)
    me = session_owner.self_gateway()
    if not me:
        return None
    rec = session_owner.owner(root)
    if rec is None and kanban_db_sync.sync_enabled():
        # A moved-away gateway can have its local board mode switched off. The
        # Board's durable session owner remains authoritative, even then.
        hit = _fallback_cache.get(root)
        if hit and time.monotonic() - hit[0] < 30:
            rec = hit[1]
            return str(rec['gateway']) if rec and rec.get('gateway') != me else None
        try:
            path = kanban_db.kanban_home() / 'kanban.db'
            board = None
            if path.is_file():
                with connect_closing(path) as conn:
                    state = kanban_db_sync._state(conn)
                    board = state['board_id'] if state else None
            if board:
                hub = kanban_sync_client.Hub(kanban_sync_client.load_identity())
                rec = (hub.sessions(board, {'action': 'get', 'session_ids': [root]}).get('owners') or {}).get(root)
                _fallback_cache[root] = (time.monotonic(), rec)
        except Exception:
            rec = hit[1] if hit else None
            # Match core admission's fail-open when there is no known move. A
            # known owner from session_owner's stale cache never takes this path.
            logging.getLogger('chat_closeout').warning('chat-closeout: Board owner fallback unavailable', exc_info=True)
    return str(rec['gateway']) if rec and rec.get('gateway') and rec['gateway'] != me else None


def courier():
    key = 'chat_closeout_courier_files'
    if key not in sys.modules:
        path = Path(__file__).resolve().parents[1] / 'session-mentions' / 'relay_files.py'
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return sys.modules.setdefault(key, mod)
    return sys.modules[key]


def _conn(led):
    c = led._conn()
    c.execute('CREATE TABLE IF NOT EXISTS handoffs (id TEXT PRIMARY KEY, session_id TEXT, sig TEXT, '
              'created_at REAL, receipt TEXT)')
    return c


def handoff(led, sid: str, gateway: str, sig: str, prompt: str, now: float,
            profile: str = 'default') -> str:
    rf = courier()
    rid = 'closeout-' + hashlib.sha256(f'{sid}|{gateway}|{sig}'.encode()).hexdigest()[:48]
    # Commit the intent BEFORE the external write. A crash in either direction is
    # an uncertain handoff for the watchdog, never permission for another send.
    c = _conn(led)
    try:
        won = c.execute('INSERT OR IGNORE INTO handoffs VALUES (?,?,?,?,NULL)',
                        (rid, sid, sig, now)).rowcount
    finally:
        c.close()
    if won:
        base = rf.root()
        present = any((base / sub / f'{rid}.json').exists() for sub in ('outbox', 'claimed', 'results'))
        if not present and not rf.enqueue({
            'id': rid, 'action': 'send', 'route': gateway,
            'args': {'target': f'@session:{profile}@{gateway}/{sid}', 'message': prompt},
            'wait_for_reply': 0, 'sender': {'label': 'chat-closeout', 'profile': profile},
            'created_at': now, 'ttl': 600,
        }, base):
            c = _conn(led)
            try:
                c.execute('UPDATE handoffs SET receipt=? WHERE id=?',
                          (json.dumps({'ok': False, 'error': 'courier refused enqueue'}), rid))
            finally:
                c.close()
    return 'forwarded'


def stalled(led, now: float, stale_s: float) -> list[dict]:
    """Read receipts without consuming them; unknown/failed outcomes never mean delivered."""
    c = _conn(led)
    try:
        rows = c.execute('SELECT * FROM handoffs').fetchall()
        out = []
        for r in rows:
            receipt = json.loads(r['receipt']) if r['receipt'] else None
            if receipt is None:
                path = courier().root() / 'results' / f"{r['id']}.json"
                if path.exists():
                    receipt = json.loads(path.read_text())
                    c.execute('UPDATE handoffs SET receipt=? WHERE id=?', (json.dumps(receipt), r['id']))
            # Courier sends report delivered, queued or streaming; accepted is not a reply.
            accepted = receipt and (receipt.get('delivered') is True or
                                    receipt.get('status') in ('queued', 'streaming', 'delivered'))
            if not accepted and now - r['created_at'] >= stale_s:
                out.append(dict(r))
        return out
    finally:
        c.close()
