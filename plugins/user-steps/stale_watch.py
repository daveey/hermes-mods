#!/usr/bin/env python3
"""Open one Kanban card per user-steps request that is still open after its question was answered.

Detection countermeasure for desktop-1321064734. Run periodically on EVERY gateway (action stores
are per host), e.g. from cron: `python3 ~/.hermes/plugins/user-steps/stale_watch.py`.

Reads chat.answered_open() (the set behind /ping stale.answeredOpen) directly, no HTTP or token. For
each request whose user reply is older than --min-age (default 1 h) it creates an agent-owned Kanban
card with idempotency key `user-steps-stale:<requestId>`, so reruns never duplicate and a retired
request (no longer open) gets no card. Never creates an Inbox card. Read-only for the action store.
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
