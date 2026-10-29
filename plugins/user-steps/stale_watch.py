#!/usr/bin/env python3
"""Open one Kanban card per user-steps request that is still open after its question was answered.

Detection countermeasure for desktop-1321064734. Run periodically on EVERY gateway (action stores
are per host), e.g. from cron: `python3 ~/.hermes/plugins/user-steps/stale_watch.py`.

Reads chat.answered_open() (the set behind /ping stale.answeredOpen) directly, no HTTP or token. For
each request whose user reply is older than --min-age (default 1 h) it creates an agent-owned Kanban
card with idempotency key `user-steps-stale:<requestId>`, so reruns never duplicate and a retired
request (no longer open) gets no card. Never creates an Inbox card. Read-only for the action store.

It also opens one card (key `user-steps-failed-decision:<operationId>`) for a delivered decision whose
admitted turn ended in a failed-turn boundary before any tool ran and that nothing continued (no
auto-continue row, no later user message) within FAILED_DECISION_MIN_AGE (self-heal desktop-3a6213ce74:
a backend restart killed the turn, the tile had already closed, and the decision silently never ran).
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_AGE = 3600
FAILED_DECISION_MIN_AGE = 600
FAILED_DECISION_MAX_AGE = 7 * 86400


def _load(name):
    spec = importlib.util.spec_from_file_location('us_' + name, HERE / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def card(item):
    rid = item['requestId']
    body = (f"A user-steps panel request is still open although its question was already answered in chat.\n\n"
            f"- Request id: {rid}\n- Session: {item['session']}\n"
            f"- Question:\n{item['question']}\n\n- User reply:\n{item['reply']}\n\n"
            f"Retire it: POST /api/plugins/user-steps/requests/{rid}/reconcile-chat (the judge re-reads the "
            f"transcript; no answer text is accepted from the caller). If the judge returns unmatched for a "
            f"genuine answer, fix the judge prompt (SYSTEM) in plugins/user-steps/chat.py instead.\n"
            f"Source: plugins/user-steps/stale_watch.py. Done when the id is gone from /ping stale.answeredOpenIds.")
    return {'title': f'user-steps: request {rid} still open after its question was answered',
            'body': body, 'idempotency_key': 'user-steps-stale:' + rid, 'assignee': 'default'}


def dup_card(d):
    lines = '\n'.join('- ' + ', '.join(g) for g in d['groups'])
    body = (f"A session holds more than one open user-steps request for the same decision (the one-tile rule, "
            f"core.same_open_ask, was bypassed: a regression).\n\n- Namespace: {d['namespace']}\n"
            f"- Session: {d['session']}\n- Duplicate groups (request ids, oldest first):\n{lines}\n\n"
            f"Find which path minted the extra request (extractor, action_request tool, plan gate) and fix it, "
            f"then withdraw the newer duplicates with a reason. This watcher never withdraws anything.\n"
            f"Source: plugins/user-steps/stale_watch.py. Done when /ping stale.duplicateOpen no longer counts "
            f"this session.")
    return {'title': f"user-steps: duplicate open requests in session {d['session']}", 'body': body,
            'idempotency_key': f"user-steps:dup:{d['namespace']}:{d['session']}", 'assignee': 'default'}


def cross_dup_card(p):
    a, b = p['ids']
    sessions = ', '.join(p['sessions'])
    body = (f"Two sessions on this gateway hold open user-steps requests for the same decision "
            f"(core.same_open_ask matched them across chats). A status reply in one chat can restate the "
            f"other's ask and mint a copy.\n\n- Namespace: {p['namespace']}\n"
            f"- Sessions: {sessions}\n- Request ids: {a}, {b}\n\n"
            f"Find which path minted the extra request and withdraw the newer duplicate with a reason. "
            f"This watcher never withdraws anything.\n"
            f"Source: plugins/user-steps/stale_watch.py.")
    return {'title': 'user-steps: duplicate open requests across sessions', 'body': body,
            'idempotency_key': f"user-steps:dup:{p['namespace']}:{a}:{b}", 'assignee': 'default'}


def _admission(body):
    receipt = body.get('receipt') or {}
    adm = (receipt.get('run') or {}).get('admission') if isinstance(receipt.get('run'), dict) else None
    adm = adm if isinstance(adm, dict) else receipt.get('admission')
    return adm if isinstance(adm, dict) and adm.get('status') == 'accepted' else None


def _turn_outcome(c, session, user_row_id):
    """('failed', ts) when the admitted turn's first terminal row is a failed-turn boundary with no tool before it
    and no later continuation (auto_continue row or a human message); otherwise (None, None)."""
    rows = c.execute('SELECT role, display_kind, tool_calls, timestamp FROM messages WHERE session_id=? AND id>? '
                     'ORDER BY id LIMIT 400', (session, user_row_id)).fetchall()
    failed_at = None
    for role, kind, calls, ts in rows:
        if failed_at is None:
            if role == 'tool' or (role == 'assistant' and calls and calls not in ('[]', 'null')):
                return None, None  # work ran: never re-send, never flag (at-most-once)
            if role == 'assistant' and kind == 'failed_turn':
                failed_at = ts
                continue
            if role == 'assistant' and kind is None:
                return None, None  # the turn answered
        elif role == 'user' and kind in (None, 'auto_continue', 'steer'):
            return None, None  # continued by recovery, or the user is back in the chat
    return ('failed', failed_at) if failed_at is not None else (None, None)


def failed_decisions(core, store, db=None, now=None, min_age=FAILED_DECISION_MIN_AGE):
    """Delivered approve/choose decisions whose admitted turn failed before any tool and was never continued."""
    now = time.time() if now is None else now
    with store._tx() as c:
        ops = [r[0] for r in c.execute("SELECT body FROM operations WHERE body LIKE '%\"delivered\"%'").fetchall()]
    out, conn = [], None
    try:
        for raw in ops:
            try:
                body = json.loads(raw)
            except ValueError:
                continue
            adm = _admission(body)
            if (body.get('dispatchState') != 'delivered' or (body.get('intent') or {}).get('effect') == 'decline'
                    or not adm or not adm.get('stored_session_id') or adm.get('user_row_id') is None):
                continue
            conn = conn or core._ro_connect(db or core._state_db())
            state, ts = _turn_outcome(conn, adm['stored_session_id'], int(adm['user_row_id']))
            if state and min_age <= now - (ts or now) <= FAILED_DECISION_MAX_AGE:
                out.append({'operationId': body['operationId'], 'requestId': body.get('requestId'),
                            'session': adm['stored_session_id'], 'userRowId': adm['user_row_id'], 'failedAt': ts})
    finally:
        if conn is not None:
            conn.close()
    return out


def failed_decision_card(f):
    oid = f['operationId']
    when = time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(f['failedAt']))
    body = (f"A For-you decision was delivered (prompt admission accepted, tile closed) but the turn it started "
            f"ended in a failed-turn boundary at {when} before any tool ran, and nothing continued it: neither "
            f"restart recovery (auto-continue / startup resume) nor a later user message.\n\n"
            f"- Operation id: {oid}\n- Request id: {f['requestId']}\n- Session: {f['session']}\n"
            f"- Admitted user row: {f['userRowId']}\n\n"
            f"Carry out the decision in that chat (read the admitted row; no tool ran, so nothing was done), then find "
            f"why recovery skipped it: agent.log 'startup resume: marker ... skipped: <reason>' around that time.\n"
            f"Source: plugins/user-steps/stale_watch.py (self-heal desktop-3a6213ce74).")
    return {'title': f'user-steps: decision {oid[:8]} was admitted but its turn failed and never ran',
            'body': body, 'idempotency_key': 'user-steps-failed-decision:' + oid, 'assignee': 'default'}


def create_card(c):
    cmd = ['hermes', 'kanban', 'create', c['title'], '--body-file', '-', '--assignee', c['assignee'],
           '--idempotency-key', c['idempotency_key'], '--json']
    p = subprocess.run(cmd, input=c['body'], text=True, capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout.strip()


def run(core, chat, store, db=None, now=None, min_age=MIN_AGE, create=create_card):
    now = time.time() if now is None else now
    opened = []
    for item in chat.answered_open(core, store, db):
        if item['replyAt'] is None or now - item['replyAt'] < min_age:
            continue
        create(card(item))  # idempotent on the key: a rerun returns the existing card
        opened.append(item['requestId'])
    for d in chat.duplicate_open(core, store):
        create(dup_card(d))  # one card per session; idempotent on its key
        opened.append('dup:' + d['session'])
    for p in chat.cross_session_duplicates(core, store):
        create(cross_dup_card(p))  # one card per pair; idempotent on its key
        opened.append('xdup:' + ':'.join(p['ids']))
    for f in failed_decisions(core, store, db, now=now):
        create(failed_decision_card(f))  # one card per operation; idempotent on its key
        opened.append('failed-decision:' + f['operationId'])
    return opened


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-age', type=int, default=MIN_AGE, help='seconds since the user reply (default 3600)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    core, chat = _load('core'), _load('chat')
    create = (lambda c: print(json.dumps(c))) if a.dry_run else create_card
    ids = run(core, chat, core.ActionStore(), min_age=a.min_age, create=create)
    print(json.dumps({'cards': ids}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
