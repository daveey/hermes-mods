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


# Emoji-presentation symbols the Desktop fonts draw as tofu (a Hermes-bug title carried "⛔"). Plain text symbols
# such as ⚠ ✓ → stay: they render everywhere.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x231A, 0x231B), (0x23E9, 0x23F3), (0x23F8, 0x23FA),
    (0x25FD, 0x25FE), (0x2614, 0x2615), (0x2648, 0x2653), (0x267F, 0x267F),
    (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE),
    (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA),
    (0x26F2, 0x26F3), (0x26F5, 0x26F5), (0x26FA, 0x26FA), (0x26FD, 0x26FD),
    (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C),
    (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797),
    (0x27B0, 0x27B0), (0x27BF, 0x27BF), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50),
    (0x2B55, 0x2B55), (0xFE0F, 0xFE0F), (0x200D, 0x200D), (0x20E3, 0x20E3),
)


def clean(value):
    """Display text without emoji glyphs the fonts cannot draw, whitespace collapsed."""
    return flat(''.join(c for c in text(value) if not any(lo <= ord(c) <= hi for lo, hi in _EMOJI_RANGES)))


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


QUIET_S = 20 * 60  # a running card with no event this long reads "quiet" (Stuck takes over at STUCK_S)


def where_text(row):
    """Where the run is, in one short word: a worker name, hub, pool, or the owning gateway."""
    m = text(row.get('run_machine'))
    if m == 'worker-pool':
        return 'pool'
    return m or (text(row.get('gateway')) if row.get('gateway') not in (None, '', 'local') else '')


def model_text(row):
    """`class:deep` -> `deep`, `claude-opus-5-5` -> `opus-5-5`; '' when unknown."""
    return ', '.join(p for p in (re.sub(r'^(class:|claude-)', '', s.strip()) for s in text(row.get('run_model')).split(',')) if p)


def run_meta(row, state, progress, now):
    """Running/review row detail: `worker-a4 · opus-5-5 · 12m` (or `quiet 25m`), plus full-word details lines."""
    where, model = where_text(row), model_text(row)
    since = seconds(row.get('current_run_started_at')) or seconds(row.get('started_at'))
    quiet = bool(progress) and now - progress >= QUIET_S
    run_for = short_dur(now - since) if 0 < since <= now else ''
    last = (f'last event{" " + row["activity_kind"] if row.get("activity_kind") else ""} {ago(progress, now)}' if progress else 'last event unknown')
    head = ['in review'] if state == 'review' else []
    detail = ' · '.join(p for p in [*head, where, model, f'quiet {short_dur(now - progress)}' if quiet else run_for or (ago(progress, now) if progress else '')] if p) or 'running'
    details = [' · '.join(p for p in [f'card {row.get("id")}' if row.get('id') else '', f'assignee {row["assignee"]}' if row.get('assignee') else '',
                                      f'model {row["run_model"]}' if row.get('run_model') else '', f'on {where}' if where else '',
                                      f'board {row["board"]}' if row.get('board') not in (None, '', 'default') else ''] if p),
               ' · '.join(p for p in [f'started {ago(since, now)}' if 0 < since <= now else '', last] if p)]
    return {'detail': detail, 'details': [d for d in details if d], 'quiet': quiet}


def key(row):
    return ':'.join(str(row.get(k) or ('default' if k in ('profile', 'board') else ''))
                    for k in ('gateway', 'profile', 'board', 'id'))


def target(row):
    return {k: row.get(k) for k in ('id', 'gateway', 'profile', 'board')}


def row_view(row, kind='card'):
    return {'id': str(row.get('id') or ''), 'key': key(row), 'kind': kind,
            'gateway': row.get('gateway') or '', 'profile': row.get('profile') or 'default',
            'board': row.get('board') or 'default', 'title': clean(row.get('title')) or str(row.get('id') or 'task'),
            'detail': '', 'details': [], 'actions': [{'kind': 'open_card', 'label': 'Open card', 'target': target(row)}] if kind == 'card' else []}


SKEW_S = 120  # worker clocks run a little ahead of the hub's; core rejects `now < observed_at` outright
# Why a producer sent no usable execution evidence. The card is then described by its board status, never by a
# made-up liveness claim, but it is not "status unknown" either: nothing contradicts the board.
PRODUCER_GAPS = frozenset({'no_run_identity', 'producer_sent_no_observations', 'no_observation_for_card',
                           'hub_replica_not_source_evidence', 'missing_qualified_identity', 'no_observation'})


def _newest_observation(records):
    stamps = []
    for r in records:
        if isinstance(r, dict):
            stamps.append(r.get('observed_at'))
            stamps.extend(s.get('observed_at') for s in r.get('required_sources') or [] if isinstance(s, dict))
    return max([s for s in stamps if type(s) in (int, float) and 0 <= s < 8.64e12] or [0])


def observation(row, now, kind, projector=None):
    """Exact execution identity selection, same order as Desktop chipObservation.

    A worker clock a little ahead of the reading clock is not a contradiction: `now` is lifted to the newest
    observation when it is at most SKEW_S ahead (a larger lead stays core's `clock_inconsistent`).
    """
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
    newest = _newest_observation(candidates)
    if now < newest <= now + SKEW_S:
        now = newest
    if projector is None:
        try:
            from hermes_cli.execution_observations import select, identity_key
        except ImportError:
            return {'state': 'unknown', 'unknown_reason': 'sdk_projector_unavailable'}
        identities = {identity_key(r.get('identity')) for r in candidates}
        if len(identities) > 1:
            return {'state': 'unknown', 'unknown_reason': 'ambiguous_execution_identity', 'sourced': True}
        out: dict = dict(select(candidates, identity=candidates[0]['identity'], now=now), sourced=True)
        # A replica row carries no run id, so its identity is no execution identity: the producer gave no run
        # evidence. That is a gap in what was sent, not an invalid request by the reader.
        if out.get('unknown_reason') == 'invalid_requested_identity' and identities == {None}:
            out['unknown_reason'] = 'no_run_identity'
        return out
    return projector(candidates, now=now)


_URL = re.compile(r'https?://[^\s)\]]+')
_HASHY = re.compile(r'\b(?:core|mods)?(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b(?:…|\.\.\.)?')
WAIT_MAX = 100  # one short phrase; the full text is a Details line


def _pr_number(row, urls, on):
    """The PR a CI wait is about: the card's own pr_url, a /pull/N link in the wait, else the one PR its title names."""
    for blob in (text(row.get('pr_url')), *urls):
        m = re.search(r'/pull/(\d+)', blob)
        if m:
            return m[1]
    found = {g for m in re.finditer(r'\bPRs?\s*#?(\d+)|#(\d{3,6})\b', f'{on} {text(row.get("title"))}') for g in m.groups() if g}
    return found.pop() if len(found) == 1 else ''


def wait_info(row, now):
    """What a parked card waits on, as one short human phrase.

    Returns `{text, raw, urls}`: `text` for the row line, `raw` the producer's own wording (a Details line, only
    when it differs), `urls` for an Open action. Never inlines a URL, commit hash or run reference.
    """
    wait = obj(row.get('wait'))
    on = text(wait.get('waiting_on'))
    since = number(wait.get('waiting_since'))
    if not on:
        return {'text': '', 'raw': '', 'urls': []}
    match = re.match(r'the clock until (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC\b', on)
    if match:
        due = datetime.fromisoformat(f'{match[1]}T{match[2]}:00+00:00').timestamp()
        prefix = f'waiting {short_dur(now - since)} · ' if 0 < since <= now else ''
        if due >= now:
            return {'text': f'{prefix}{short_dur(due - now)} left (until {pacific(due)})', 'raw': on if on != match[0] else '', 'urls': []}
        return {'text': f'{prefix}{"⚠ " if now - due >= 1800 else ""}overdue by {short_dur(now - due)} (was due {pacific(due)})', 'raw': on if on != match[0] else '', 'urls': []}
    urls = [u.rstrip('.,;') for u in _URL.findall(on)]
    age = f' · {short_dur(now - since)}' if 0 < since <= now else ''
    head = re.match(r'(ci|deploy)\b', on, re.I)
    if head and head[1].lower() == 'ci':
        pr = _pr_number(row, urls, on)
        phrase = 'waiting on CI' + (f' · PR #{pr}' if pr else '')
    elif head:
        phrase = 'waiting on hub deploy' if re.search(r'\bhub\b', on, re.I) else 'waiting on deploy'
    elif re.match(r'(?:producer\s+)?card\s+t_[0-9a-f]+\b', on, re.I):
        phrase = 'waiting on parent card'
    elif re.search(r'\bt_[0-9a-f]+\b|\blocal:|\bPID\s*\d+|\S+\.service\b', on, re.I):
        phrase = 'waiting on external work' if re.match(r'external\b', on, re.I) else 'waiting on agent work'
    else:
        body = flat(_HASHY.sub('', _URL.sub('', on)))
        body = re.sub(r'\s+(?:for|and|/|·)\s*$', '', re.sub(r'\s{2,}', ' ', body)).strip()
        if len(body) > WAIT_MAX:
            body = body[:WAIT_MAX - 1].rstrip() + '…'
        phrase = f'waiting on {body}' if body else 'waiting'
    return {'text': phrase + age, 'raw': on, 'urls': urls[:1]}


def wait_target(row, bundle, detail):
    """Resolve the named dependency from already-hydrated evidence, never a new read."""
    refs = [obj(row.get(k)) for k in ('blocked_upstream', 'upstream')]
    refs += [{'id': cid} for cid in re.findall(r'\bt_[0-9a-f]+\b', text(obj(row.get('wait')).get('waiting_on')))]
    for ref in refs:
        cid = text(ref.get('id'))
        if not cid:
            continue
        rec = obj(obj(bundle.get('cards')).get(cid))
        hydration = obj(rec.get('detail'))
        task = obj(hydration.get('task'))
        linked = next((t for t in detail.get('link_tasks') or [] if isinstance(t, dict) and t.get('id') == cid), {})
        dep = {**linked, **task, **ref}
        if not dep.get('title'):
            continue
        dep['board'] = dep.get('board') or rec.get('board') or row.get('board') or 'default'
        owner = hydration.get('owner')
        dep['gateway'] = (dep.get('home_gateway') or dep.get('gateway') or
                          (owner if isinstance(owner, str) else None) or
                          (bundle.get('gateway') if hydration.get('source') == 'live' else None))
        dep['profile'] = dep.get('profile') or bundle.get('profile')
        if dep.get('gateway') and dep.get('profile'):
            return dep
    return None


def wait_phrase(row, now):
    return wait_info(row, now)['text']


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
               title=clean(title or request.get('title')) or 'The exact step isn’t shown here.',
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


def _matches(row, alias, same_gateway=()):
    """`same_gateway`: other names of the serving gateway (its `install:<id>` owner id). user-steps
    stamps local card refs with the install id while board rows carry the hub gateway name; without
    this the card and its own mirrored request both show as separate asks."""
    def gw(value):
        return 'self' if value and value in same_gateway else value
    return str(alias.get('id')) == str(row.get('id')) and all(
        bool(row.get(field)) and (gw(row[field]) if field == 'gateway' else row[field]) == (gw(alias.get(other)) if field == 'gateway' else alias.get(other))
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
        return 'waiting on parent card'
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


def route_model(row, detail):
    """The model a run was routed to, from the card's own events, when the run row carries none.

    `run_route` (what the dispatcher chose) beats `model_tier` (what the tier policy proposed); only the
    current run's events count, else the newest ones when the run id is unknown."""
    events = [e for e in detail.get('events') or [] if isinstance(e, dict) and e.get('kind') in ('run_route', 'model_tier')
              and text(obj(e.get('payload')).get('model'))]
    run = row.get('current_run_id')
    if run is not None:
        events = [e for e in events if e.get('run_id') == run]
    for kind in ('run_route', 'model_tier'):
        picked = [e for e in events if e['kind'] == kind]
        if picked:
            return text(obj(max(picked, key=lambda e: number(e.get('id')))['payload']).get('model'))
    return ''


def decision_text(request, now):
    """One status line for an answered ask whose delivery has not been confirmed: `Done · delivers when this turn ends`."""
    resolution = obj(request.get('resolution'))
    choice = next((c for c in request.get('choices') or [] if isinstance(c, dict) and c.get('id') == resolution.get('choiceId')), {})
    label = clean(re.sub(r'\s*\(recommended\)\s*$', '', text(choice.get('label')), flags=re.I)) or 'Answered'
    try:
        at = datetime.fromisoformat(text(resolution.get('recordedAt'))).timestamp()
    except ValueError:
        at = 0
    # A decision that sat undelivered for long is no longer "waiting for the turn to end": say so.
    return f'{label} · not delivered yet, recorded {ago(at, now)}' if at and now - at > RECORDED_STALE_S else f'{label} · delivers when this turn ends'


def recorded(request):
    """Answered but not yet delivered: the decision queue keeps the request open until delivery is confirmed."""
    return bool(obj(request.get('resolution'))) and request.get('state') in ('open', 'needs_context')


RECORDED_STALE_S = 900
HEADER_LABELS = {'unresolved': 'interrupted'}


def reduce_panel(bundle, *, now, requests=None, subagents=None, processes=None, rentals=None, projector=None):
    """Return JSON-safe PanelModel v1. Inputs are snapshots, and are never mutated."""
    sections = {s: [] for s in SECTIONS}
    rows_by_key = {}
    requests = list(requests if requests is not None else bundle.get('requests') or [])
    used, seen = set(), set()
    # The serving gateway's own names (board name + install owner id). Only these are equated, never a foreign one.
    same_gateway = frozenset(g for g in [bundle.get('gateway'), *(bundle.get('gateway_aliases') or [])] if g) if bundle.get('gateway') else frozenset()
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
        if row.get('status') in ('running', 'review') and not row.get('run_model'):
            row['run_model'] = route_model(row, detail)
        k = key(row)
        if k in seen:
            continue
        rows_by_key[k] = row
        state = row.get('status')
        if row.get('unfinished') and state not in ACTIVE:
            state = 'unknown'
        row['status'] = state
        if state in TERMINAL:
            continue
        seen.add(k)
        v = row_view(row)
        parent = obj(row.get('upstream'))
        if parent.get('id'):
            v['details'].append(f'Parent card: {parent["id"]} ({parent.get("status") or "status unknown"})')
        progress = number(row.get('activity_at')) or number(row.get('current_run_started_at'))
        wi = wait_info(row, now)
        dependency = wait_target(row, bundle, detail)
        dependency_text = ''
        if dependency:
            title = clean(dependency['title'])
            title = title if len(title) <= WAIT_MAX else title[:WAIT_MAX - 1].rstrip() + '…'
            since = number(obj(row.get('wait')).get('waiting_since'))
            age = f' · {short_dur(now - since)}' if 0 < since <= now else ''
            dependency_text = f'waiting on: {title}{age}'
            if not wi['text'].startswith(('waiting on CI', 'waiting on hub deploy', 'waiting on deploy')):
                wi['text'] = dependency_text
            v['actions'].append({'kind': 'open_card', 'label': 'Open parent card', 'target': target(dependency)})
        on = wi['text']
        if wi['raw'] and wi['raw'] != on:
            v['details'].append(f'Waiting on: {wi["raw"]}')
        v['actions'].extend({'kind': 'open_url', 'label': 'Open CI' if wi['text'].startswith('waiting on CI') else 'Open link', 'url': u} for u in wi['urls'])
        if state == 'unknown' and row.get('reported_status', raw.get('status')) in ('done', 'archived', 'done_pending_release'):
            v['detail'] = f'reported {row.get("reported_status") or "done"} by {row.get("gateway") or "another gateway"} · not confirmed by its owner'
            sections['waiting'].append(v)
            continue
        if row.get('stale'):
            v['detail'] = f'status unknown · last known: {state or "unknown"}; the card could not be re-read'
            sections['waiting'].append(v)
            continue
        matches = [(i, r) for i, r in enumerate(requests) if any(_matches(row, a, same_gateway) for a in _aliases(r))]
        if human_owned(row):
            live = next(((i, r) for i, r in matches if r.get('state') in ('open', 'needs_context') and not r.get('resolution')), None)
            if live:
                used.add(live[0]); sections['needs_you'].append(ask(row, live[1]))
            elif matches:
                # Retiring/Skip closes attention, not the blocked owner. No resurrected controls.
                used.update(i for i, _ in matches)
                answered = next((r for _, r in matches if recorded(r)), None)
                if answered:
                    # Answered, delivery pending: the card carries the one status line (`decision`), no ask card.
                    v['detail'] = 'blocked · answered'
                else:
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
            v['details'].append(f'Parent card: {upstream.get("id")} ({upstream.get("status") or "status unknown"})')
            if contained:
                v['details'].append(f'Release by card: {obj(contained.get("authority")).get("id") or "unknown"}')
            v['detail'] = dependency_text or ('waiting on contained parent card' if contained else 'waiting on blocked parent card')
            sections['waiting'].append(v); continue
        if look == 'stalled':
            v.update(reason='stalled', detail=f'stalled: {on or "agent-owned wait over 2 h"}')
            sections['stuck'].append(v); continue
        ev = observation(row, now, 'card', projector)
        unknown = ev.get('state') == 'unknown'
        reason = ev.get('unknown_reason') or 'unknown'
        v['evidence_state'] = ev.get('state')
        reported = bool(row.get('remote') or row.get('source') in ('replica', 'origin-obligation'))
        fresh = 0 <= now - read_at <= 60
        # The owner's board says it is running and the producer sent no run evidence at all (a replica row has no
        # run id): that is the owner's report, shown as such, not a contradiction and not "status unknown".
        synced_at = row.get('synced_at')
        fresh_sync = type(synced_at) in (int, float) and 0 <= now - synced_at <= 300
        if unknown and state == 'running' and reported and reason in PRODUCER_GAPS and fresh and fresh_sync:
            unknown = False
            v.update(evidence_state='reported', reported=True)
        provenance = (f'Reported by {row.get("gateway") or "another gateway"}\'s board; no live run evidence reached this gateway.'
                      if reported else 'Board status; no live run evidence for this card.')
        if unknown and state in ('scheduled', 'blocked', 'triage', 'review', 'ready', 'todo') and fresh:
            v['parked'] = True
            queued = state == 'todo' and not on and not obj(row.get('upstream')).get('id')
            v['detail'] = _ready(row, detail, now) if state == 'ready' else 'not started' if queued else _parked(row, on)
            v['details'].append(provenance)
            v['queued'] = queued
            sections['ready' if state == 'ready' or queued else 'waiting'].append(v); continue
        if unknown:
            v['detail'] = f'status unknown ({reason.replace("_", " ")}) · last known: {ev.get("last_known_state") or state or "unknown"}'
            sections['waiting'].append(v); continue
        if state == 'running' and progress and now - progress >= STUCK_S:
            v.update(reason='no-progress', last_at=progress, detail=f'running, no progress since {pacific(progress)} ({ago(progress, now)})' + (f' · last event: {row["activity_kind"]}' if row.get('activity_kind') else ''))
            sections['stuck'].append(v)
        elif state in ('running', 'review'):
            v.update(run_meta(row, state, progress, now))
            if v.get('reported'):
                v['details'].append(provenance)
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
            linked = [t for t in bundle.get('tasks') or [] if any(_matches({**t, 'gateway': t.get('gateway') or (bundle.get('gateway') if not t.get('remote') and t.get('source') != 'replica' else None), 'profile': t.get('profile') or bundle.get('profile'), 'board': t.get('board') or 'default'}, a, same_gateway) for a in aliases)]
            if linked and not any(human_owned(t) for t in linked):
                continue
            sections['needs_you'].append(ask({}, r))
    views = {x['key']: x for s in SECTIONS for x in sections[s]}
    for r in requests:
        if not recorded(r):
            continue
        # One status line next to the card the answer unblocks (or as its own row for a card-less ask), never a
        # second block: it disappears when the request resolves, i.e. once delivery is confirmed.
        info = {'request_id': r.get('requestId'), 'text': decision_text(r, now)}
        owner = next((views[k] for k, row in rows_by_key.items() if k in views and any(_matches(row, a, same_gateway) for a in _aliases(r))), None)
        if owner is not None and 'decision' not in owner:
            owner['decision'] = info
            if owner['detail'] == 'blocked · answered':
                owner['detail'] = ''
        elif owner is None:
            own = obj(r.get('owner'))
            v = row_view({'id': r.get('requestId'), 'title': r.get('title'), 'gateway': own.get('gatewayId'), 'profile': own.get('profile')}, 'decision')
            v['key'] = 'decision:' + str(r.get('requestId'))
            v['decision'] = info
            sections['waiting'].append(v)
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
        result = clean(row.get('result'))
        v['detail'] = result if len(result) <= 80 else result[:79].rstrip() + '…'
        sections['done'].append(v)
    outcome = obj(bundle.get('outcome_json') or bundle.get('outcome'))
    counts = {s: len(sections[s]) for s in SECTIONS}
    pips = [{'section': s, 'count': counts[s], 'label': {'needs_you': 'needs you', 'running': 'running'}.get(s, s)} for s in SECTIONS if counts[s]]
    state = outcome.get('state') or outcome.get('kind') or 'unknown'
    pending = any(x.get('decision') for s in SECTIONS for x in sections[s])
    # `state` stays the canonical outcome; `label` is what the header says. An answered ask is not "unresolved".
    label = ('decision recorded' if pending and state in ('unresolved', 'unknown', 'decision', 'needs_user', 'needs_input') and not counts['needs_you']
             else 'working' if state == 'unresolved' and counts['running'] else HEADER_LABELS.get(state, state))
    result = {'version': 1, 'header': {'state': state, 'label': label, 'counts': counts, 'pips': pips}, 'sections': sections,
              'diagnostics': list(bundle.get('input_errors') or [])}
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return result
