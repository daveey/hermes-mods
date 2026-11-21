"""PanelModel v1: pure presentation reducer. No stores, clocks, RPCs or authority writes.

The canonical outcome is consumed, never inferred from the section counts. Execution
observations use core's shared projector; the optional projector argument enables
core-free fixture tests without a second implementation of evidence semantics.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location('_panel_brief', Path(__file__).with_name('brief.py'))
assert _spec is not None and _spec.loader is not None
_brief = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_brief)
parse_brief = _brief.parse_brief

SECTIONS = ('needs_you', 'stuck', 'running', 'waiting', 'ready', 'done')
TERMINAL = {'done', 'archived', 'cancelled', 'canceled', 'resolved', 'withdrawn'}
ACTIVE = {'running', 'review', 'ready', 'todo', 'blocked', 'scheduled', 'triage'}
STUCK_S = 7200


def obj(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def text(value):
    return value if isinstance(value, str) else ''


def flat(value):
    return ' '.join(text(value).split())


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0


def seconds(value):
    n = number(value)
    return n / 1000 if n > 1e11 else n


def short_dur(value):
    n = max(0, int(value))
    if n < 60:
        return f'{n}s'
    m, h, d = n // 60, n // 3600, n // 86400
    if d:
        return f'{d}d' + (f' {h % 24}h' if h % 24 else '')
    if h:
        return f'{h}h' + (f' {m % 60}m' if m % 60 else '')
    return f'{m}m'


def ago(at, now):
    n = max(0, int(now - at))
    return 'just now' if n < 60 else f'{n // 60} min ago' if n < 3600 else f'{n // 3600} h ago' if n < 86400 else f'{n // 86400} d ago'


def pacific(at):
    return datetime.fromtimestamp(at, ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M %Z')


def key(row):
    return ':'.join(str(row.get(k) or ('default' if k in ('profile', 'board') else ''))
                    for k in ('gateway', 'profile', 'board', 'id'))


def target(row):
    return {k: row.get(k) for k in ('id', 'gateway', 'profile', 'board')}


def row_view(row, kind='card'):
    return {'id': str(row.get('id') or ''), 'key': key(row), 'kind': kind,
            'gateway': row.get('gateway') or '', 'profile': row.get('profile') or 'default',
            'board': row.get('board') or 'default', 'title': text(row.get('title')) or str(row.get('id') or 'task'),
            'detail': '', 'details': [], 'actions': [{'kind': 'open_card', 'label': 'Open card', 'target': target(row)}] if kind == 'card' else []}


def observation(row, now, kind, projector=None):
    """Exact execution identity selection, same order as Desktop chipObservation."""
    records = row.get('execution_observations')
    if not isinstance(records, list):
        return {'state': 'unknown', 'unknown_reason': 'hub_replica_not_source_evidence' if row.get('replica') or row.get('source') == 'replica' else 'producer_sent_no_observations'}
    candidates = []
    for r in records:
        identity = obj(r.get('identity')) if isinstance(r, dict) else {}
        if identity.get('kind') != kind or identity.get('work_id') != row.get('work_id', row.get('id')):
            continue
        if any(row.get(k) and k in identity and row[k] != identity[k] for k in ('gateway', 'profile', 'board')):
            continue
        candidates.append(r)
    if not candidates:
        return {'state': 'unknown', 'unknown_reason': 'no_observation_for_card'}
    if projector is None:
        try:
            from hermes_cli.execution_observations import select, identity_key
        except ImportError:
            return {'state': 'unknown', 'unknown_reason': 'sdk_projector_unavailable'}
        identities = {identity_key(r.get('identity')) for r in candidates}
        if len(identities) > 1:
            return {'state': 'unknown', 'unknown_reason': 'ambiguous_execution_identity', 'sourced': True}
        return dict(select(candidates, identity=candidates[0]['identity'], now=now), sourced=True)
    return projector(candidates, now=now)


def wait_phrase(row, now):
    wait = obj(row.get('wait'))
    on = text(wait.get('waiting_on'))
    since = number(wait.get('waiting_since'))
    if not on:
        return ''
    match = re.match(r'the clock until (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC\b', on)
    if match:
        due = datetime.fromisoformat(f'{match[1]}T{match[2]}:00+00:00').timestamp()
        prefix = f'waiting {short_dur(now - since)} · ' if 0 < since <= now else ''
        if due >= now:
            return f'{prefix}{short_dur(due - now)} left (until {pacific(due)})'
        return f'{prefix}{"⚠ " if now - due >= 1800 else ""}overdue by {short_dur(now - due)} (was due {pacific(due)})'
    return f'waiting on {on}' + (f' for {short_dur(now - since)}' if since > 0 else '')


def human_owned(row):
    """block_kind survives old blocks. It alone is NEVER evidence of a current ask."""
    state = row.get('status')
    if state in TERMINAL or row.get('stale'):
        return False
    wait = obj(row.get('wait'))
    if wait.get('wait_state'):
        return wait['wait_state'] == 'human'
    hr = obj(row.get('human_request'))
    if state == 'scheduled':
        return bool(obj(row.get('wakes')).get('blocks_for_human') and hr.get('action'))
    try:
        from hermes_cli.kanban_open_asks import card_is_open_human_ask
    except ImportError:
        # Portable/core-free fixtures retain the narrow validated shape. Production
        # always consumes core's predicate, never a second block-kind authority.
        return state in ('blocked', 'triage') and bool(hr.get('action') and hr.get('why_human') and hr.get('interaction')) and row.get('block_kind') not in ('dependency', 'time', 'transient', 'assignment')
    return card_is_open_human_ask(state, row.get('block_kind'), hr)


def ask(row, request=None):
    request = request or {}
    hr = obj(row.get('human_request'))
    scope = obj(request.get('scope'))
    if not hr:
        markdown = text(scope.get('markdown'))
        why = re.search(r'(?:^|\n)(Tried:[\s\S]*?)(?=\n\s*\n|\n\s*Situation:|$)', markdown, re.I)
        hr = {'action': request.get('title'), 'scope': markdown, 'choices': request.get('choices') or [],
              'why_human': why[1] if why else ''}
    # Canonical choices control ordering/IDs; descriptions remain joined by their OWN letters.
    if request.get('choices'):
        hr = {**hr, 'choices': request['choices']}
    parsed = parse_brief(hr)
    out = row_view(row, 'card' if row.get('id') else 'question')
    if not row.get('id'):
        out['key'] = 'question:' + ':'.join(str(obj(request.get('owner')).get(k) or '') for k in ('gatewayId', 'profile', 'storedSessionId')) + ':' + str(request.get('requestId') or '')
    action = text(hr.get('action'))
    title = re.split(r'\b(?:unblock|yes)\s*(?:\([^)]*\))?\s*=', action, maxsplit=1, flags=re.I)[0].strip()
    out.update(source='card' if row.get('id') else 'question', id=row.get('id') or request.get('requestId'),
               title=title or request.get('title') or 'The exact step isn’t shown here.',
               situation=parsed['situation'], only_you=parsed['only_you'], options=[],
               details=[s for s in (parsed.get('tried'), text(hr.get('evidence')), text(hr.get('scope'))) if s])
    choices = request.get('choices') or hr.get('choices') or []
    for i, option in enumerate(parsed['options']):
        choice = choices[i] if i < len(choices) else {}
        recommended = option['letter'] == parsed['recommend_letter']
        out['options'].append({**option, 'recommended': recommended,
                               'reason': parsed['reason'] if recommended else '',
                               'choice_id': choice.get('id') if request else None})
    out['recommend_letter'], out['reason'] = parsed['recommend_letter'], parsed['reason']
    out['request_id'] = request.get('requestId')
    out['request_revision'] = request.get('revision')
    if request:
        # The Desktop no longer renders a second full request. Preserve every
        # consent boundary in the single model, including card-backed requests
        # whose human_request.scope differs from the canonical plan.
        full_scope = text(scope.get('markdown'))
        if full_scope and full_scope not in out['details']:
            out['details'].append(full_scope)
        for limit in request.get('limits') or []:
            limit = obj(limit)
            out['details'].append('Limit: ' + text(limit.get('key')) + ': ' + str(limit.get('value', '')) +
                                  ' · permissionRequired: ' + json.dumps(limit.get('permissionRequired'), ensure_ascii=False))
        for exclusion in request.get('exclusions') or []:
            out['details'].append('Not included: ' + text(exclusion))
        for effect, outcome in obj(request.get('outcomes')).items():
            if text(outcome):
                out['details'].append(str(effect) + ': ' + text(outcome))
        for choice in choices:
            if text(obj(choice).get('explain')):
                out['details'].append(text(choice.get('label')) + ': ' + text(choice.get('explain')))
        out['gateway'] = obj(request.get('owner')).get('gatewayId') or out['gateway']
        out['actions'].append({'kind': 'decide', 'label': 'Answer', 'request_id': request['requestId'],
                               'revision': request.get('revision'), 'owner': request.get('owner')})
    return out


def _aliases(request):
    return [a for a in [*(request.get('aliases') or []), *(obj(request.get('scope')).get('sourceRefs') or [])]
            if isinstance(a, dict) and a.get('type') == 'kanban']


def _matches(row, alias):
    return str(alias.get('id')) == str(row.get('id')) and all(
        bool(row.get(field)) and row[field] == alias.get(other)
        for field, other in (('gateway', 'gatewayId'), ('profile', 'profile'), ('board', 'board')))


def _parked(row, on):
    state = row.get('status')
    up = obj(row.get('upstream'))
    unpublished = not on or 'did not publish' in on
    if not unpublished and not (state == 'triage' and obj(row.get('wait')).get('waiting_on') == 'triage'):
        return on
    if state == 'triage':
        return 'in triage · waiting to be specified and promoted'
    if state == 'review':
        return 'waiting for review'
    if up.get('id'):
        return f'{state} · waiting on card {up["id"]} ({up.get("status") or "status unknown"})'
    return {'todo': 'waiting on its parent cards', 'scheduled': 'scheduled · its owner has not published what wakes it',
            'blocked': 'blocked · no reason published'}.get(state, state or 'status unknown')


def _ready(row, detail, now):
    # The board publishes the admission cause. Never replace lane/quota holds with capacity wording.
    note = flat(detail.get('queue_note') or detail.get('capacity_note') or row.get('queue_reason'))
    if row.get('block_kind') == 'lane_gate' or 'lane_gate' in note:
        return 'ready · Waiting for admission to lane'
    if note:
        return f'ready · {note}'
    since = seconds(row.get('ready_since'))
    return f'ready · waiting{f" {short_dur(now - since)}" if since else ""} for a worker to claim it'


def reduce_panel(bundle, *, now, requests=None, subagents=None, processes=None, rentals=None, projector=None):
    """Return JSON-safe PanelModel v1. Inputs are snapshots, and are never mutated."""
    sections = {s: [] for s in SECTIONS}
    requests = list(requests if requests is not None else bundle.get('requests') or [])
    used, seen = set(), set()
    read_at = number(bundle.get('computed_at')) or now
    for raw in bundle.get('tasks') or []:
        if not isinstance(raw, dict) or not raw.get('id'):
            continue
        row = dict(raw)
        rec = obj(obj(bundle.get('cards')).get(str(row['id'])))
        # Only serving-gateway rows inherit serving coordinates. Replica/unknown
        # ownership is never repaired into local decision authority.
        if not row.get('remote') and row.get('source') != 'replica' and row.get('ownership_state') != 'unknown':
            for field in ('gateway', 'profile'):
                if not row.get(field) and bundle.get(field):
                    row[field] = bundle[field]
        row.setdefault('board', 'default')
        detail = obj(rec.get('detail'))
        # Detail is supplementary only. Current list/wait wins over preserved block_kind and old ask text.
        row = {**obj(detail.get('task')), **row}
        k = key(row)
        if k in seen:
            continue
        state = row.get('status')
        if row.get('unfinished') and state not in ACTIVE:
            state = 'unknown'
        row['status'] = state
        if state in TERMINAL:
            continue
        seen.add(k)
        v = row_view(row)
        progress = number(row.get('activity_at')) or number(row.get('current_run_started_at'))
        on = wait_phrase(row, now)
        if state == 'unknown' and row.get('reported_status', raw.get('status')) in ('done', 'archived', 'done_pending_release'):
            v['detail'] = f'reported {row.get("reported_status") or "done"} by {row.get("gateway") or "another gateway"} · not confirmed by its owner'
            sections['waiting'].append(v)
            continue
        if row.get('stale'):
            v['detail'] = f'status unknown · last known: {state or "unknown"}; the card could not be re-read'
            sections['waiting'].append(v)
            continue
        matches = [(i, r) for i, r in enumerate(requests) if any(_matches(row, a) for a in _aliases(r))]
        if human_owned(row):
            live = next(((i, r) for i, r in matches if r.get('state') in ('open', 'needs_context') and not r.get('resolution')), None)
            if live:
                used.add(live[0]); sections['needs_you'].append(ask(row, live[1]))
            elif matches:
                # Retiring/Skip closes attention, not the blocked owner. No resurrected controls.
                used.update(i for i, _ in matches)
                v['detail'] = f'blocked · request {matches[0][1].get("state") or "dismissed"}'
                sections['waiting'].append(v)
            else:
                sections['needs_you'].append(ask(row))
            continue
        wait = obj(row.get('wait'))
        look = wait.get('wait_state')
        upstream = obj(row.get('blocked_upstream'))
        if upstream and look != 'stalled':
            contained = obj(upstream.get('contained'))
            v['detail'] = (f'waiting on contained card {upstream.get("id")} · release by card {obj(contained.get("authority")).get("id") or "unknown"}'
                           if contained else f'waiting on blocked parent card {upstream.get("id")} ({upstream.get("status")})')
            sections['waiting'].append(v); continue
        if look == 'stalled':
            v.update(reason='stalled', detail=f'stalled: {on or "agent-owned wait over 2 h"}')
            sections['stuck'].append(v); continue
        ev = observation(row, now, 'card', projector)
        unknown = ev.get('state') == 'unknown'
        v['evidence_state'] = ev.get('state')
        if unknown and state in ('scheduled', 'blocked', 'triage', 'review', 'ready', 'todo') and 0 <= now - read_at <= 60:
            v['parked'] = True
            source = 'reported board status' if row.get('remote') or row.get('source') == 'replica' else 'board status'
            queued = state == 'todo' and not on and not obj(row.get('upstream')).get('id')
            v['detail'] = (_ready(row, detail, now) if state == 'ready' else 'not started' if queued else _parked(row, on)) + f' · {source}'
            v['queued'] = queued
            sections['ready' if state == 'ready' or queued else 'waiting'].append(v); continue
        if unknown:
            reason = ev.get('unknown_reason') or 'unknown'
            v['detail'] = f'status unknown ({reason.replace("_", " ")}) · last known: {ev.get("last_known_state") or state or "unknown"}'
            sections['waiting'].append(v); continue
        if state == 'running' and progress and now - progress >= STUCK_S:
            v.update(reason='no-progress', last_at=progress, detail=f'running, no progress since {pacific(progress)} ({ago(progress, now)})' + (f' · last event: {row["activity_kind"]}' if row.get('activity_kind') else ''))
            sections['stuck'].append(v)
        elif state in ('running', 'review'):
            v['detail'] = ('in review' if state == 'review' else 'running' + (f' · {row["assignee"]}' if row.get('assignee') else '')) + (f' · {ago(progress, now)}' if progress else ' · last event unknown')
            sections['running'].append(v)
        elif state in ('ready', 'todo'):
            v.update(detail=_ready(row, detail, now) if state == 'ready' else 'not started', queued=state == 'todo')
            sections['ready'].append(v)
        else:
            v['detail'] = on or state or 'status unknown'
            sections['waiting'].append(v)
    for i, r in enumerate(requests):
        if i not in used and r.get('state') in ('open', 'needs_context') and not r.get('resolution'):
            # A linked card no longer waiting for a person cannot revive an obsolete mirror ask.
            aliases = _aliases(r)
            linked = [t for t in bundle.get('tasks') or [] if any(_matches(t, a) for a in aliases)]
            if linked and not any(human_owned(t) for t in linked):
                continue
            sections['needs_you'].append(ask({}, r))
    for kind, rows in (('subagent', subagents if subagents is not None else bundle.get('subagents') or []),
                       ('process', processes if processes is not None else bundle.get('processes') or []),
                       ('machine', rentals if rentals is not None else bundle.get('rentals') or [])):
        for raw in rows:
            row = dict(raw)
            row['id'] = row.get('id') or row.get('subagent_id') or row.get('session_id')
            row['title'] = row.get('title') or row.get('goal') or row.get('command') or row.get('name') or row['id']
            v = row_view(row, kind)
            v['key'] = kind + ':' + v['key']
            if v['key'] in seen:
                continue
            seen.add(v['key'])
            ev = observation(row, now, kind, projector) if kind != 'machine' else {'state': 'running'}
            v.update(evidence_state=ev.get('state'), detail='rented machine' if kind == 'machine' else ev.get('state', 'unknown').replace('_', ' ') + (f' ({ev["unknown_reason"].replace("_", " ")})' if ev.get('unknown_reason') else ''))
            sections['running' if ev.get('state') == 'running' else 'waiting'].append(v)
    done_seen = set()
    for row in sorted(bundle.get('done') or [], key=lambda r: number(r.get('completed_at')), reverse=True):
        if row.get('status') not in ('done', 'archived') or row.get('unfinished') or row.get('stale'):
            continue
        k = key(row)
        if k in done_seen or k in seen:
            continue
        done_seen.add(k)
        v = row_view(row)
        v['detail'] = row.get('result') or row['status']
        sections['done'].append(v)
    outcome = obj(bundle.get('outcome_json') or bundle.get('outcome'))
    counts = {s: len(sections[s]) for s in SECTIONS}
    pips = [{'section': s, 'count': counts[s], 'label': {'needs_you': 'needs you', 'running': 'running'}.get(s, s)} for s in SECTIONS if counts[s]]
    result = {'version': 1, 'header': {'state': outcome.get('state') or outcome.get('kind') or 'unknown', 'counts': counts, 'pips': pips}, 'sections': sections,
              'diagnostics': list(bundle.get('input_errors') or [])}
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return result
