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
_spec_time = importlib.util.spec_from_file_location('_panel_relative_text', Path(__file__).with_name('relative_text.py'))
assert _spec_time is not None and _spec_time.loader is not None
_relative = importlib.util.module_from_spec(_spec_time)
_spec_time.loader.exec_module(_relative)
relative = _relative.relative
_spec_tree = importlib.util.spec_from_file_location('_panel_hierarchy', Path(__file__).with_name('hierarchy.py'))
assert _spec_tree is not None and _spec_tree.loader is not None
_hierarchy = importlib.util.module_from_spec(_spec_tree)
_spec_tree.loader.exec_module(_hierarchy)
_spec_action = importlib.util.spec_from_file_location('_panel_actionability', Path(__file__).with_name('actionability.py'))
assert _spec_action is not None and _spec_action.loader is not None
_actionability = importlib.util.module_from_spec(_spec_action)
_spec_action.loader.exec_module(_actionability)

SECTIONS = ('needs_you', 'stuck', 'running', 'waiting', 'ready', 'done')
TERMINAL = {'done', 'archived', 'cancelled', 'canceled', 'resolved', 'withdrawn'}
ACTIVE = {'running', 'review', 'ready', 'todo', 'blocked', 'scheduled', 'triage'}
STUCK_S = 7200
# A quiet session's board bundle is fully re-read only every QUIET_FULL_EVERY_S (300 s, dashboard/feed.py); card
# events recompute it at once. Within that cadence plus slack an older snapshot is still the current parked state, so a
# delivery wait (CI, deploy, clock) keeps its light-blue colour; past it the colour drops to the uncoloured default.
HISTORICAL_DELIVERY_S = 360
DELIVERY_KINDS = frozenset({'ci', 'deploy', 'clock', 'card'})  # light-blue/purple waits; 'agent' still drops when aged


def obj(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def published_status(outcome):
    """Validate the backend wire envelope; never derive a replacement from rows or legacy kind."""
    status = outcome.get('status')
    def wire_integer(value):
        # JSON has one number type: match Number.isSafeInteger after parsing in Desktop.
        return type(value) in (int, float) and 0 <= value <= 9007199254740991 and value == int(value)

    if not isinstance(status, dict) or not wire_integer(status.get('status_version')) or status['status_version'] != 1:
        return None
    for key in ('revision', 'generation'):
        if not wire_integer(status.get(key)):
            return None
    if not all(isinstance(status.get(k), dict) for k in ('identity', 'coverage', 'presentation')):
        return None
    identity, coverage, paint = (status[k] for k in ('identity', 'coverage', 'presentation'))
    if not all(isinstance(identity.get(k), str) and identity[k] for k in ('gateway', 'profile', 'session_id', 'owner_epoch')):
        return None
    if coverage.get('state') not in ('complete', 'unknown') or paint.get('stripe') not in ('attention', 'blocked', 'done', 'none', 'stuck', 'waiting'):
        return None
    for value in (coverage.get('reasons'), paint.get('reason_codes')):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None
    if status.get('kind') not in ('decision', 'done', 'muted', 'running', 'unresolved', 'waiting'):
        return None
    if paint['stripe'] == 'done' and (coverage['state'] != 'complete' or status['kind'] != 'done'):
        return None
    return status


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


def failures_text(row):
    """`3 failures` when the card's failed-run count is a positive integer, else '' (missing/unreadable shows nothing)."""
    n = row.get('failed_runs')
    if type(n) is not int or n <= 0:
        return ''
    return f'{n} failure' + ('' if n == 1 else 's')


def money(usd):
    return f'${usd:.2f}' if usd < 100 else f'${usd:,.0f}'


def tokens_text(n):
    return f'{n / 1e9:.1f}B tokens' if n >= 1e9 else f'{n / 1e6:.1f}M tokens' if n >= 1e6 else f'{n // 1000}k tokens' if n >= 1000 else f'{n} tokens'


def done_meta(row):
    """Done row: `42m · 2 restarts · $3.10` (how long its runs took, restarts, dollars), or None without stats.

    Dollars are the worker-reported cost, else the run's tokens at its model's API list price; `~` marks a sum
    missing runs it could not price (a class-routed run records no concrete model). With no priced run at all
    the token count stands in, never a made-up dollar figure."""
    s = obj(row.get('run_stats'))
    whole = lambda v: v if type(v) is int and v > 0 else 0
    runs, secs, priced, reported, tokens = (whole(s.get(k)) for k in ('runs', 'run_seconds', 'priced_runs', 'usage_runs', 'tokens'))
    usd = s.get('cost_usd')
    usd = float(usd) if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd > 0 else 0.0
    if not runs:
        return None
    restarts = runs - 1
    cost = ('~' if priced < reported else '') + money(usd) if usd and priced else tokens_text(tokens) if tokens else ''
    detail = ' · '.join(p for p in [short_dur(secs) if secs else '',
                                    f'{restarts} restart' + ('' if restarts == 1 else 's') if restarts else '', cost] if p)
    spend = (f'{money(usd)} for {priced} of {reported} runs with usage (reported cost, else tokens at API list price)'
             if usd and priced else 'cost unknown')
    line = ' · '.join(p for p in [f'{runs} run' + ('' if runs == 1 else 's'), f'run time {short_dur(secs)}' if secs else '',
                                  tokens_text(tokens) if tokens else '', spend] if p)
    return {'detail': detail, 'details': [line]}


def run_meta(row, state, progress, now, *, wire=False):
    """Running/review row detail: `worker-a4 · opus-5-5 · 12m · 2 failures` (or `quiet 25m`), plus full-word details lines."""
    where, model = where_text(row), model_text(row)
    since = seconds(row.get('current_run_started_at')) or seconds(row.get('started_at'))
    quiet = bool(progress) and now - progress >= QUIET_S
    age = lambda at: relative(at, 'ago') if wire else ago(at, now)
    run_for = (relative(since, 'elapsed') if wire else short_dur(now - since)) if 0 < since <= now else ''
    last = (f'last event{" " + row["activity_kind"] if row.get("activity_kind") else ""} {age(progress)}' if progress else 'last event unknown')
    head = ['in review'] if state == 'review' else []
    detail = ' · '.join(p for p in [*head, where, model, f'quiet {relative(progress, "elapsed") if wire else short_dur(now - progress)}' if quiet else run_for or (age(progress) if progress else ''), failures_text(row)] if p) or 'running'
    details = [' · '.join(p for p in [f'card {row.get("id")}' if row.get('id') else '', f'assignee {row["assignee"]}' if row.get('assignee') else '',
                                      f'model {row["run_model"]}' if row.get('run_model') else '', f'on {where}' if where else '',
                                      f'board {row["board"]}' if row.get('board') not in (None, '', 'default') else ''] if p),
               ' · '.join(p for p in [f'started {age(since)}' if 0 < since <= now else '', last] if p)]
    return {'detail': detail, 'details': [d for d in details if d], 'quiet': quiet}


def key(row):
    return ':'.join(str(row.get(k) or ('default' if k in ('profile', 'board') else ''))
                    for k in ('gateway', 'profile', 'board', 'id'))


def target(row):
    return {k: row.get(k) for k in ('id', 'gateway', 'profile', 'board')}


def session_target(row):
    """The chat that owns a card (its `session_id`), on the card's own gateway/profile; None when unknown."""
    sid = row.get('session_id')
    if not isinstance(sid, str) or not sid.strip():
        return None
    return {'session': sid.strip(), 'gateway': row.get('gateway') or '', 'profile': row.get('profile') or 'default'}


def row_view(row, kind='card'):
    actions = []
    if kind == 'card':
        actions.append({'kind': 'open_card', 'label': 'Open card', 'target': target(row)})
        session = session_target(row)
        if session:
            actions.append({'kind': 'open_session', 'label': 'Open session', 'target': session})
    return {'id': str(row.get('id') or ''), 'key': key(row), 'kind': kind,
            'gateway': row.get('gateway') or '', 'profile': row.get('profile') or 'default',
            'board': row.get('board') or 'default', 'title': clean(row.get('title')) or str(row.get('id') or 'task'),
            'detail': '', 'details': [], 'actions': actions}


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


def wait_info(row, now, *, wire=False):
    """What a parked card waits on, as one short human phrase.

    Returns `{text, raw, urls}`: `text` for the row line, `raw` the producer's own wording (a Details line, only
    when it differs), `urls` for an Open action. Never inlines a URL, commit hash or run reference.
    """
    wait = obj(row.get('wait'))
    eta = {k: wait.get(k) for k in ('waited_since', 'expected_s', 'estimate_source')}
    on = text(wait.get('waiting_on'))
    since = number(wait.get('waiting_since'))
    if not on:
        return {'text': '', 'raw': '', 'urls': []}
    match = re.match(r'the clock until (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC\b', on)
    if match:
        due = datetime.fromisoformat(f'{match[1]}T{match[2]}:00+00:00').timestamp()
        prefix = f'waiting {relative(since, "elapsed") if wire else short_dur(now - since)} · ' if 0 < since <= now else ''
        if due >= now:
            return {'text': f'{prefix}{relative(due, "remaining") if wire else short_dur(due - now)} left (until {pacific(due)})', 'raw': on if on != match[0] else '', 'urls': [], 'kind': 'clock', 'eta': eta}
        return {'text': f'{prefix}{"⚠ " if now - due >= 1800 else ""}overdue by {relative(due, "elapsed") if wire else short_dur(now - due)} (was due {pacific(due)})', 'raw': on if on != match[0] else '', 'urls': [], 'kind': 'clock', 'eta': eta}
    urls = [u.rstrip('.,;') for u in _URL.findall(on)]
    age = f' · {relative(since, "elapsed") if wire else short_dur(now - since)}' if 0 < since <= now else ''
    head = re.match(r'(ci|deploy)\b', on, re.I)
    kind = head[1].lower() if head else ''
    if head and head[1].lower() == 'ci':
        pr = _pr_number(row, urls, on)
        phrase = 'waiting on CI' + (f' · PR #{pr}' if pr else '')
    elif head:
        phrase = 'waiting on hub deploy' if re.search(r'\bhub\b', on, re.I) else 'waiting on deploy'
    elif primary := re.match(r'(?:producer\s+)?card\s+(t_[0-9a-f]+)\b', on, re.I):
        phrase = 'waiting on an update' if primary[1] == row.get('id') else 'waiting on parent card'
    elif re.search(r'\bt_[0-9a-f]+\b|\blocal:|\bPID\s*\d+|\S+\.service\b', on, re.I):
        phrase = 'waiting on external work' if re.match(r'external\b', on, re.I) else 'waiting on agent work'
    else:
        body = flat(_HASHY.sub('', _URL.sub('', on)))
        body = re.sub(r'\s+(?:for|and|/|·)\s*$', '', re.sub(r'\s{2,}', ' ', body)).strip()
        if len(body) > WAIT_MAX:
            body = body[:WAIT_MAX - 1].rstrip() + '…'
        phrase = f'waiting on {body}' if body else 'waiting'
    return {'text': phrase + age, 'raw': on, 'urls': urls[:1], **({'kind': kind, 'eta': eta} if kind else {})}


def wait_target(row, bundle, detail):
    """Resolve the named dependency from already-hydrated evidence, never a new read."""
    if row.get('status') not in ('scheduled', 'todo', 'blocked', 'triage'):
        return None
    on = text(obj(row.get('wait')).get('waiting_on'))
    # A clock, CI, deploy or release-authority annotation is not a parent wait.
    if on and not (re.match(r'(?:producer\s+)?card\b', on, re.I) or
                   re.match(r'external\b', on, re.I) or 'did not publish' in on):
        return None
    named = re.findall(r'\bt_[0-9a-f]+\b', on)
    graph_refs = [obj(row.get(k)) for k in ('blocked_upstream', 'upstream') if obj(row.get(k)).get('id')]
    if named:
        # The published park owns its primary identity, including self-probes.
        # Graph ancestry is independent of that park and must never replace it.
        if named[0] == row.get('id'):
            return None
        refs = [next((ref for ref in graph_refs if ref.get('id') == named[0]), {'id': named[0]})]
    else:
        # Only absent/unpublished or explicitly parent-only waits use ancestry.
        if on and not ('did not publish' in on or re.fullmatch(r'(?:producer\s+)?card(?:\s+parent)?', on, re.I)):
            return None
        refs = graph_refs
    for ref in refs[:1]:
        cid = text(ref.get('id'))
        if not cid or cid == row.get('id'):
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


def wait_parents(dep, bundle):
    """Hydrated next hops shared by the wait label and outline; no source reads."""
    hydration = obj(obj(obj(bundle.get('cards')).get(dep['id'])).get('detail'))
    if text(obj(dep.get('wait')).get('waiting_on')):
        primary = wait_target(dep, bundle, hydration)
        return [primary] if primary else []
    parents = obj(hydration.get('links')).get('parents') or []
    if not parents:
        primary = wait_target(dep, bundle, hydration)
        return [primary] if primary else []
    linked = {t.get('id'): t for t in hydration.get('link_tasks') or [] if isinstance(t, dict)}
    result = []
    for pid in parents:
        rec = obj(obj(bundle.get('cards')).get(pid))
        detail = obj(rec.get('detail'))
        task = {**obj(linked.get(pid)), **obj(detail.get('task'))}
        if not task.get('title') or task.get('status') in TERMINAL:
            continue
        owner = detail.get('owner')
        task.update(id=pid, board=task.get('board') or rec.get('board') or dep.get('board'),
                    gateway=task.get('home_gateway') or task.get('gateway') or
                    (owner if isinstance(owner, str) else None) or
                    (bundle.get('gateway') if detail.get('source') == 'live' else None),
                    profile=task.get('profile') or dep.get('profile'))
        if task.get('gateway') and task.get('profile'):
            result.append(task)
    return sorted(result, key=key)


def wait_leaf(dependency, bundle):
    """Deepest unfinished hydrated parent; bounded, cycle-safe, and deterministic on ties."""
    best, best_depth = dependency, 0
    pending = [(dependency, 0, frozenset())]
    for _ in range(80):
        if not pending:
            break
        dep, depth, visited = pending.pop(0)
        identity = (dep.get('gateway'), dep.get('profile'), dep.get('board'), dep.get('id'))
        if identity in visited or dep.get('status') in TERMINAL:
            continue
        if depth > best_depth:
            best, best_depth = dep, depth
        pending.extend((task, depth + 1, visited | {identity}) for task in wait_parents(dep, bundle))
    return best


def wait_reason(row, bundle):
    """Only the current wait's owner text, never an older ask on running work."""
    if row.get('status') not in ('scheduled', 'blocked', 'triage', 'todo'):
        return ''
    detail = obj(obj(obj(bundle.get('cards')).get(row['id'])).get('detail'))
    for event in reversed(detail.get('events') or []):
        if not isinstance(event, dict):
            continue
        kind, payload = event.get('kind'), obj(event.get('payload'))
        if kind == 'decision_retained':
            disposition = obj(payload.get('disposition'))
            return flat(obj(payload.get('next_action')).get('instruction') or disposition.get('next_step') or disposition.get('summary'))
        if kind == 'spend_budget_triaged':
            return flat(payload.get('reason')) or 'the stage allowance needs review'
        if kind == 'decision_requested':
            return 'asks origin: ' + flat(payload.get('why') or payload.get('reason'))
        if kind in ('scheduled', 'continued', 'blocked'):
            awaiting = obj(payload.get('awaiting'))
            reason = flat(awaiting.get('why') or payload.get('reason') or payload.get('checkpoint'))
            return ('asks origin: ' if awaiting.get('kind') == 'chat' else '') + reason
        if kind in ('claimed', 'unblocked', 'completed', 'archived'):
            break
    return flat(row.get('block_reason')) if row.get('status') in ('blocked', 'triage') else ''


def wait_phrase(row, now):
    return wait_info(row, now)['text']


def agent_hold(row, bundle):
    """Administrative custody, not a human ask or permission to resume execution."""
    if row.get('status') not in ('triage', 'blocked') or row.get('stale') or human_owned(row):
        return False
    detail = obj(obj(obj(bundle.get('cards')).get(row.get('id'))).get('detail'))
    if row.get('status') == 'triage':
        return True
    for event in reversed(detail.get('events') or []):
        if not isinstance(event, dict):
            continue
        if event.get('kind') in ('decision_retained', 'spend_budget_triaged', 'decision_requested'):
            return True
        if event.get('kind') in ('claimed', 'unblocked', 'completed', 'archived', 'blocked', 'scheduled', 'continued'):
            break
    return False


def human_owned(row):
    """Use the shared attention contract only for readable card snapshots."""
    # Partial source coverage is panel-local evidence: an old mirror ask cannot
    # become current merely because core can still validate its stored request.
    if row.get('stale') or row.get('partial_source_unknown'):
        return False
    try:
        from hermes_cli.kanban_open_asks import needs_you
    except ImportError:
        # Core-free public fixtures and rolling older installs only. Keep their
        # legacy adapter until the companion core provides the shared predicate.
        return _legacy_human_owned(row)
    return needs_you(row)


def _legacy_human_owned(row):
    """Compatibility for cores predating the shared attention contract."""
    state = row.get('status')
    if state in TERMINAL or row.get('stale') or row.get('partial_source_unknown'):
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


def triage_label(row, bundle, now, *, wire=False):
    """Current triage entry from lifecycle events, never the card's creation clock."""
    detail = obj(obj(obj(bundle.get('cards')).get(row.get('id'))).get('detail'))
    phase, entered = None, None
    fixed = {'spend_budget_triaged': 'triage', 'spend_budget_held': 'triage', 'decision_retained': 'triage',
             'contained': 'triage', 'gave_up': 'triage', 'supervisor_escalated': 'triage',
             'decision_protected_stopped': 'triage', 'block_loop_detected': 'triage', 'agent_block_escalated': 'triage',
             'claimed': 'running', 'spawned': 'running', 'unblocked': 'ready', 'triage_swept': 'ready',
             'scheduled': 'scheduled', 'continued': 'scheduled', 'blocked': 'blocked',
             'completed': 'done', 'archived': 'archived', 'review_requested': 'review'}
    for event in detail.get('events') or []:
        if not isinstance(event, dict):
            continue
        kind, payload = event.get('kind'), obj(event.get('payload'))
        if kind in ('promoted', 'promoted_manual', 'unblocked'):
            landing = payload.get('status', 'ready')
        else:
            landing = payload.get('status') if kind in ('created', 'status', 'reopened', 'no_progress_escalated') else fixed.get(kind or '')
        if landing is None:
            continue
        if landing == 'triage' and phase != 'triage':
            value = event.get('created_at')
            at = seconds(value) if type(value) in (int, float) else 0
            entered = at if 0 < at <= now else None
        elif landing != 'triage':
            entered = None
        phase = landing
    age = (relative(entered, 'elapsed') if wire else short_dur(now - entered)) if entered else 'entry time unavailable'
    return 'in triage · ' + age


def held_cause(row, bundle):
    detail = obj(obj(obj(bundle.get('cards')).get(row.get('id'))).get('detail'))
    for event in reversed(detail.get('events') or []):
        if not isinstance(event, dict):
            continue
        payload = obj(event.get('payload'))
        if event.get('kind') == 'decision_retained':
            return {'scope_stopped': 'scope stopped', 'decision_pending': 'decision held',
                    'capability_unavailable': 'capability unavailable'}.get(text(obj(payload.get('disposition')).get('reason')), 'decision held')
        if event.get('kind') == 'spend_budget_triaged':
            return 'scope stopped' if payload.get('cause') == 'scope_stopped' else 'run cap used up' if payload.get('limit') == 'runs' else 'spend budget held'
        if event.get('kind') in ('claimed', 'unblocked', 'completed', 'archived'):
            break
    return ''


SPEND_ESCALATION_S = 1800
_REVIEW_PHASE = {'spend_review_queued': 'reviewing', 'spend_review_started': 'reviewing',
                 'spend_review_escalated': 'escalated'}


_REVIEW_ENDS = ('spend_budget_triaged', 'spend_review_granted', 'claimed', 'unblocked', 'completed', 'archived',
                'decision_retained', 'decision_requested', 'blocked', 'scheduled', 'continued')


def spend_review(row, bundle):
    """The current spend stop's review (core ``kanban_spend_reviewer``): `{phase, at, reason}` or None.

    `reviewing` while the reviewer judges the stop; `escalated` once it handed the stop to the owner. A later
    owner decision (`decision_retained`, ...) ends it. A routing notice after a queued or running review means the
    reviewer was turned off and the stop went to its usual owner: no review then."""
    detail = obj(obj(obj(bundle.get('cards')).get(row.get('id'))).get('detail'))
    routed = False
    for event in reversed(detail.get('events') or []):
        if not isinstance(event, dict):
            continue
        kind = event.get('kind')
        if kind == 'spend_budget_decision_ready':
            routed = True
        elif kind in _REVIEW_PHASE:
            if routed and _REVIEW_PHASE[kind] == 'reviewing':
                return None
            value = event.get('created_at')
            return {'phase': _REVIEW_PHASE[kind], 'at': seconds(value) if type(value) in (int, float) else 0,
                    'reason': _row_reason(obj(event.get('payload')).get('reason'))}
        elif kind in _REVIEW_ENDS:
            return None
    return None


def _row_reason(value):
    """A producer's reason fit for a row line: no URL or hash, at most WAIT_MAX characters."""
    reason = flat(re.sub(r'https?://\S+|\b[0-9a-f]{12,64}\b', '', text(value)))
    return reason[:WAIT_MAX - 1].rstrip() + '…' if len(reason) > WAIT_MAX else reason


def spend_escalation_overdue(row, bundle, now):
    """An escalated stop nobody decided for SPEND_ESCALATION_S: the owner is not paying attention."""
    if row.get('stale') or row.get('partial_source_unknown'):
        return False  # unreadable evidence never turns a card red
    review = spend_review(row, bundle)
    return bool(review and review['phase'] == 'escalated' and 0 < review['at'] <= now - SPEND_ESCALATION_S)


def spend_overdue_text(row, bundle, now, *, wire=False):
    review = spend_review(row, bundle) or {}
    age = relative(review.get('at'), 'elapsed') if wire else short_dur(now - (review.get('at') or now))
    reason = review.get('reason') or 'no reason recorded'
    return f'out of budget · the reviewer escalated {age} ago and the owner has not decided yet: {reason}'


def agent_hold_text(row, bundle):
    wait = obj(row.get('wait'))
    if wait.get('next_actor') and wait.get('next_action') and wait.get('waiting_on'):
        # Preserve the authoritative wait producer's actor/action. A coordinating
        # chat turn does not establish that its own agent owns this recovery.
        return 'waiting on ' + flat(wait['waiting_on']) + ' · next: ' + flat(wait['next_action'])
    authority = obj(obj(obj(row.get('wait')).get('contained')).get('authority'))
    if authority.get('id'):
        task = obj(obj(obj(obj(bundle.get('cards')).get(authority['id'])).get('detail')).get('task'))
        name = clean(task.get('title')) or authority['id']
        status = task.get('status') or authority.get('status') or 'status unknown'
        return f'waiting on the release-authority agent: {name} ({status})'
    reason = wait_reason(row, bundle) or 'review the held scope and choose the next step'
    reason = re.sub(r'^Administering owner(?: chat [\w-]+)?:\s*', '', reason, flags=re.I)
    reason = flat(re.sub(r'https?://\S+|\b[0-9a-f]{12,64}\b', '', reason))
    if len(reason) > WAIT_MAX:
        reason = reason[:WAIT_MAX - 1].rstrip() + '…'
    review = spend_review(row, bundle)
    if review and review['phase'] == 'reviewing':
        return 'out of budget · the spend reviewer is deciding now'
    cause = held_cause(row, bundle)
    if review:
        return ((cause + ' · ' if cause else '') +
                f"reviewer escalated: {review['reason'] or reason} · waiting on this chat's agent to decide")
    if not wait_reason(row, bundle) and not cause:
        return 'blocked · execution reason unknown · next actor/action not published'
    return (cause + ' · ' if cause else '') + "waiting on this chat's agent to decide: " + reason


def ask(row, request=None):
    request = request or {}
    hr = obj(row.get('human_request'))
    step = text(obj(row.get('wait')).get('owner_step'))
    if not hr and not request and step:
        # Compatibility attention for a hidden physical wait, not a new consent request.
        # Pure/keyed by the card: no request is posted again on each probe tick.
        hr = {'action': step, 'why_human': 'Only you can: confirm device access or correct this wait.',
              'scope': 'The card was scheduled on device availability without a visible owner step.'}
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
    if request.get('state') == 'needs_context':
        # An approval asked without a written plan: user-steps offers only "Request changes" (a text reply), and
        # nothing can be approved. Drawing that lone revise choice as "A · Request changes / Choose A" under an
        # "Approve ..." title read as a one-button approve/reject card. Show no choices and say what is missing.
        out['options'] = []
        parsed = {**parsed, 'recommend_letter': '', 'reason': ''}
        out['situation'] = ('The agent asked for your approval but posted no plan, so there is nothing to approve '
                            'yet. Ask it for the plan in the chat, or ignore this if the work no longer needs it.')
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
            flag = limit.get('permissionRequired')
            out['details'].append('Limit: ' + text(limit.get('key')) + ': ' + str(limit.get('value', '')) + (
                ' · permission: granted if you approve' if flag is True else
                ' · permissionRequired: ' + json.dumps(flag, ensure_ascii=False)))
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
                               'revision': request.get('revision'), 'owner': request.get('owner'),
                               'admission': {'state': request.get('state'), 'resolved': bool(request.get('resolution')),
                                             'scope_complete': scope.get('complete') is True and bool(full_scope.strip()),
                                             'choices': [{k: obj(c).get(k) for k in ('id', 'effect', 'label')} for c in choices]}})
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
    if not unpublished:
        return on
    if up.get('id'):
        return 'waiting on parent card'
    return f'{state or "status unknown"} · execution reason unknown'


def diagnostic(row, now):
    """A task-scoped diagnostic is independent of an execution generation.

    The acquisition path owns writer authentication. Selection here must never
    infer execution identity or turn a machine restriction into fleet authority.
    """
    try:
        from hermes_cli.kanban_nonrunning_reason import select
    except ImportError:
        return None  # older core; no invented reason or execution identity
    return select(row.get('execution_observations'), row, now=now)


def _ready(row, detail, now, *, wire=False):
    # The board publishes the admission cause. Never replace lane/quota holds with capacity wording.
    note = flat(detail.get('queue_note') or detail.get('capacity_note') or row.get('queue_reason'))
    if row.get('block_kind') == 'lane_gate' or 'lane_gate' in note:
        return 'ready · Waiting for admission to lane'
    if note:
        return f'ready · {note}'
    # Ready is a stored lane, not evidence that worker capacity is the cause.
    # Also used by stale-replica and older-core paths without a diagnostic selector.
    return 'ready · execution reason unknown'


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


def decision_text(request, now, *, wire=False):
    """One status line for an answered ask whose delivery has not been confirmed: `Done · delivers when this turn ends`."""
    if obj(request.get('delivery')).get('state') == 'delivering':
        return text(request['delivery'].get('text')) or 'Delivering…'
    resolution = obj(request.get('resolution'))
    choice = next((c for c in request.get('choices') or [] if isinstance(c, dict) and c.get('id') == resolution.get('choiceId')), {})
    label = clean(re.sub(r'\s*\(recommended\)\s*$', '', text(choice.get('label')), flags=re.I)) or 'Answered'
    try:
        at = datetime.fromisoformat(text(resolution.get('recordedAt'))).timestamp()
    except ValueError:
        at = 0
    # A decision that sat undelivered for long is no longer "waiting for the turn to end": say so.
    return f'{label} · not delivered yet, recorded {relative(at, "ago") if wire else ago(at, now)}' if at and now - at > RECORDED_STALE_S else f'{label} · delivers when this turn ends'


def recorded(request):
    """Answered but not yet delivered: the decision queue keeps the request open until delivery is confirmed."""
    return bool(obj(request.get('resolution'))) and (request.get('state') in ('open', 'needs_context')
            or obj(request.get('delivery')).get('state') == 'delivering')


RECORDED_STALE_S = 900
def header_label(state, status):
    """Backend wording for the reason, never collapse unavailable evidence into a failed turn."""
    reason = obj(status).get('reason')
    codes = obj(obj(status).get('presentation')).get('reason_codes') or []
    if reason == 'source_unknown' or 'linked_status_unknown' in codes:
        sources = obj(obj(status).get('coverage')).get('reasons') or []
        return "status unknown · couldn't read linked cards" if any(r.startswith('cards:') for r in sources) else 'status unknown · source unavailable'
    if state == 'unresolved':
        return {'interrupted': 'interrupted', 'empty': 'not started', 'turn_failed': 'turn failed',
                'linked_work_unsuccessful': 'linked work needs attention', 'turn_unsettled': 'turn outcome unknown',
                'notice_tail': 'reply pending'}.get(reason, 'status unknown')
    if state == 'running' and 'own_turn_active' in codes:
        return 'chat active · task execution shown below'
    return state.replace('needs-you', 'needs you')


def reduce_panel(bundle, *, now, requests=None, subagents=None, processes=None, rentals=None, projector=None, wire=False):
    """Return JSON-safe PanelModel v1. Inputs are snapshots, and are never mutated."""
    sections = {s: [] for s in SECTIONS}
    rows_by_key = {}
    diagnostic_rows = {}
    dependency_prefixes = {}
    requests = list(requests if requests is not None else bundle.get('requests') or [])
    used, seen = set(), set()
    # The serving gateway's own names (board name + install owner id). Only these are equated, never a foreign one.
    same_gateway = frozenset(g for g in [bundle.get('gateway'), *(bundle.get('gateway_aliases') or [])] if g) if bundle.get('gateway') else frozenset()
    def live_request(row):
        return any(r.get('state') in ('open', 'needs_context') and not r.get('resolution')
                   and any(_matches(row, a, same_gateway) for a in _aliases(r)) for r in requests)
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
        diagnostic_row = dict(row)
        detail = obj(rec.get('detail'))
        # Detail is supplementary only. Current list/wait wins over preserved block_kind and old ask text.
        row = {**obj(detail.get('task')), **row}
        if row.get('status') in ('running', 'review') and not row.get('run_model'):
            row['run_model'] = route_model(row, detail)
        k = key(row)
        if k in seen:
            continue
        rows_by_key[k] = row
        diagnostic_rows[k] = diagnostic_row
        state = row.get('status')
        if row.get('unfinished') and state not in ACTIVE:
            state = 'unknown'
        row['status'] = state
        if state in TERMINAL and not (row.get('stale') or row.get('partial_source_unknown')):
            continue
        seen.add(k)
        v = row_view(row)
        parent = obj(row.get('upstream'))
        if parent.get('id'):
            v['details'].append(f'Parent card: {parent["id"]} ({parent.get("status") or "status unknown"})')
        progress = number(row.get('activity_at')) or number(row.get('current_run_started_at'))
        wi = wait_info(row, now, wire=wire)
        dependency = wait_target(row, bundle, detail)
        dependency = wait_leaf(dependency, bundle) if dependency else None
        if state in ('scheduled', 'todo', 'blocked', 'triage') and not text(obj(row.get('wait')).get('waiting_on')) and obj(detail.get('links')).get('parents'):
            leaf = wait_leaf(row, bundle)
            if leaf.get('id') != row['id']:
                dependency = leaf
        dependency_text = ''
        blocker = dependency or row
        why = wait_reason(blocker, bundle)
        if why:
            v['details'].append('Blocking reason: ' + why)
            why = flat(re.sub(r'https?://\S+|\b[0-9a-f]{12,64}\b', '', why))
            why = why if len(why) <= WAIT_MAX else why[:WAIT_MAX - 1].rstrip() + '…'
        gateway = text(blocker.get('gateway'))
        hostname = text(blocker.get('hostname'))
        location = f' on {gateway}' + (f' ({hostname})' if hostname else '') if gateway else ''
        if state in ('scheduled', 'todo', 'blocked', 'triage') and not dependency and (why or wi['text']):
            canonical = re.match(r'(?:the clock until|ci\b|deploy\b)', text(obj(row.get('wait')).get('waiting_on')), re.I)
            base = wi['text'] if canonical else why or wi['text']
            cap = max(24, WAIT_MAX - len(location))
            wi['text'] = (_relative.clip(base, cap) if wire else (base if len(base) <= cap else base[:cap - 1].rstrip() + '…')) + location
        if dependency:
            title = clean(dependency['title'])
            title = title if len(title) <= WAIT_MAX else title[:WAIT_MAX - 1].rstrip() + '…'
            since = number(obj(row.get('wait')).get('waiting_since'))
            age = f' · {relative(since, "elapsed") if wire else short_dur(now - since)}' if 0 < since <= now else ''
            # The awaited card is often not this chat's own (a worker made it), so it has no row of its
            # own here: its live status rides on this line, or a running dependency reads as idle.
            dep_status = text(dependency.get('status'))
            dep_label = triage_label(dependency, bundle, now, wire=wire) if dep_status == 'triage' else dep_status
            dep_state = f' ({dep_label})' if dep_label else ''
            dependency_prefix = f'waiting on: {title}{dep_state}{location}'
            dependency_prefixes[k] = (key(dependency), dependency_prefix)
            dependency_text = dependency_prefix + (f' · {why}' if why else '') + age
            v['details'].append(f'Blocking card: {dependency["id"]} ({dep_status or "status unknown"})')
            if not wi['text'].startswith(('waiting on CI', 'waiting on hub deploy', 'waiting on deploy')):
                wi['text'] = dependency_text
            v['actions'].append({'kind': 'open_card', 'role': 'dependency', 'label': 'Open dependency', 'target': target(dependency)})
        on = wi['text']
        if wi.get('kind') and (wi['kind'] == 'clock' or on.startswith(('waiting on CI', 'waiting on hub deploy', 'waiting on deploy'))):
            # Delivery waits (CI, deploy, clock) paint light blue.
            v['wait_kind'] = wi['kind']
            v['wait_eta'] = wi['eta']
        if wi['raw'] and wi['raw'] != on:
            v['details'].append(f'Waiting on: {wi["raw"]}')
        # A CI wait links its first run from the word "CI" in the detail line (`inline`), not a separate button.
        ci = wi['text'].startswith('waiting on CI')
        v['actions'].extend({'kind': 'open_url', 'label': 'Open CI' if ci else 'Open link', 'url': u,
                             **({'inline': 'CI'} if ci and i == 0 else {})} for i, u in enumerate(wi['urls']))
        if state == 'unknown' and row.get('reported_status', raw.get('status')) in ('done', 'archived', 'done_pending_release'):
            v['detail'] = f'reported {row.get("reported_status") or "done"} by {row.get("gateway") or "another gateway"} · not confirmed by its owner'
            sections['waiting'].append(v)
            continue
        synced_row = row.get('synced_at')
        replica_row = bool(row.get('remote') or row.get('source') in ('replica', 'origin-obligation'))
        # Retain the owner's last parked report and sync age as historical context,
        # never as current wait/attention authority. Even a recent sync timestamp
        # cannot override an explicit stale flag. Unreadable/absent/future clocks use
        # the generic unknown path below.
        if (row.get('stale') and not row.get('partial_source_unknown') and replica_row and not human_owned(row)
                and state in ('scheduled', 'blocked', 'triage', 'review', 'ready', 'todo')
                and type(synced_row) in (int, float) and 0 <= now - synced_row < 8.64e9):
            gw = row.get('gateway') or 'its owner'
            age_s = int(now - synced_row)
            queued = state == 'todo' and not on and not obj(row.get('upstream')).get('id')
            v['parked'] = True
            v['queued'] = queued
            v['evidence_state'] = 'unknown'
            v['reason'] = 'source-stale'
            v.pop('wait_kind', None)
            v.pop('wait_eta', None)
            historical = _ready(row, detail, now, wire=wire) if state == 'ready' else 'not started · execution reason unknown' if queued else _parked(row, on)
            v['detail'] = f'status unknown · last known: {historical}'
            v['details'].append('Task source freshness: stale; read current authoritative task state before acting.')
            if age_s <= 300:
                v['detail'] += f' · on {gw} (synced {age_s}s ago)'
            else:
                v.pop('wait_kind', None)
                v.pop('wait_eta', None)
                v['detail'] += f' · as of {pacific(synced_row)}, {gw} not synced for {max(1, age_s // 60)} min'
                v['details'].append('Historical board status: the owner has not synced recently; this does not establish current execution.')
            v['details'].append(f"Reported by {gw}'s board; no live run evidence reached this gateway.")
            sections['ready' if state == 'ready' or queued else 'waiting'].append(v)
            continue
        if row.get('stale') or row.get('partial_source_unknown'):
            v.pop('wait_kind', None)
            v['evidence_state'] = 'unknown'
            v['detail'] = f'status unknown · last known: {state or "unknown"}; waiting for status from {row.get("gateway") or "the card’s owner"}'
            v['details'].append('The card could not be re-read. Its owner’s agent acts next; unavailable status is not a request for you.')
            sections['waiting'].append(v)
            continue
        matches = [(i, r) for i, r in enumerate(requests) if any(_matches(row, a, same_gateway) for a in _aliases(r))]
        live = next(((i, r) for i, r in matches if r.get('state') in ('open', 'needs_context') and not r.get('resolution')), None)
        if human_owned(row) or (state in ('blocked', 'triage') and live):
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
        # Preserve primary producers. Otherwise name the administrative actor,
        # including when the held card is an off-list dependency on another home.
        blocker_wait = wait_info(blocker, now, wire=wire)
        if dependency and blocker_wait.get('kind') and not blocker.get('stale'):
            v['wait_kind'] = blocker_wait['kind']
            on = dependency_text + ' · ' + blocker_wait['text']
        elif dependency and dependency.get('status') in ('running', 'review') and not dependency.get('stale'):
            v['wait_kind'] = 'card'
        elif dependency and live_request(blocker):
            on = dependency_text + " · waiting on you to answer the parent's current request"
        elif not v.get('wait_kind') and agent_hold(blocker, bundle):
            v['wait_kind'] = 'agent'
            actor = agent_hold_text(blocker, bundle)
            if spend_escalation_overdue(blocker, bundle, now):
                actor, look = spend_overdue_text(blocker, bundle, now, wire=wire), 'stalled'
            elif look == 'stalled':
                look = 'waiting'
            on = dependency_text + ' · ' + actor if dependency else actor
        upstream = obj(row.get('blocked_upstream'))
        if upstream and look != 'stalled':
            contained = obj(upstream.get('contained'))
            v['details'].append(f'Parent card: {upstream.get("id")} ({upstream.get("status") or "status unknown"})')
            if contained:
                v['details'].append(f'Release by card: {obj(contained.get("authority")).get("id") or "unknown"}')
            v['detail'] = on or ('waiting on contained parent card' if contained else 'waiting on blocked parent card')
            sections['waiting'].append(v); continue
        if look == 'stalled':
            v.update(reason='stalled', detail=f'stalled: {on or "agent-owned wait over 2 h"}')
            sections['stuck'].append(v); continue
        why = diagnostic(row, now)
        if state == 'ready' and why is not None:
            v['non_running_reason'] = why
            v['evidence_state'] = 'unknown'  # diagnostic evidence is not execution proof
            v['details'].append(f'Reason freshness: {why.get("freshness") or "unknown"}')
            if why.get('observed_at') is not None:
                v['details'].append(f'Dispatcher evidence as of {pacific(why["observed_at"])}')
            v['details'].append(f'Next actor: {why.get("actor") or "unknown"}')
            v['details'].append(text(why.get('next_action')))
            if why.get('freshness') == 'current':
                blocked = why.get('disposition') == 'blocked'
                v.update(reason='admission-blocked' if blocked else 'admission-wait',
                         detail=f'{"blocked" if blocked else "ready"} · {why["summary"]}')
                sections['stuck' if blocked else 'ready'].append(v)
            else:
                v.update(reason='reason-unknown', detail='ready · execution reason unknown')
                sections['ready'].append(v)
            # Panel acquisition and dispatcher diagnosis have independent clocks.
            # Even a current diagnosis cannot make an old panel snapshot fresh.
            # Wire freshness is rendered from its source timestamp in the client;
            # adding text at an age threshold would publish clock-only revisions.
            if not wire and now > read_at + 60:
                age = short_dur(now - read_at)
                v['detail'] += f' · as of {pacific(read_at)} (panel data {age} old)'
                v['details'].append('Historical board status: the panel data is over 60 seconds old; this does not establish current execution or producer liveness.')
            continue
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
        # Aged board data still describes the last parked state; absence of a run
        # identity is expected there. Do not extend this to contradictory evidence
        # or future read clocks, and never imply the historical producer is live.
        aged_parked = now > read_at + 60 and reason in PRODUCER_GAPS
        if unknown and state in ('scheduled', 'blocked', 'triage', 'review', 'ready', 'todo') and (fresh or aged_parked):
            v['parked'] = True
            queued = state == 'todo' and not on and not obj(row.get('upstream')).get('id')
            v['detail'] = _ready(row, detail, now, wire=wire) if state == 'ready' else 'not started · execution reason unknown' if queued else _parked(row, on)
            v['details'].append(provenance)
            if aged_parked:
                if now - read_at > HISTORICAL_DELIVERY_S or v.get('wait_kind') not in DELIVERY_KINDS:
                    v.pop('wait_kind', None)
                    v.pop('wait_eta', None)
                age = relative(read_at, 'elapsed') if wire else short_dur(now - read_at)
                v['detail'] += f' · as of {pacific(read_at)} (panel data {age} old)'
                v['details'].append('Historical board status: the panel data is over 60 seconds old; this does not establish current execution or producer liveness.')
            v['queued'] = queued
            sections['ready' if state == 'ready' or queued else 'waiting'].append(v); continue
        if unknown:
            v.pop('wait_kind', None)
            v['detail'] = f'status unknown ({reason.replace("_", " ")}) · last known: {ev.get("last_known_state") or state or "unknown"}'
            sections['waiting'].append(v); continue
        if state == 'running' and progress and now - progress >= STUCK_S:
            v.update(reason='no-progress', last_at=progress, detail=f'running, no progress since {pacific(progress)} ({relative(progress, "ago") if wire else ago(progress, now)})' + (f' · last event: {row["activity_kind"]}' if row.get('activity_kind') else '') + (f' · {failures_text(row)}' if failures_text(row) else ''))
            sections['stuck'].append(v)
        elif state in ('running', 'review'):
            v.update(run_meta(row, state, progress, now, wire=wire))
            if v.get('reported'):
                v['details'].append(provenance)
            sections['running'].append(v)
        elif state in ('ready', 'todo'):
            v.update(detail=_ready(row, detail, now, wire=wire) if state == 'ready' else 'not started', queued=state == 'todo')
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
    for view in sections['needs_you']:
        for action in view['actions']:
            if action.get('kind') == 'decide':
                # The adapter supplies install-id aliases from trusted local identity.
                # Never equate a foreign request owner to the serving connection.
                if same_gateway:
                    action['source_owner'] = obj(action.get('owner')).get('gatewayId') in same_gateway
                else:
                    # Unenrolled/older readers have no trusted route identity.
                    # Keep their existing verified owner GET, not an ambient route.
                    action.pop('admission', None)
    views = {x['key']: x for s in SECTIONS for x in sections[s]}
    for r in requests:
        if not recorded(r):
            continue
        # One status line next to the card the answer unblocks (or as its own row for a card-less ask), never a
        # second block: it disappears when the request resolves, i.e. once delivery is confirmed.
        info = {'request_id': r.get('requestId'), 'text': decision_text(r, now, wire=wire)}
        owner = next((views[k] for k, row in rows_by_key.items() if k in views and any(_matches(row, a, same_gateway) for a in _aliases(r))), None)
        if owner is not None and 'decision' not in owner:
            owner['decision'] = info
            # Suppress only the redundant answered-ask placeholder. Delivery is a
            # separate line, never a replacement for the owner's execution status.
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
            if kind == 'process' and row.get('work_id') and bundle.get('session') and row.get('gateway') and row.get('profile'):
                v['actions'].append({'kind': 'open_process', 'label': 'Open process',
                    'target': {'id': row['work_id'], 'session': bundle['session'],
                               'gateway': row['gateway'], 'profile': row['profile']}})
            v.update(evidence_state=ev.get('state'), detail='rented machine' if kind == 'machine' else ev.get('state', 'unknown').replace('_', ' ') + (f' ({ev["unknown_reason"].replace("_", " ")})' if ev.get('unknown_reason') else ''))
            sections['running' if ev.get('state') == 'running' else 'waiting'].append(v)
    done_seen = set()
    for row in sorted(bundle.get('done') or [], key=lambda r: number(r.get('completed_at')), reverse=True):
        if row.get('status') not in ('done', 'archived') or row.get('unfinished') or row.get('stale') or row.get('partial_source_unknown'):
            continue
        k = key(row)
        if k in done_seen or k in seen:
            continue
        done_seen.add(k)
        v = row_view(row)
        result = clean(row.get('result'))
        meta = done_meta(row)
        finished = seconds(row.get('completed_at'))
        done_age = (relative(finished, 'ago') if wire else ago(finished, now)) if finished > 0 else ''
        if meta:
            v['detail'] = ' · '.join(p for p in [done_age, meta['detail']] if p)
            v['details'] = [*meta['details'], *([result] if result else [])]
        else:
            clipped = result if len(result) <= 80 else result[:79].rstrip() + '…'
            v['detail'] = ' · '.join(p for p in [done_age, clipped] if p)
        if done_age:
            v['details'].insert(0, f'completed {pacific(finished)}')
        sections['done'].append(v)
        diagnostic_rows[k] = row
    for views in sections.values():
        for view in views:
            source_row = rows_by_key.get(view['key'], {})
            if source_row.get('status') == 'triage':
                age = triage_label(source_row, bundle, now, wire=wire)
                view['detail'] = (view.get('detail', '') + ' · ' + age).strip(' ·')
                view['details'].append(age)
    groups = _hierarchy.project(sections, rows_by_key, bundle, key=key, row_view=row_view,
                                successors=lambda row: wait_parents(row, bundle),
                                terminal=TERMINAL, prefixes=dependency_prefixes)
    for group in groups:
        card = {**group, **obj(obj(obj(obj(bundle.get('cards')).get(group['id'])).get('detail')).get('task'))}
        matches = [r for r in requests if any(_matches(card, a, same_gateway) for a in _aliases(r))]
        # Canonical retirement outranks a preserved board human_request, just as
        # for ordinary rows. Groups must never revive dismissed attention.
        if live_request(card) or (human_owned(card) and not matches):
            group.update(section='needs_you', detail='waiting on you to answer the current request')
        elif human_owned(card) and matches:
            state = matches[0].get('state') or 'dismissed'
            group.update(section='waiting', wait_kind='agent',
                         detail=f"request {state} · waiting on this chat's agent to continue")
        elif not card.get('stale') and wait_info(card, now, wire=wire).get('kind'):
            group.update(section='waiting', wait_kind=wait_info(card, now, wire=wire)['kind'],
                         detail=wait_info(card, now, wire=wire)['text'])
        elif agent_hold(card, bundle) and spend_escalation_overdue(card, bundle, now):
            group.update(section='stuck', reason='stalled',
                         detail='stalled: ' + spend_overdue_text(card, bundle, now, wire=wire))
        elif agent_hold(card, bundle):
            group.update(section='waiting', wait_kind='agent',
                         detail=agent_hold_text(card, bundle))
        if group.get('status') == 'triage':
            age = triage_label(card, bundle, now, wire=wire)
            group['detail'] += ' · ' + age
            group['details'].append(age)
    # After all row/ask/group construction: no diagnostic can influence any
    # classifier, decision binding, action, wait palette or hierarchy.
    for view in [v for values in sections.values() for v in values] + groups:
        if view.get('kind') == 'card':
            source_row = diagnostic_rows.get(view['key'], view)
            _actionability.present(view, source_row, obj(obj(bundle.get('cards')).get(view['id'])),
                                   now=now, terminal=TERMINAL, serving=same_gateway)
    # Open session names the card's own chat; the chat this panel already shows (any compression ancestor) is not one.
    here = {str(s) for s in [bundle.get('session'), *(bundle.get('lineage') or [])] if s}
    for view in [v for values in sections.values() for v in values] + groups:
        view['actions'] = [a for a in view.get('actions') or [] if not (a.get('kind') == 'open_session' and str(obj(a.get('target')).get('session')) in here)]
    outcome = obj(bundle.get('outcome_json') or bundle.get('outcome'))
    counts = {s: len(sections[s]) for s in SECTIONS}
    pips = [{'section': s, 'count': counts[s], 'label': {'needs_you': 'needs you', 'running': 'running'}.get(s, s)} for s in SECTIONS if counts[s]]
    # Every waiting row waits on CI or a deploy: the waiting pip and the header dot paint light blue.
    delivery = counts['waiting'] > 0 and all(v.get('wait_kind') for v in sections['waiting'])
    wait_kind = 'agent' if any(v.get('wait_kind') == 'agent' for v in sections['waiting']) else next((v.get('wait_kind') for v in sections['waiting']), None)
    for pip in pips:
        if pip['section'] == 'waiting' and delivery:
            pip['wait_kind'] = wait_kind
    status = published_status(outcome)
    # Presentation fallback only: legacy outcome.kind is never a status envelope.
    # An unavailable outcome does not make otherwise readable panel rows unknown.
    row_errors = [e for e in bundle.get('input_errors') or [] if e != 'session outcome unavailable']
    derived = ('unknown' if row_errors else
               'needs-you' if counts['needs_you'] else
               'stuck' if counts['stuck'] else
               'running' if counts['running'] else
               'waiting' if counts['waiting'] or counts['ready'] else 'idle')
    state = status['kind'] if status else derived
    label = header_label(state, status)
    source = 'canonical' if status else 'derived'
    header = {'state': state, 'status': status, 'source': source, 'label': label, 'counts': counts, 'pips': pips}
    if delivery and state == 'waiting' and not (counts['needs_you'] or counts['stuck'] or counts['running']):
        header['wait_kind'] = wait_kind
    result = {'version': 1, 'header': header, 'sections': sections,
              'groups': groups, 'diagnostics': list(bundle.get('input_errors') or [])}
    if wire:
        result = _relative.encode(result)
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return result
