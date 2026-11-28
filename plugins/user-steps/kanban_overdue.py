"""Overdue-triage cards: a red session gets a card it can act on.

Core turns a chat red when a card it links sits in triage past the overdue threshold
(``hermes_cli.kanban_triage_attention``), deliberately without a human_request. Nothing
was drawn for that red: no ask, nothing to click. This module mirrors such a card into
the chat's decision queue as one bound request with three choices:

* **Release** — a person's release: core ``kanban_release.release`` with a human writer
  (spend/scope stops, reviewer allowances included), else core promotion for a plain park.
  Never offered while a provider-safety restriction holds the card; the card says why.
* **Archive** — stops the card for good (core ``archive_task``).
* **Keep parked** — leaves it in triage and writes core's ``triage_park_acknowledged``
  event, which quiets the red for 24 h or until the card's next lifecycle event.

The snapshot is frozen into the immutable binding; delivery rechecks it under the board
write transaction exactly as ordinary card asks are rechecked. The card is never minted
from age alone without core support: an older core without the acknowledgement kind or
the overdue predicate publishes nothing.
"""

import json
import re
import time

VERSION = 1
CHOICES = ('release', 'archive', 'keep_parked')
_REASON_KEYS = ('summary', 'reason', 'next_step', 'message')
_MAX_REASON = 220


def _attention():
    try:
        from hermes_cli import kanban_triage_attention as attention
    except ImportError:
        return None
    return attention if hasattr(attention, 'PARK_ACK') and hasattr(attention, 'quiet_until') else None


def _plain(value, limit=_MAX_REASON):
    """One plain line: no card ids to type, no markdown, bounded."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    text = re.sub(r'\bt_[0-9a-f]{8,}\b', 'another card', text)
    text = re.sub(r'\bcomment\s*\d+\b', 'a comment', text)
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + '…'
    return text


def _payload(raw):
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _hold_reason(c, task, attention):
    """``(event kind, plain reason)`` of the newest lifecycle event that parked the card."""
    rows = c.execute(
        "SELECT kind, payload FROM task_events WHERE task_id=? AND kind IN (SELECT value FROM json_each(?)) "
        "ORDER BY id DESC LIMIT 20", (task, json.dumps(attention.LIFECYCLE_KINDS))).fetchall()
    kind = None
    for event_kind, raw in rows:
        payload = _payload(raw)
        if attention.landing(event_kind, payload) is None:
            continue
        kind = kind or event_kind
        for key in _REASON_KEYS:
            if isinstance(payload.get(key), str) and payload[key].strip():
                return kind, _plain(payload[key])
    return kind, ''


def _route(c, task):
    """``('release'|'promote'|None, why-not)``: what Release does on this card."""
    try:
        from hermes_cli.kanban_refusal_receipt import restricted
        if restricted(c, task):
            return None, ('It stopped on a provider safety refusal, so it cannot be released from here; '
                          'archive it or keep it parked.')
    except ImportError:
        return None, 'This gateway cannot check its safety restrictions, so Release is not offered.'
    try:
        from hermes_cli import kanban_release
        if kanban_release.held(c, task) is not None:
            return 'release', ''
    except ImportError:
        pass
    return 'promote', ''


def _holder(kind, block_kind):
    if kind in ('decision_retained', 'scope_stopped') or block_kind == 'scope_stopped':
        return "its agent, which stopped the work and asked for no further approach"
    if kind and kind.startswith('spend_') or block_kind == 'spend':
        return 'its run budget, which ran out'
    if block_kind == 'lane_gate':
        return 'the lane planner, which has not admitted it'
    if block_kind == 'needs_input':
        return 'its agent, which asked for input in another chat'
    if block_kind == 'dependency':
        return 'its agent, which is waiting on other work'
    return 'its agent'


def _when(ts):
    try:
        return time.strftime('%b %d %H:%M UTC', time.gmtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return 'an unknown time'


def _runs():
    try:
        from hermes_cli.kanban_release import default_runs
        return default_runs()
    except Exception:  # config unreadable: the release itself still applies the real default
        return 10


def _links_session(mirror, c, task):
    """A local user chat linked to the card (core ``task_chat_links``), when the creator chat is not one."""
    try:
        rows = c.execute(
            "SELECT chat_session FROM task_chat_links WHERE task_id=? AND kind='created' "
            "AND COALESCE(released, 0)=0 ORDER BY chat_session", (task,)).fetchall()
    except Exception:
        return None
    return next((r[0] for r in rows if r[0] and mirror.session_exists(r[0])), None)


def snapshot(mirror, c, row, task, owner, *, presentation=True, now=None):
    """The frozen overdue-triage snapshot for ``task`` in ``owner``'s chat, else None."""
    attention = _attention()
    if attention is None or row.get('status') != 'triage' or row.get('human_request') not in (None, ''):
        return None
    annotated = attention.annotate(c, [{'id': task, 'status': 'triage'}])[0]
    if not attention.overdue(annotated, now=time.time() if now is None else now):
        return None
    sid = mirror._ask_session(c, row, task, owner) or _links_session(mirror, c, task)
    if not sid or (presentation and mirror.tip(sid) != owner['storedSessionId']):
        return None
    generation = c.execute(
        "SELECT MAX(id) FROM task_events WHERE task_id=? AND kind IN (SELECT value FROM json_each(?))",
        (task, json.dumps(attention.ATTENTION_KINDS))).fetchone()[0]
    kind, reason = _hold_reason(c, task, attention)
    route, no_release = _route(c, task)
    title = _plain(row.get('title') or 'Untitled card', 120)
    holder = _holder(kind, row.get('block_kind'))
    entered = annotated['triage_entered_at']
    info = dict(version=VERSION, entered_at=entered, route=route, holder=holder, reason=reason)
    return dict(task=task, session=sid, generation=generation, overdue=info, title=title,
                ask=ask(title, info, no_release, _runs()))


def ask(title, info, no_release, runs):
    """The card text David reads: what it is, why it stopped, what each button does and costs."""
    since = _when(info['entered_at'])
    why = info['reason'] or 'no reason was recorded'
    situation = (f'“{title}” has been parked since {since} by {info["holder"]}: {why}. '
                 'Nothing is scheduled to move it, so this chat stays red until someone decides.')
    options, choices = [], []
    if info['route'] == 'release':
        options.append(f'A) Release: the card resumes on its original goal with up to {runs} more agent runs; '
                       'counts as your release on the Board; park it again to undo.')
        choices.append(dict(label='A: Release', value='release',
                            explain=f'Resumes the card with up to {runs} more agent runs.'))
    elif info['route'] == 'promote':
        options.append('A) Release: the card goes back to the ready queue and an agent picks it up; '
                       'normal agent runs; park it again to undo.')
        choices.append(dict(label='A: Release', value='release',
                            explain='Puts the card back in the queue for an agent.'))
    letter = 'B' if choices else 'A'
    options.append(f'{letter}) Archive: stops the card for good; no more runs or cost; reopen the card to undo.')
    choices.append(dict(label=f'{letter}: Archive', value='archive', explain='Stops the card; nothing more runs.'))
    keep = 'C' if letter == 'B' else 'B'
    options.append(f'{keep}) Keep parked: changes nothing and hides this red for 24 h or until the card changes; free.')
    choices.append(dict(label=f'{keep}: Keep parked', value='keep_parked',
                        explain='Leaves it parked; the red returns in 24 h if nothing changes.'))
    if info['route']:
        recommend = 'A - it was parked, not finished; releasing lets it finish, and you can stop it again any time.'
        choices[0]['label'] += ' (recommended)'
        choices[0]['recommended'] = True
    else:
        recommend = f'{keep} - it cannot be released here; keeping it parked costs nothing while its owner resolves it.'
        choices[-1]['label'] += ' (recommended)'
        choices[-1]['recommended'] = True
    scope = '\n'.join([f'Situation: {situation}' + (f' {no_release}' if no_release else ''), 'Options:', *options,
                       f'Recommend: {recommend}'])
    tried = ('Tried: nothing acts on a parked card by itself; its holder has not moved it since it was parked. '
             'Only you can: release it, archive it, or keep it parked.')
    return dict(action=f'Decide what happens to the parked card “{title}”.', interaction='approval',
                why_human=tried, scope=scope, choices=[dict(c) for c in choices], overdue=True)


def _encoded(value):
    # Byte-identical to kanban_mirror.encoded: bindings and receipts compare these strings.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def deliver(mirror, api, c, binding, op):
    """Release / Archive / Keep parked on an overdue-triage card, as the person's act the click is.

    Release goes through core ``kanban_release.release`` with a human writer first, so it counts as a
    person's release on the Board (spend/scope stops and an exhausted reviewer allowance included); only a
    card no stop holds (``not_held``) is promoted instead. Every write is fenced on the exact frozen
    snapshot. A refusal leaves the card as it is, says why on the card and fails the operation."""
    import hashlib
    core, encoded = mirror.core, _encoded
    snap = json.loads(binding['snapshot'])
    request = json.loads(binding['request'])
    choice = next((i for i, ch in enumerate(request['choices']) if ch['id'] == op['decision']['choiceId']), None)
    if choice is None:
        raise core.ActionError('Unknown card choice', 409)
    value = snap['ask']['choices'][choice]['value']
    if value not in CHOICES or (value == 'release' and not snap['overdue']['route']):
        raise core.ActionError('Unknown card choice', 409)
    operation = op['operationId']
    task = binding['task']
    label = {'release': 'Release', 'archive': 'Archive', 'keep_parked': 'Keep parked'}[value]
    comment = f'{label} chosen in Desktop for this overdue parked card.\nOperation: {operation}'
    receipt = dict(destination='kanban_ask', board=binding['board'], task=task,
                   operationId=operation, value=value, executionStarted=False,
                   fingerprint=hashlib.sha256(encoded([op['intent'], op['decision']]).encode()).hexdigest())

    def fence(conn):
        if mirror._receipt(conn, binding, op):
            return 'already recorded'
        current = mirror._snapshot(conn, task, op['owner'], presentation=False)
        return None if current is not None and encoded(current) == binding['snapshot'] else 'changed ask'

    def changed():
        return core.ActionError('Source card ask changed or transition refused', 409)

    def record(extra, text):
        with api.write_txn(c):
            old = mirror._receipt(c, binding, op)
            if old:
                return old
            api.add_comment(c, task, 'user-steps', text)
            c.execute('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)',
                      (task, 'user_steps_answer', encoded(dict(user_steps_projection=dict(receipt, **extra))),
                       int(time.time())))
        return mirror._receipt(c, binding, op)

    def refuse(reason):
        if not c.execute("SELECT 1 FROM task_comments WHERE task_id=? AND author='user-steps' AND body=?",
                         (task, reason)).fetchone():  # a redelivery says it once
            api.add_comment(c, task, 'user-steps', reason)
        raise core.ActionError(reason.split('\n', 1)[0], 409)

    if value == 'archive':
        api.archive_task(c, task, precondition=fence, comment=('user-steps', comment),
                         event_extra={'user_steps_projection': receipt})
    elif value == 'keep_parked':
        attention = _attention()
        with api.write_txn(c):
            if not mirror._receipt(c, binding, op):
                if fence(c):
                    raise changed()
                api.add_comment(c, task, 'user-steps', comment)
                c.execute('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)',
                          (task, attention.PARK_ACK, encoded(dict(by='desktop', quiet_s=attention.PARK_QUIET_S,
                                                                  user_steps_projection=receipt)),
                           int(time.time())))
    else:
        release = getattr(api, 'release', None)
        note = 'Desktop Release, operation ' + operation
        done = release.released_by(c, task, note) if release is not None else None
        if done is not None:  # a crash after the release, before its receipt: record it now
            return record(dict(released=done, outcome='released'), comment + '\nReleased.')
        if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='promoted_manual' "
                     "AND json_extract(payload,'$.reason')=?", (task, note)).fetchone():
            return record(dict(released=task, outcome='promoted'), comment)
        if fence(c):
            raise changed()
        if release is not None:
            out = release.release(c, task, writer=release.human_writer('desktop-operation:' + operation),
                                  author='user-steps', note=note, precondition=fence)
            if out['outcome'] == 'refused' and out['refusal'] == 'ask_changed':
                raise changed()
            if out['outcome'] != 'refused':
                return record(dict(released=out['target'], outcome=out['outcome']), comment + '\nReleased.')
            if out['refusal'] != 'not_held':
                refuse(f"Desktop Release did not release this card ({out['refusal']}): {out['message']}"
                       f'\nOperation: {operation}')
        from hermes_cli.kanban_db_promote import promote_task
        ok, why = promote_task(c, task, actor='user-steps', reason=note, precondition=fence)
        if not ok:
            if why in ('already recorded', 'changed ask') or 'changed during promotion' in str(why):
                raise changed()
            refuse(f'Desktop Release did not move this card: {why}\nOperation: {operation}')
        return record(dict(released=task, outcome='promoted'), comment)
    recorded = mirror._receipt(c, binding, op)
    if not recorded:
        raise changed()
    return recorded
