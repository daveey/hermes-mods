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

Old-ask review (cron runs only, not run()'s default): at most one card a day (key
`user-steps-old-asks:<PT date>`) lists undecided requests asked more than 72 h ago and not marked reviewed in the
last 7 days, with the owner chat's title and last turn (PT) and its linked/named card states. It is created on this
gateway's board through the kanban_create tool handler (placement='local', requires declared), never the CLI. The
watcher decides and withdraws nothing. The reviewing worker uses the sanctioned CLI:
`--withdraw ID --reason ... --evidence ...` (core.withdraw_request by=hygiene; refused for recorded decisions,
card-bound requests and owners not on this gateway) or `--reviewed ID --reason ... [--evidence ...]` (side table
old_ask_reviews; hides the request from the review for 7 days, it stays open).
"""
import argparse
import importlib.util
import json
import sqlite3
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
    body = (f"A session holds more than one open user-steps request that look like the same decision (the "
            f"core.restates_open_ask detector: title, word or menu overlap). Overlap is NOT identity: two requests "
            f"with the same title can be different decisions.\n\n- Namespace: {d['namespace']}\n"
            f"- Session: {d['session']}\n- Possible duplicate groups (request ids, oldest first):\n{lines}\n\n"
            f"Trace each request to its source (originating reply, tool call, notice). Withdraw a newer one with a "
            f"reason only when that source proves it re-asks the same decision; otherwise leave both open. "
            f"This watcher never withdraws anything.\n"
            f"Source: plugins/user-steps/stale_watch.py. Done when /ping stale.duplicateOpen no longer counts "
            f"this session.")
    return {'title': f"user-steps: duplicate open requests in session {d['session']}", 'body': body,
            'idempotency_key': f"user-steps:dup:{d['namespace']}:{d['session']}", 'assignee': 'default'}


def cross_dup_card(p):
    a, b = p['ids']
    sessions = ', '.join(p['sessions'])
    body = (f"Two sessions on this gateway hold open user-steps requests that look like the same decision "
            f"(the core.restates_open_ask detector; overlap is not identity).\n\n- Namespace: {p['namespace']}\n"
            f"- Sessions: {sessions}\n- Request ids: {a}, {b}\n\n"
            f"Trace both to their source. Withdraw the newer one with a reason only when its source proves it "
            f"relays the other's ask (e.g. a notice relay); otherwise leave both open. "
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


# ---- old-ask review: one local card a day; withdrawal only through the sanctioned CLI with evidence ----------

OLD_ASK_AGE = 72 * 3600
REVIEW_WINDOW = 7 * 86400
REVIEW_KEY = 'user-steps-old-asks:'
REVIEW_REQUIRES = ['terminal', 'kanban']
PT = 'America/Los_Angeles'
_REVIEWS_DDL = ('CREATE TABLE IF NOT EXISTS old_ask_reviews (namespace TEXT, id TEXT, revision INTEGER, '
                'reviewed_at REAL, reason TEXT, evidence TEXT, PRIMARY KEY(namespace,id))')


def _pt(ts, fmt='%Y-%m-%d %H:%M %Z'):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo(PT)).strftime(fmt)


def _reviewed(c):
    """Reviews apply only to the reviewed revision; never create the side table on read."""
    try:
        return {(r[0], r[1], r[2]): r[3] for r in c.execute(
            'SELECT namespace, id, revision, reviewed_at FROM old_ask_reviews')}
    except sqlite3.OperationalError:
        return {}


def _review_ledger(core, owner):
    """Read the known local owner's ledger, including non-default profiles; never initialize a store."""
    if owner['gatewayId'] != _local_gateway(core):
        return None
    db = _owner_db(core, owner)
    return core.Ledger(Path(db).parent / 'user-steps', db=db) if db is not None else None


def _legacy_asked_at(core, owner, body):
    """Only original revisions can inherit legacy evidence. Later unstamped revisions have unknown age."""
    if body.get('revision') != 1:
        return None
    aliases = [a for a in body.get('aliases', []) if a.get('type') == 'user_step'
               and a.get('gatewayId') == owner['gatewayId'] and a.get('profile') == owner['profile']]
    if not aliases:
        return None
    ledger = _review_ledger(core, owner)
    if ledger is None:
        return None
    try:
        data = ledger._read_strict(ledger.root(owner['storedSessionId']), legacy=True)
        times = []
        for alias in aliases:
            matches = [s for s in data['steps'] if s.get('id') == alias.get('id')]
            if len(matches) != 1:
                continue
            step = matches[0]
            if step.get('requestId') not in (None, body['requestId']):
                continue
            ts = step.get('created')
            if isinstance(ts, (int, float)) and 0 < ts < float('inf'):
                times.append(ts)
        return max(times) if times else None
    except (OSError, ValueError, LookupError):
        return None  # missing/unreadable evidence is not proof of age


def old_asks(core, store, now=None, *, min_age=OLD_ASK_AGE, window=REVIEW_WINDOW):
    """Undecided requests asked more than ``min_age`` ago and not marked reviewed within ``window``. Read-only.

    Undecided: open/needs_context, no resolution, no recorded operation for the current revision. Requests bound
    to a decision card are skipped (that card is the ask; withdrawal refuses them). A request with no recorded
    ask time is skipped: its age is unproven."""
    now = time.time() if now is None else now
    out = []
    with store._read() as c:
        reviewed = _reviewed(c)
        rows = c.execute("SELECT r.namespace, r.id, r.session, r.revision, v.body, l.created_at, l.asked_revision FROM requests r "
                         "JOIN revisions v ON v.namespace=r.namespace AND v.id=r.id AND v.revision=r.revision "
                         "LEFT JOIN lifecycle l ON l.namespace=r.namespace AND l.id=r.id "
                         "WHERE r.state IN ('open','needs_context') AND r.resolution IS NULL ORDER BY r.rowid").fetchall()
        for ns, rid, session, rev, raw, asked, asked_rev in rows:
            if now - reviewed.get((ns, rid, rev), float('-inf')) < window:
                continue
            if c.execute('SELECT 1 FROM operations WHERE namespace=? AND request=? AND revision=?',
                         (ns, rid, rev)).fetchone():
                continue
            try:
                body, (gateway, profile) = json.loads(raw), json.loads(ns)
            except (ValueError, TypeError):
                continue
            if any(a.get('type') == 'kanban' for a in body.get('aliases', [])):
                continue
            owner = {'gatewayId': gateway, 'profile': profile, 'storedSessionId': session}
            # A missing revision stamp is usable only for an original legacy ask.
            if asked_rev != rev and not (rev == 1 and asked_rev is None):
                continue
            if asked is None:
                asked = _legacy_asked_at(core, owner, body)
            if asked is None or now - asked <= min_age:
                continue
            out.append({'requestId': rid, 'title': body.get('title') or '', 'namespace': ns, 'revision': rev,
                        'owner': {'gatewayId': gateway, 'profile': profile, 'storedSessionId': session},
                        'askedAt': asked, 'request': body})
    return out


def _local_gateway(core):
    try:
        return core.server_owner('old-ask-review')['gatewayId']
    except Exception:
        return None  # unknown identity: every owner is treated as foreign (nothing read, nothing withdrawn)


def _owner_db(core, owner):
    """The owner profile's state.db on this install, or None when the profile is unknown."""
    try:
        from hermes_cli.profiles import get_profile_dir, profile_exists
        if not profile_exists(owner['profile']):
            return None
        return core._state_db(Path(get_profile_dir(owner['profile'])))
    except Exception:
        return None


def chat_facts(core, owner, *, local_gateway, owner_db):
    """{'status', 'title', 'lastTurnAt'} for the owner chat. Never guesses: a foreign gateway, unknown profile or
    failed read is reported as such."""
    if not local_gateway or owner['gatewayId'] != local_gateway:
        return {'status': 'on another gateway (not read)'}
    db = owner_db(owner)
    if db is None:
        return {'status': 'unknown profile (not read)'}
    try:
        c = core._ro_connect(db, timeout=2)
        try:
            row = c.execute('SELECT title FROM sessions WHERE id=?', (owner['storedSessionId'],)).fetchone()
            if row is None:
                return {'status': 'not found in the owner store'}
            last = c.execute('SELECT max(timestamp) FROM messages WHERE session_id=?',
                             (owner['storedSessionId'],)).fetchone()[0]
        finally:
            c.close()
    except Exception as exc:
        return {'status': f'unreadable ({type(exc).__name__})'}
    return {'status': 'found', 'title': row[0], 'lastTurnAt': last}


def linked_cards(core, request, owner, boards):
    """(cards, problems): owner/origin tasks.session_id plus tui notification chat links and named cards.

    Older boards can lack either link surface. Preserve named-card results and explicit unknown-read notes.
    Subscriptions are context, not proof that a chat owns or has completed the task."""
    named = {tid for _b, tid in core._watched_cards(request, owner, {})}
    cards, problems = [], []
    for slug, path in boards:
        try:
            c = core._ro_connect(path, timeout=2)
            try:
                rows = [r for tid in sorted(named) for r in c.execute(
                    'SELECT id, title, status FROM tasks WHERE id=?', (tid,)).fetchall()]
                columns = {r[1] for r in c.execute('PRAGMA table_info(tasks)')}
                if 'session_id' in columns:
                    rows += c.execute('SELECT id, title, status FROM tasks WHERE session_id=? ORDER BY id',
                                      (owner['storedSessionId'],)).fetchall()
                else:
                    problems.append(f'board {slug} origin chat links unavailable (no tasks.session_id)')
                subcols = {r[1] for r in c.execute('PRAGMA table_info(kanban_notify_subs)')}
                if subcols:
                    try:
                        params = [owner['storedSessionId']]
                        profile = ''
                        if 'notifier_profile' in subcols:
                            profile = ' AND (s.notifier_profile=? OR s.notifier_profile IS NULL)'
                            params.append(owner['profile'])
                        rows += c.execute('SELECT t.id, t.title, t.status FROM tasks t JOIN kanban_notify_subs s '
                                          "ON s.task_id=t.id WHERE s.platform='tui' AND s.chat_id=?" + profile,
                                          params).fetchall()
                    except sqlite3.Error as exc:
                        problems.append(f'board {slug} subscription chat links unreadable ({type(exc).__name__})')
                else:
                    problems.append(f'board {slug} subscription chat links unavailable (no kanban_notify_subs)')
            finally:
                c.close()
        except Exception as exc:
            problems.append(f'board {slug} unreadable ({type(exc).__name__})')
            continue
        seen = {(x['board'], x['id']) for x in cards}
        for tid, title, status in rows:
            if (slug, tid) not in seen:
                seen.add((slug, tid))
                cards.append({'board': slug, 'id': tid, 'title': title, 'status': status,
                              'link': 'named by the ask' if tid in named else 'linked to the chat'})
    return cards, problems


def _item_lines(it):
    o, f = it['owner'], it['chat']
    if f['status'] == 'found':
        last = _pt(f['lastTurnAt']) if f.get('lastTurnAt') else 'no messages'
        chat_line = f"\"{f.get('title') or '(untitled)'}\", last turn {last}"
    else:
        chat_line = f['status']
    lines = [f"- **{it['requestId']}**: {it['title']}",
             f"  - Asked: {_pt(it['askedAt'])} (revision {it['revision']})",
             f"  - Owner chat: {o['profile']} / {o['storedSessionId']} ({o['gatewayId']}): {chat_line}"]
    cards = it.get('cards') or []
    lines.append('  - Cards: ' + ('; '.join(f"{c['id']} [{c['board']}] {c['status']} ({c['link']}): {c['title']}"
                                            for c in cards) if cards else 'none found'))
    for p in it.get('problems') or []:
        lines.append(f'  - Note: {p}')
    return lines


def review_card(items, now):
    script = '~/.hermes/plugins/user-steps/stale_watch.py'
    listing = '\n'.join(line for it in items for line in _item_lines(it)) or '- (none)'
    body = (f"User-steps requests still undecided more than 72 h after they were asked, not reviewed in the last "
            f"7 days (action store on this host). The watcher withdrew and decided nothing.\n\n{listing}\n\n"
            f"For EACH request, read the owner chat and the cards above, then do exactly one:\n"
            f"1. Withdraw it only with evidence, one of: a linked or named card is done or archived and the ask was "
            f"about it; it was decided elsewhere (quote the user's words and where); its time window expired (say "
            f"when); it is a test fixture (say which); or it asks the user for agent work (then do that work or file "
            f"a card for it first, and cite it).\n"
            f"   `python3 {script} --withdraw <id> --reason \"<why>\" --evidence \"<card id / quote / date>\"`\n"
            f"2. Otherwise keep it: `python3 {script} --reviewed <id> --reason \"<why it is still a live ask>\" "
            f"--evidence \"<what you checked>\"` (hides it from this review for 7 days; it stays open for the user).\n"
            f"Never decide a request for the user. Withdrawal is refused for recorded decisions and card-bound "
            f"requests. A chat that is on another gateway, unknown or unreadable is not evidence: keep those.\n"
            f"Source: plugins/user-steps/stale_watch.py (old-ask review).")
    return {'title': f'user-steps: review {len(items)} undecided request(s) older than 72 h', 'body': body,
            'idempotency_key': REVIEW_KEY + _pt(now, '%Y-%m-%d'), 'assignee': 'default',
            'placement': 'local', 'requires': list(REVIEW_REQUIRES)}


def review_old_asks(core, store, now=None, create=None, *, local_gateway=None, owner_db=None, boards=None):
    """Create (via ``create``) at most one review card for today's old asks; return the listed request ids."""
    if create is None:
        raise ValueError('review_old_asks needs a card creator')
    now = time.time() if now is None else now
    items = old_asks(core, store, now=now)
    if not items:
        return []
    owner_db = owner_db or (lambda o: _owner_db(core, o))
    boards = core.kanban_boards() if boards is None else boards
    for it in items:
        it['chat'] = chat_facts(core, it['owner'], local_gateway=local_gateway, owner_db=owner_db)
        it['cards'], it['problems'] = linked_cards(core, it['request'], it['owner'], boards)
    create(review_card(items, now))  # idempotent on the daily key
    return [it['requestId'] for it in items]


def create_review_card(c, registry=None):
    """Create the card through the kanban_create tool handler (not the CLI), pinned to this board."""
    if registry is None:
        import tools.kanban_tools  # noqa: F401  registers kanban_create
        from tools.registry import registry
    entry, schema = registry.get_entry('kanban_create'), registry.get_schema('kanban_create')
    props = ((schema or {}).get('parameters') or {}).get('properties') or {}
    missing = [k for k in ('placement', 'requires', 'idempotency_key') if k not in props]
    if entry is None or missing:
        raise RuntimeError(f'kanban_create tool unavailable or lacks {missing}: refusing to create the review card')
    args = {k: c[k] for k in ('title', 'body', 'assignee', 'idempotency_key', 'placement', 'requires')}
    out = entry.handler(args)
    try:
        res = json.loads(out) if isinstance(out, str) else out
    except ValueError:
        raise RuntimeError(f'kanban_create returned non-JSON: {str(out)[:300]}')
    if not isinstance(res, dict) or res.get('error') or not res.get('task_id'):
        raise RuntimeError(f'kanban_create failed: {str(out)[:500]}')
    return res['task_id']


def _find(core, store, rid):
    with store._read() as c:
        rows = c.execute('SELECT namespace, session, revision, state FROM requests WHERE id=?', (rid,)).fetchall()
    if len(rows) != 1:
        raise core.ActionError('Unknown request' if not rows else 'Ambiguous request id', 404)
    ns, session, rev, state = rows[0]
    gateway, profile = json.loads(ns)
    return {'gatewayId': gateway, 'profile': profile, 'storedSessionId': session}, ns, rev, state


def _need(core, value, what):
    if not isinstance(value, str) or not value.strip():
        raise core.ActionError(f'{what} required')
    return value.strip()


def withdraw_old(core, store, rid, *, reason, evidence, local_gateway, ledger=None, now=None):
    """Sanctioned hygiene withdrawal: by=hygiene with reason and evidence. Refused for recorded decisions and
    card-bound requests (ActionStore.withdraw), and for owners not on this gateway or an unknown identity."""
    reason, evidence = _need(core, reason, 'Reason'), _need(core, evidence, 'Evidence')
    owner, _ns, rev, _state = _find(core, store, rid)
    if not local_gateway or owner['gatewayId'] != local_gateway:
        raise core.ActionError('Request owner is not on this gateway; not withdrawn', 409)
    return core.withdraw_request(store, ledger, owner, rid, by='hygiene', reason=reason, evidence=evidence,
                                 expected_revision=rev, now=now)


def mark_reviewed(core, store, rid, *, reason, evidence=None, now=None):
    """Record that a reviewer kept an open request (hides it from the review for REVIEW_WINDOW). Not a decision."""
    reason = _need(core, reason, 'Reason')
    now = time.time() if now is None else now
    owner, ns, rev, state = _find(core, store, rid)
    if state not in ('open', 'needs_context'):
        raise core.ActionError('Request is not open', 409)
    with store._tx() as c:
        c.execute(_REVIEWS_DDL)
        c.execute('INSERT INTO old_ask_reviews VALUES (?,?,?,?,?,?) ON CONFLICT(namespace,id) DO UPDATE SET '
                  'revision=excluded.revision, reviewed_at=excluded.reviewed_at, reason=excluded.reason, '
                  'evidence=excluded.evidence', (ns, rid, rev, now, reason[:300], (evidence or '').strip()[:500]))
    return {'requestId': rid, 'revision': rev, 'reviewedAt': now, 'reason': reason[:300]}


def _store(core):
    return core.ActionStore()


def _read_only_store(core):
    """No ActionStore initialization/migrations; every query is enforced read-only, including legacy _tx reads."""
    store = core.ActionStore.__new__(core.ActionStore)
    store.path = core.home() / 'user-steps' / 'actions.sqlite3'
    store.timeout = 30

    def connect():
        c = core._ro_connect(store.path, timeout=store.timeout)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        return c

    store._connect = connect
    store._tx = store._read
    return store


def _ledger(core, owner):
    """The legacy step ledger only for the current profile's chats; another profile's ledger lives elsewhere."""
    try:
        from hermes_cli.profiles import current_profile_name
        return core.Ledger() if current_profile_name() == owner['profile'] else None
    except Exception:
        return None


def run(core, chat, store, db=None, now=None, min_age=MIN_AGE, create=create_card, review_create=None):
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
    if review_create is not None:
        listed = review_old_asks(core, store, now=now, create=review_create, local_gateway=_local_gateway(core))
        if listed:
            opened.append('old-asks:' + _pt(now, '%Y-%m-%d'))
    return opened


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-age', type=int, default=MIN_AGE, help='seconds since the user reply (default 3600)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--withdraw', metavar='ID', help='withdraw one request (by=hygiene); needs --reason and --evidence')
    ap.add_argument('--reviewed', metavar='ID', help='keep one open request; hides it from the old-ask review 7 days')
    ap.add_argument('--reason')
    ap.add_argument('--evidence')
    a = ap.parse_args(argv)
    if a.dry_run and (a.withdraw or a.reviewed):
        print(json.dumps({'error': '--dry-run cannot be combined with --withdraw or --reviewed'}))
        return 2
    core = _load('core')
    if a.withdraw and a.reviewed:
        print(json.dumps({'error': 'use --withdraw or --reviewed, not both'}))
        return 2
    if a.withdraw or a.reviewed:
        store = _store(core)
        try:
            if a.withdraw:
                owner = _find(core, store, a.withdraw)[0]
                v = withdraw_old(core, store, a.withdraw, reason=a.reason, evidence=a.evidence,
                                 local_gateway=_local_gateway(core), ledger=_ledger(core, owner))
                print(json.dumps({'withdrawn': v}))
            else:
                print(json.dumps({'reviewed': mark_reviewed(core, store, a.reviewed, reason=a.reason,
                                                            evidence=a.evidence)}))
        except core.ActionError as exc:
            print(json.dumps({'error': str(exc), 'status': exc.status}))
            return 2
        return 0
    chat = _load('chat')
    create = (lambda c: print(json.dumps(c))) if a.dry_run else create_card
    review_create = (lambda c: print(json.dumps(c))) if a.dry_run else create_review_card
    store = _read_only_store(core) if a.dry_run else _store(core)
    ids = run(core, chat, store, min_age=a.min_age, create=create, review_create=review_create)
    print(json.dumps({'cards': ids}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
