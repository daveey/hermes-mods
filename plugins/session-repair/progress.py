"""BS-no-progress: per-chat progress snapshots and deterministic predictions (skill `session-progress`).

[daveey 2026-11-27] "make a skill that measures, looks at a session, and makes sure that it is moving forward. It
then makes predictions about what it expects to see from this session in half an hour ... run that skill in a loop on
all the sessions so that we can actively detect sessions that have not made progress."

No model call per session. Each tick observes every scanned chat's open cards (the linked cards and their chain,
the set scan.py walks) and, for chats with no open card, their live background processes; compares the observation
with the previous tick's snapshot; and writes a new snapshot with the predictions it expects to see met by the next
window. The scanner only reads snapshots (`load_snapshot`); `persist` (called by run.py's tick, never by a scan)
writes them under `$HERMES_HOME/state/session-progress/<chat>.json`.

Verdicts, one per chat per tick (skill `session-progress` defines them):
- `baseline`: no usable previous snapshot (first sight, or older than STALE_SNAPSHOT_S): nothing judged.
- `progress`: some progress signal since the last snapshot.
- `valid_red`: every open card waits on a valid human_request (or a deliberate core hold).
- `healthy_running` / `healthy_wait` / `clock_not_due` / `pending`: no progress yet, but no prediction is due.
- `missed_once` / `missed_twice` / `process_only`: a due prediction was not met.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

VERSION = 1
PREDICT_S = 30 * 60               # "what it expects to see from this session in half an hour"
HEALTHY_RUN_S = 30 * 60           # a running card with a progress event this recent is a healthy wait
CLOCK_GRACE_S = 10 * 60           # a clock wake is due at wake_at + 10 min (not +30 min)
CI_GRACE_S = 15 * 60              # a CI/deploy run is due by park + expected_s + 15 min (one tick of slack)
AWAITED_DONE_GRACE_S = 10 * 60    # a park whose awaited card finished wakes within 10 min (scan.PARENT_DONE_S)
READY_EXPLAINED_S = 2 * 3600      # a ready card whose wait the dispatcher explained (pacing, quota)
STALE_SNAPSHOT_S = 2 * 3600       # an older snapshot is a new baseline (hub down, chat dropped out and back)
KEEP_S = 3 * 86400                # snapshot files untouched this long are pruned
SATISFIED = ('done', 'archived')
# Liveness and infrastructure ticks: not progress. A heartbeat WITH a note is an agent-written progress report and
# counts; the runtime's automatic heartbeat (no payload) does not.
NOISE = frozenset({'heartbeat', 'external_watch', 'wake_check_alive', 'wake_check_broken', 'wake_check_lost',
                   'wake_check_stale', 'wake_episode_evidence', 'wake_episode_started', 'wake_probe_unproven_notice',
                   'dispatch_skipped', 'comment_delivered', 'lane_assigned', 'hub_admission'})
QUEUE_RE = re.compile(r'merge[ -]?queue|(?:^|[/:._-])queue(?:$|[/:._-])', re.I)


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _json(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (str, bytes)) and v:
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


# ── snapshot files ────────────────────────────────────────────────────────────────────────────────────────────────
def progress_dir(home: Path) -> Path:
    return Path(home) / 'state' / 'session-progress'


def _file(home: Path, chat: str) -> Path:
    return progress_dir(home) / (re.sub(r'[^A-Za-z0-9_.-]', '_', chat) + '.json')


def load_snapshot(home: Path, chat: str) -> Optional[dict]:
    try:
        data = json.loads(_file(home, chat).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('version') == VERSION and data.get('chat') == chat else None


def persist(snapshots: Optional[dict], home: Path, now: Optional[float] = None) -> int:
    """Write each chat's snapshot atomically; prune files idle KEEP_S. Returns how many were written."""
    if not snapshots:
        return 0
    d = progress_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for chat, snap in snapshots.items():
        p = _file(home, chat)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=f'.{p.name}.')
        with os.fdopen(fd, 'w') as f:
            json.dump(snap, f, indent=1, default=str)
        os.replace(tmp, p)
        n += 1
    at = max((_num(s.get('at')) or 0 for s in snapshots.values()), default=0) if now is None else now
    for p in d.glob('*.json'):
        try:
            if at - p.stat().st_mtime > KEEP_S:
                p.unlink()
        except OSError:
            pass
    return n


# ── observation (read-only, through the scanner's query-only connections) ─────────────────────────────────────────
def last_progress_event(conn, card: str) -> Optional[dict]:
    """The newest event that is progress: not a liveness/infrastructure tick, not session-repair's own comment or
    nudge wake. {id, at, kind}."""
    marks = ','.join('?' * len(NOISE))
    rows = conn.execute(f'SELECT id, kind, payload, created_at FROM task_events WHERE task_id = ? AND '
                        f"(kind NOT IN ({marks}) OR (kind = 'heartbeat' AND payload LIKE '%\"note\"%')) "
                        'ORDER BY id DESC LIMIT 50', (card, *NOISE)).fetchall()
    for r in rows:
        payload = _json(r[2]) or {}
        if r[1] == 'heartbeat' and not str(payload.get('note') or '').strip():
            continue
        if r[1] == 'unblocked' and payload.get('session_repair_nudge'):
            continue
        if r[1] == 'commented':
            body = conn.execute('SELECT body FROM task_comments WHERE task_id = ? AND id = ?',
                                (card, payload.get('comment_id'))).fetchone()
            if body and str(body[0] or '').startswith('[session-repair]'):
                continue
        return {'id': int(r[0]), 'at': _num(r[3]), 'kind': r[1]}
    return None


def _latest_event(conn, card: str) -> dict:
    r = conn.execute('SELECT id, kind, created_at FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1',
                     (card,)).fetchone()
    return {'id': int(r[0]), 'kind': r[1], 'at': r[2]} if r else {'id': 0, 'kind': None, 'at': None}


def _watch(conn, card: str, park_id: int) -> Optional[list]:
    """The current park's newest watcher observation as [state, detail]: a changed detail (queue position, job
    counts) is a producer that moved."""
    r = conn.execute("SELECT payload FROM task_events WHERE task_id = ? AND kind = 'external_watch' AND id > ? "
                     'ORDER BY id DESC LIMIT 1', (card, park_id)).fetchone()
    data = _json(r[0]) if r else None
    if not isinstance(data, dict):
        return None
    return [str(data.get('state') or ''), str(data.get('detail') or '')[:200]]


def observe_card(scan, src, card, now: float) -> dict:
    """One card's facts for the snapshot. `scan` is the scanner module (its predicates, never copies)."""
    c, t = card.conn, card.task
    ev = last_progress_event(c, card.id)
    obs: dict[str, Any] = {
        'board': card.board, 'status': card.status, 'title': card.title[:120],
        'run_id': getattr(t, 'current_run_id', None), 'event_id': ev['id'] if ev else 0,
        'event_at': ev['at'] if ev else None, 'event_kind': ev['kind'] if ev else None,
        'last_event': _latest_event(c, card.id), 'human': False, 'held': None, 'park': None, 'awaited': None,
        'awaited_status': None, 'awaited_done_at': None, 'awaited_observed': False, 'watch': None, 'healthy': None,
        'parents_open': None, 'parents_done_at': None, 'skip': False,
    }
    if card.status in ('blocked', 'triage'):
        obs['human'] = bool(scan._human_request(t)[1])
    try:
        from hermes_cli.kanban_spend_decision import execution_refusal, pending
        held = scan._promotion_held(c, card)
        if held == 'recovery_held':
            held = None  # a sticky block waits on someone: terminal only with a valid human_request (above)
        if not held and (pending(c, card.id) or execution_refusal(c, card.id)):
            held = 'spend_decision'
        obs['held'] = held
    except Exception as e:  # noqa: BLE001 -- unknown hold state: judge the card as unheld
        src.error(f'progress holds {card.board}/{card.id}', e)
    if card.status == 'running':
        at = obs['event_at']
        obs['healthy'] = 'running' if at is not None and now - at <= HEALTHY_RUN_S else None
    elif card.status in ('scheduled', 'blocked', 'todo', 'triage'):
        park = scan.current_park(card)
        if park:
            payload = park['payload'] or {}
            awaited, _wait = scan.wait_producer(card, park, now)
            raw = payload.get('awaiting') if isinstance(payload.get('awaiting'), dict) else {}
            obs['park'] = {'id': park['id'], 'at': _num(park['at']), 'kind': park['kind'],
                           'wake_at': _num(payload.get('wake_at')),
                           'probe': bool(str(payload.get('wake_check') or '').strip()),
                           'expected_s': _num(raw.get('expected_s')) or _num(payload.get('expected_s')),
                           'reason': str(payload.get('reason') or payload.get('error') or '')[:200],
                           'why': str(raw.get('why') or '')[:200]}
            if awaited:
                obs['awaited'] = {'kind': awaited.get('kind'), 'ref': str(awaited.get('ref') or '')[:300],
                                  'gateway': awaited.get('gateway')}
            obs['watch'] = _watch(c, card.id, park['id'])
            if awaited and awaited.get('kind') == 'card' and awaited.get('ref'):
                board, state = src.find_card(str(awaited['ref']), card.board)
                if state:
                    obs['awaited_status'] = state.get('status')
                    obs['awaited_done_at'] = _num(state.get('completed_at'))
                    obs['awaited_observed'] = state.get('source') == 'live' and board in {b for b, _ in src.boards()}
                    obs['awaited_board'] = board
            try:
                # Core's own fresh probe/watch receipt for this park (no network read: BS-long-wait owns those).
                if card.status == 'scheduled' and scan.healthy_future_park(card, park, now):
                    obs['healthy'] = 'producer'
            except Exception as e:  # noqa: BLE001
                src.error(f'progress producer {card.board}/{card.id}', e)
        if card.status == 'todo':
            try:
                from hermes_cli import kanban_db
                from hermes_cli import kanban_db_sync as sync
                states = dict(kanban_db.parent_states(c, card.id, remote=sync.has_remote_parents(c)))
                obs['parents_open'] = any(kanban_db.parent_blocks(c, pid, st) for pid, st in states.items()) if states else False
                if states and not obs['parents_open']:
                    times = [(scan._parent_state(c, p) or {}).get('completed_at') for p in states]
                    obs['parents_done_at'] = max((x for x in times if x is not None), default=None)
            except Exception as e:  # noqa: BLE001
                src.error(f'progress parents {card.board}/{card.id}', e)
    elif card.status == 'ready':
        try:
            from hermes_cli import kanban_dispatch_skip as kds
            obs['skip'] = bool(scan._skip_explains(kds.latest(c, card.id), now))
        except Exception as e:  # noqa: BLE001
            src.error(f'progress skip {card.board}/{card.id}', e)
    elif card.status == 'review':
        run = c.execute("SELECT 1 FROM task_runs WHERE task_id = ? AND status = 'running' LIMIT 1", (card.id,)).fetchone()
        obs['healthy'] = 'running' if run and obs['event_at'] and now - obs['event_at'] <= HEALTHY_RUN_S else None
    return obs


def state_key(obs: dict) -> list:
    """What must stay the same for a carried prediction to keep its deadline."""
    return [obs.get('status'), obs.get('run_id'), obs.get('event_id'), (obs.get('park') or {}).get('id'),
            obs.get('awaited_status'), obs.get('watch')]


# ── predictions ───────────────────────────────────────────────────────────────────────────────────────────────────
def _clock(obs: dict) -> bool:
    park, aw = obs.get('park') or {}, obs.get('awaited') or {}
    return aw.get('kind') == 'clock' or (not aw and park.get('wake_at') is not None and not park.get('probe'))


def predict(obs: dict, since: float, long_wait: dict) -> dict:
    """{expect, due_at, terminal?, dependent?, clock?}: what this card should show by `due_at` (None = never due).
    `since` is when the card's current state was first seen (a carried prediction keeps its deadline)."""
    st, park, aw = obs.get('status'), obs.get('park') or {}, obs.get('awaited') or {}
    if obs.get('human'):
        return {'expect': 'the person answers the ask (valid human_request)', 'due_at': None, 'terminal': 'human'}
    if obs.get('held'):
        return {'expect': f'core hold ({obs["held"]}) is released by its owner', 'due_at': None, 'terminal': 'held'}
    if st == 'triage':
        # A decision queue (specifier, spend reviewer, supervisor), not a worked card: its owner decides, on its own
        # cadence. Hub shadow pass 3 flagged 42 parked triage cards at +30 min; none was a stalled session.
        return {'expect': 'its triage owner decides (specify, release or archive)', 'due_at': None,
                'terminal': 'triage'}
    if st == 'running':
        return {'expect': 'a non-heartbeat event or status change', 'due_at': since + PREDICT_S}
    if st == 'ready':
        wait = READY_EXPLAINED_S if obs.get('skip') else PREDICT_S
        return {'expect': 'a worker claims it', 'due_at': since + wait}
    if st == 'todo':
        if obs.get('parents_open'):
            return {'expect': 'its parents finish (they carry their own predictions)', 'due_at': None, 'dependent': True}
        if obs.get('parents_done_at') is not None:
            return {'expect': 'promoted after its parents finished',
                    'due_at': obs['parents_done_at'] + AWAITED_DONE_GRACE_S}
    if park:
        kind, ref = aw.get('kind'), aw.get('ref')
        wake = park.get('wake_at')
        if kind == 'card':
            if obs.get('awaited_status') in SATISFIED and obs.get('awaited_done_at') is not None:
                return {'expect': f'the park wakes: awaited card {ref} is {obs["awaited_status"]}',
                        'due_at': max(obs['awaited_done_at'], park.get('at') or 0) + AWAITED_DONE_GRACE_S,
                        'wait': 'card'}
            if obs.get('awaited_observed'):
                return {'expect': f'awaited card {ref} makes progress (it carries its own prediction)',
                        'due_at': None, 'dependent': ref, 'wait': 'card'}
            return {'expect': f'awaited card {ref} (not readable here) finishes or the park re-parks',
                    'due_at': (wake + CLOCK_GRACE_S) if wake else since + long_wait.get('card', 4 * 3600),
                    'wait': 'card', 'defer': 'BS-long-wait'}
        if kind in ('ci', 'deploy'):
            expected = park.get('expected_s') or long_wait.get(kind, 2700)
            return {'expect': f'the {kind} run completes and the park wakes',
                    'due_at': (park.get('at') or since) + expected + CI_GRACE_S, 'wait': kind,
                    'defer': 'BS-long-wait'}
        if kind and QUEUE_RE.search(f'{ref} {aw.get("why") or ""} {park.get("why") or ""}'):
            return {'expect': 'merge-queue position decreases or it merges', 'due_at': since + PREDICT_S,
                    'wait': 'merge_queue'}
        if kind in long_wait:
            return {'expect': f'the {kind} producer {ref} delivers and the park wakes',
                    'due_at': (park.get('at') or since) + long_wait[kind], 'wait': kind,
                    'defer': 'BS-long-wait'}
        if _clock(obs) and wake is not None:
            return {'expect': 'the clock wake fires', 'due_at': wake + CLOCK_GRACE_S, 'clock': True}
        if wake is not None:
            return {'expect': 'the wake fires or the probe passes', 'due_at': wake + CLOCK_GRACE_S, 'wait': 'probe'}
    return {'expect': f'a status change or event on this {st or "open"} card', 'due_at': since + PREDICT_S}


def predict_process(proc: dict, since: float) -> dict:
    if proc.get('log'):
        return {'expect': f'the log of background process {proc.get("id")} advances', 'due_at': since + PREDICT_S}
    return {'expect': f'background process {proc.get("id")} exits or the chat takes a turn',
            'due_at': since + PREDICT_S}


# ── evaluation (pure) ─────────────────────────────────────────────────────────────────────────────────────────────
def evaluate(prev: Optional[dict], cur: dict, now: float, *, long_wait: dict,
             lookup_status: Callable[[str, str], Optional[str]] = lambda board, card: None) -> dict:
    """Compare the previous snapshot with this observation. Returns {verdict, signals, missed, snapshot}.

    cur: {chat, cards: {id: obs}, last_message_id, processes: {id: proc}}. A miss re-arms the card's prediction a
    full window later, so the second consecutive miss comes 30 min after the first, not one 15-min tick later.
    Any progress signal anywhere in the chat meets the session and resets every miss count.
    """
    chat = cur['chat']
    baseline = (prev is None or not isinstance(prev.get('cards'), dict)
                or now - (_num(prev.get('at')) or 0) > STALE_SNAPSHOT_S)
    pcards = {} if baseline else prev['cards']
    pprocs = {} if baseline else (prev.get('processes') or {})
    signals: list[dict] = []
    if not baseline:
        for cid, p in pcards.items():
            o = cur['cards'].get(cid)
            if o is None:
                st = lookup_status(p.get('board') or 'default', cid)
                if st and (st in SATISFIED or st != p.get('status')):
                    signals.append({'card': cid, 'signal': 'status', 'from': p.get('status'), 'to': st})
                continue
            if o['status'] != p.get('status'):
                signals.append({'card': cid, 'signal': 'status', 'from': p.get('status'), 'to': o['status']})
            elif (o.get('event_id') or 0) > (p.get('event_id') or 0):
                signals.append({'card': cid, 'signal': 'event', 'kind': o.get('event_kind'), 'event_id': o['event_id']})
            elif o.get('run_id') != p.get('run_id'):
                signals.append({'card': cid, 'signal': 'run', 'run_id': o.get('run_id')})
            elif o.get('awaited_status') != p.get('awaited_status'):
                signals.append({'card': cid, 'signal': 'producer', 'awaited_status': o.get('awaited_status')})
            elif o.get('watch') and p.get('watch') and o['watch'] != p['watch']:
                signals.append({'card': cid, 'signal': 'producer', 'watch': o['watch']})
        for cid in cur['cards']:
            if cid not in pcards:
                signals.append({'card': cid, 'signal': 'new_card'})
        if (cur.get('last_message_id') or 0) > (prev.get('last_message_id') or 0):
            signals.append({'signal': 'chat_turn', 'message_id': cur['last_message_id']})
        for pid, proc in (cur.get('processes') or {}).items():
            old = pprocs.get(pid)
            if old is None:
                signals.append({'process': pid, 'signal': 'new_process'})
            elif (proc.get('log_mtime') or 0) > (old.get('log_mtime') or 0):
                signals.append({'process': pid, 'signal': 'process_log'})
    progressed = bool(signals)
    cards_out, missed = {}, []
    for cid, o in cur['cards'].items():
        p = pcards.get(cid) or {}
        same = bool(p) and p.get('key') == state_key(o)
        since = (_num(p.get('since')) or now) if same else now
        misses = int(p.get('misses') or 0) if same and not progressed else 0
        rearm = (_num(p.get('rearm_at')) or 0) if same else 0
        pred = predict(o, since, long_wait)
        if pred.get('due_at') is not None:
            pred['due_at'] = max(pred['due_at'], rearm)
        if not baseline and not progressed and pred.get('due_at') is not None and pred['due_at'] <= now \
                and not pred.get('terminal'):
            if pred.get('defer') or o.get('healthy') == 'producer':
                # An overdue producer park is BS-long-wait's to judge (its probe/watch receipts, nudge cadence and
                # thresholds): it flags the stalled ones itself. A fresh park-bound producer receipt (core's
                # healthy_future_park) is a healthy wait. Neither is a miss here: label the chat only.
                pred['deferred'] = pred.get('defer') or 'healthy_producer'
            else:
                misses += 1
                rearm = now + PREDICT_S
                missed.append({'card': cid, 'board': o['board'], 'misses': misses, 'prediction': dict(pred),
                               'since': since})
                pred['due_at'] = rearm
        cards_out[cid] = {**o, 'key': state_key(o), 'since': since, 'misses': misses, 'rearm_at': rearm,
                          'prediction': pred}
    procs_out = {}
    process_only = not cur['cards'] and bool(cur.get('processes'))
    for pid, proc in (cur.get('processes') or {}).items():
        p = pprocs.get(pid) or {}
        same = bool(p) and (p.get('log_mtime') or 0) >= (proc.get('log_mtime') or 0)
        since = (_num(p.get('since')) or now) if same else now
        misses = int(p.get('misses') or 0) if same and not progressed else 0
        rearm = (_num(p.get('rearm_at')) or 0) if same else 0
        pred = predict_process(proc, since)
        pred['due_at'] = max(pred['due_at'], rearm)
        if process_only and not baseline and not progressed and pred['due_at'] <= now:
            misses += 1
            rearm = now + PREDICT_S
            missed.append({'process': pid, 'misses': misses, 'prediction': dict(pred), 'since': since, 'proc': proc})
            pred['due_at'] = rearm
        procs_out[pid] = {**proc, 'since': since, 'misses': misses, 'rearm_at': rearm, 'prediction': pred}
    if baseline:
        verdict = 'baseline'
    elif progressed:
        verdict = 'progress'
    elif missed:
        verdict = ('missed_twice' if any(m['misses'] >= 2 for m in missed)
                   else 'process_only' if process_only else 'missed_once')
    else:
        verdict = classify(cards_out, process_only)
    predictions = [{'card': cid, 'expect': c['prediction']['expect'], 'due_at': c['prediction'].get('due_at')}
                   for cid, c in cards_out.items()]
    predictions += [{'process': pid, 'expect': p['prediction']['expect'], 'due_at': p['prediction']['due_at']}
                    for pid, p in procs_out.items()]
    snapshot = {'version': VERSION, 'chat': chat, 'at': now, 'verdict': verdict,
                'last_message_id': cur.get('last_message_id'), 'cards': cards_out, 'processes': procs_out,
                'predictions': predictions, 'signals': signals[:20]}
    return {'verdict': verdict, 'signals': signals, 'missed': missed, 'snapshot': snapshot}


def classify(cards: dict, process_only: bool) -> str:
    """No progress and nothing due: the healthiest wait the chat has."""
    preds = [c['prediction'] for c in cards.values()]
    if cards and all(p.get('terminal') or p.get('dependent') for p in preds) and any(p.get('terminal') for p in preds):
        return 'valid_red'
    if any(c.get('healthy') == 'running' for c in cards.values()):
        return 'healthy_running'
    live = [c for c in cards.values() if not c['prediction'].get('deferred')]
    if process_only or any(c.get('healthy') == 'producer' for c in cards.values()) \
            or any(c['prediction'].get('wait') for c in live):
        return 'healthy_wait'
    if any(p.get('clock') for p in preds):
        return 'clock_not_due'
    if any(p.get('deferred') for p in preds):
        return 'long_wait'
    return 'pending'
