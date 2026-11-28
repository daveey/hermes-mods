"""Read-only flight deck. Producer adapters own storage; this module never opens SQL.

Wire v1: snapshot fields are replacement units. Stream starts with a snapshot,
then emits {type: delta, base_cursor, cursor, changes}. Clients shallow-merge
changes only when base_cursor matches, otherwise reconnect for a full snapshot.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
_LOAD_LOCK = threading.RLock()
POLL_SECONDS = 1.0


def home():
    try:
        from hermes_constants import get_hermes_home
    except ImportError:
        return Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes'))
    return Path(get_hermes_home())


def producer(name):
    """Reuse core's mounted module identity; never create a second producer owner."""
    path = ROOT / name / 'dashboard' / 'plugin_api.py'
    key = 'hermes_dashboard_plugin_' + name
    with _LOAD_LOCK:
        existing = sys.modules.get(key)
        if existing is not None and Path(getattr(existing, '__file__', '')).resolve() == path.resolve():
            return existing
        try:
            from hermes_cli.plugin_isolation import import_user_code
        except ImportError:
            pass  # Standalone plugin tests without core.
        else:
            module = import_user_code(path, key)
            if module is None:
                raise RuntimeError('producer import refused')
            return module
        spec = importlib.util.spec_from_file_location(key, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(key, None)
            raise
        return module


def read_lanes():
    value = producer('lanes').rest_view(6.0, '6h', True)
    if not value.get('configured') or value.get('warming'):
        raise RuntimeError('lanes projection unavailable')
    return value


def read_sessions():
    # Read an existing process feed, never start a parallel feed from dashboard demand.
    api = producer('session-panel')
    core = sys.modules.get('_session_panel_feed_core')
    feed = getattr(core, 'PROCESS_FEED', None)
    # A process feed is launch-profile scoped. Never carry it into a secondary
    # request profile (to_thread retains the core's profile ContextVars).
    readers = getattr(feed, 'readers', None)
    feed_home = getattr(getattr(readers, 'home_context', None), 'home', None)
    request_profile = getattr(api, '_request_profile', lambda: None)()
    scoped = (str(feed_home) == str(home()) if feed_home else
              request_profile is not None and request_profile == getattr(api, 'LAUNCH_PROFILE', None))
    if request_profile is not None and request_profile != getattr(api, 'LAUNCH_PROFILE', None):
        raise RuntimeError('session-panel request profile unavailable')
    if feed is not None and scoped and not feed.stopped.is_set():
        snap = feed.snapshot(timeout=0.1)
    else:
        snap = api.load_snapshot()
    if snap is None:
        raise RuntimeError('session-panel cached snapshot unavailable')
    snap = copy.deepcopy(snap)
    model = api._load_feed()._model()
    for held in snap.get('bundles', {}).values():
        bundle = held.get('bundle', held)
        for task in bundle.get('tasks', []):
            detail = obj(bundle.get('cards')).get(str(task.get('id')), {})
            dependency = model.wait_target(task, bundle, detail)
            if dependency:
                task['prerequisite'] = model.wait_leaf(dependency, bundle) or dependency
    return snap


def read_requests(sessions):
    lineages = {sid: held.get('bundle', held).get('lineage', [sid])
                for sid, held in sessions.get('bundles', {}).items()}
    value = producer('user-steps').panel_requests_many(home(), lineages)
    if any(isinstance(rows, Exception) for rows in value.values()):
        raise RuntimeError('user-steps ownership lookup unavailable')
    return value


def read_repair():
    value = producer('session-repair')._last()
    if value.get('scanned_at') is None:
        raise RuntimeError('session-repair scan unavailable')
    return value


INVENTORY_LIMIT = 5000
INVENTORY_TIMEOUT = 2.0


async def read_inventory():
    """Bounded canonical replica read; no direct or sibling-profile database access."""
    value = await asyncio.wait_for(producer('hub-sync').get_sessions(
        gateway=None, exclude=None, archived=0, limit=INVENTORY_LIMIT, projection=None),
        timeout=INVENTORY_TIMEOUT)
    if hasattr(value, 'body'):
        value = json.loads(value.body)
    return value


def inventory_complete(value):
    return (isinstance(value, dict) and value.get('available') is True
            and not any(value.get(k) for k in ('warming', 'stale', 'offline_since', 'partial', 'has_more', 'truncated'))
            and isinstance(value.get('sessions'), list)
            and len(value['sessions']) < INVENTORY_LIMIT
            and all(isinstance(r, dict) and all(r.get(k) for k in ('gateway', 'profile', 'id'))
                    for r in value['sessions']))


def canonical_inventory_rows(value):
    # The public session-state-v2 wire lives in outcome.status (status_version 1).
    # Reuse its canonical consumer validator, not legacy running/status flags.
    model = producer('session-panel')._load_feed()._model()
    result = []
    for raw in obj(value).get('sessions', []):
        if not isinstance(raw, dict) or not all(raw.get(k) for k in ('gateway', 'profile', 'id')):
            continue
        row = session_row({**raw, 'key': raw['id'], 'session_id': raw['id']})
        outcome = raw.get('outcome')
        if isinstance(outcome, str):
            try:
                outcome = json.loads(outcome)
            except ValueError:
                outcome = None
        status = model.published_status(obj(outcome))
        identity = obj(obj(status).get('identity'))
        owned = (identity.get('gateway'), identity.get('profile'), identity.get('session_id')) == (raw['gateway'], raw['profile'], raw['id'])
        fresh = not raw.get('stale') and not any(obj(value).get(k) for k in ('stale', 'offline_since', 'warming'))
        row['state'] = status['kind'] if status and owned and fresh and status['coverage']['state'] == 'complete' else 'unknown'
        row['canonical_status'] = status if owned else None
        result.append(row)
    return result


async def read_sync():
    value = await producer('hub-sync').get_status()
    if hasattr(value, 'body'):
        value = json.loads(value.body)
    if not isinstance(value, dict) or value.get('warming') or value.get('available') is False:
        raise RuntimeError('sync status unavailable')
    return value


def read_auth():
    from hermes_cli.web_server_oauth import _anthropic_oauth_status
    value = _anthropic_oauth_status()
    # The canonical status includes token previews and local paths: omit both.
    return {'anthropic': {k: value.get(k) for k in ('logged_in', 'expires_at', 'has_refresh_token')}}


def read_hub():
    api = producer('hub-status')
    return {'status': api.status(), 'history': api.events('24h')}


def read_fleet():
    return producer('runners').fleet_view()


def read_subscription():
    # Reuse the producer's saved-sample reader, not throttle/advisory or a quota
    # refresh. pacing_path owns the machine-wide governor's canonical location.
    import importlib
    package = producer('throughput').pkg()
    runway = importlib.import_module(package.__name__ + '.runway')
    return {'pools': runway.pools(runway.pacing_path(home()), time.time())}


class ProducerAdapters:
    """Injection seam; defaults call canonical producer APIs, not sample data."""
    lanes = staticmethod(read_lanes)
    sessions = staticmethod(read_sessions)
    inventory = staticmethod(read_inventory)
    requests = staticmethod(read_requests)
    repair = staticmethod(read_repair)
    sync = staticmethod(read_sync)
    auth = staticmethod(read_auth)
    hub = staticmethod(read_hub)
    fleet = staticmethod(read_fleet)
    subscription = staticmethod(read_subscription)


adapters = ProducerAdapters()


async def _invoke(fn, *args):
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    value = await asyncio.to_thread(fn, *args)
    return await value if inspect.isawaitable(value) else value


def obj(value):
    return value if isinstance(value, dict) else {}


def session_key(row):
    route = obj(row.get('route'))
    if route.get('gatewayId') and route.get('targetProfile') and row.get('session_id'):
        return '/'.join((route['gatewayId'], route['targetProfile'], row['session_id']))
    return row.get('session_id') or row['key']


def session_row(raw, sid=None):
    raw = obj(raw)
    source = obj(raw.get('source'))
    sid = raw.get('session_id') or raw.get('session') or source.get('id') or sid
    owner = obj(raw.get('owner'))
    route = obj(raw.get('route'))
    key = sid if source.get('kind') == 'session' else raw.get('key') or raw.get('id') or sid
    result = dict(key=str(key),
                title=raw.get('title') or sid or 'Untitled',
                state=raw.get('state') or raw.get('status') or 'unknown',
                session_id=sid, lane=raw.get('lane'),
                route={**{k: v for k, v in route.items() if k in
                          ('connectionId', 'gatewayId', 'profile', 'targetProfile')},
                       **({'mode': route['mode']} if route.get('mode') in ('local', 'remote') else {}),
                       'gatewayId': route.get('gatewayId') or raw.get('gateway') or owner.get('gatewayId'),
                       'profile': route.get('profile') or owner.get('profile') or raw.get('profile'),
                       'targetProfile': route.get('targetProfile') or owner.get('profile') or raw.get('profile')})
    if sid and key == sid:
        result['key'] = session_key(result)
    return result


def valid_request(request, sid):
    request = obj(request)
    owner = obj(request.get('owner'))
    revision = request.get('revision')
    return (request.get('state') in ('open', 'pending', 'needs_context') and not request.get('resolution')
            and bool(request.get('requestId')) and isinstance(revision, int) and not isinstance(revision, bool)
            and revision > 0 and all(owner.get(k) for k in ('gatewayId', 'profile', 'storedSessionId'))
            and owner['storedSessionId'] == sid)


def _lane(key, raw, *, status_unknown=False, governor_unknown=False):
    strip, limits, usage = (obj(raw.get(k)) for k in ('strip', 'limits', 'usage'))
    gate = obj(raw.get('gate'))
    result = {**raw, 'key': key, 'label': raw.get('label') or key,
            'running': raw.get('running'), 'over': strip.get('over'),
            'cap': strip.get('cap', limits.get('max_workers')), 'share': raw.get('share'),
            'target_share': raw.get('target'), 'actual_share': raw.get('actual'),
            'gate': {'admitted': gate.get('admitted', raw.get('admitted_today', usage.get('admitted_today'))),
                     'budget': gate.get('budget', raw.get('admit_per_day', limits.get('admit_per_day')))},
            'cost': {'today': usage.get('usd_today'), 'window': raw.get('cost_usd')}}
    result['status'] = 'unknown' if status_unknown or governor_unknown else 'healthy'
    result['unknown'] = (['status counts'] if status_unknown else []) + (['governor counts'] if governor_unknown else [])
    if status_unknown:
        for field in ('running', 'ready', 'human', 'on_you', 'blocked_on_you', 'not_ready', 'open', 'held', 'done', 'over', 'statuses'):
            result[field] = None
        result['status_counts'] = None
    return result


def board_completeness(lanes):
    lanes = obj(lanes)
    governors = obj(lanes.get('governors'))
    status = lanes.get('flow_complete') is not False and lanes.get('summary_complete') is not False
    status = status and not lanes.get('incomplete_status_lanes')
    governor = governors.get('available') is True
    return bool(status), governor


def machinery_signals(telemetry):
    """Current canonical faults, not historical flags or canned incidents."""
    for finding in obj(telemetry.get('repair')).get('findings', []):
        if str(finding.get('id', '')).startswith('BS-'):
            target = finding.get('card') or finding.get('chat')
            key = target or finding.get('fingerprint')
            if key:
                row = session_row({'id': key, 'session_id': finding.get('chat'),
                                   'title': finding.get('title')})
                yield {**row, 'state': 'stalled', 'label': finding.get('summary') or finding['id'],
                       'governor': 'repair', 'finding': finding}
    sync = obj(telemetry.get('sync'))
    if sync.get('last_error') or sync.get('state') == 'rejected' or sync.get('rejected'):
        yield {**session_row({'id': 'sync:rejected', 'title': 'Sync rejected'}),
               'state': 'stalled', 'label': 'Sync rejected', 'governor': 'sync'}
    auth = obj(telemetry.get('auth'))
    providers = {'anthropic': auth} if 'logged_in' in auth else auth
    for name, value in providers.items():
        if obj(value).get('logged_in') is False:
            yield {**session_row({'id': 'auth:' + name, 'title': name + ' authentication dropped'}),
                   'state': 'stalled', 'label': 'Authentication dropped', 'governor': 'auth'}


def unknown_telemetry(source):
    return {'state': 'unknown', 'reason': source + ' producer unavailable'}


def hub_view(value):
    status, history = obj(obj(value).get('status')), obj(obj(value).get('history'))
    start, end = history.get('start'), history.get('now')
    events = history.get('events')
    if (status.get('state') not in ('up', 'restarting', 'down')
            or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
            or end <= start or not isinstance(events, list)):
        return unknown_telemetry('hub-status')
    # Preserve the transition timeline and its unobserved prefix. No events does
    # not mean a day of uptime; only the producer's clipped lead establishes it.
    slots, cursor, state = [], start, 'unknown'
    for event in sorted((e for e in events if isinstance(e, dict)
                         and isinstance(e.get('ts'), (int, float))), key=lambda e: e['ts']):
        at = max(start, min(end, event['ts']))
        if at > cursor:
            slots.append({'start': cursor, 'end': at, 'state': state})
        cursor = at
        state = event.get('state') if event.get('state') in ('up', 'restarting', 'down') else 'unknown'
    if cursor < end:
        slots.append({'start': cursor, 'end': end, 'state': state})
    return {'state': 'healthy' if status['state'] == 'up' else 'degraded',
            'current': status['state'], 'band': {'start': start, 'end': end, 'slots': slots},
            'since': status.get('since')}


def occupancy_view(section, *, ci=False):
    section = obj(section)
    source = 'runners CI' if ci else 'runners fleet'
    machines = section.get('machines')
    if section.get('state') not in ('ready', 'stale') or not isinstance(machines, list):
        return unknown_telemetry(source)
    slots, complete = [], section['state'] == 'ready'
    for machine in machines:
        machine = obj(machine)
        if ci:
            observed = machine.get('slots')
            if not isinstance(observed, list):
                complete = False
                continue
            for slot in observed:
                state = obj(slot).get('state', 'unknown') if section['state'] == 'ready' else 'unknown'
                complete = complete and state in ('busy', 'idle', 'offline')
                slots.append({'busy': state == 'busy' if state in ('busy', 'idle') else None, 'state': state})
        else:
            capacity, running = machine.get('slots'), machine.get('running')
            # Launch estimates are not registered capacity, so never draw them.
            if machine.get('state') == 'launching':
                continue
            if type(capacity) is not int or capacity < 0 or not isinstance(running, list):
                complete = False
                continue
            known = section['state'] == 'ready' and machine.get('state') in ('active', 'draining')
            for index in range(max(capacity, len(running))):
                busy = index < len(running) if known else None
                slots.append({'busy': busy, 'state': ('busy' if busy else 'idle') if known else machine.get('state', 'unknown')})
            complete = complete and known
    count = sum(obj(m).get('state') != 'launching' for m in machines)
    return {'state': 'healthy' if complete else 'unknown', 'slots': slots,
            'capacity': len(slots) if complete else None, 'servers': count,
            'label': str(count) + (' server' if count == 1 else ' servers')}


def subscription_view(value):
    pools = obj(value).get('pools')
    if not isinstance(pools, list) or not pools:
        return unknown_telemetry('throughput runway')
    tags = []
    for pool in pools:
        pool = obj(pool)
        if not pool.get('provider') or not pool.get('accounts'):
            continue
        seconds = pool.get('runway_s')
        label = (format(seconds / 3600, '.1f') + 'h runway'
                 if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 'runway unknown')
        tags.append({'provider': pool['provider'], 'label': pool['provider'] + ' · ' + label,
                     **{k: pool.get(k) for k in ('runway_s', 'pacing', 'next_reset_at', 'read_at')}})
    if not tags:
        return unknown_telemetry('throughput runway')
    return {'state': 'healthy', 'tags': tags, 'label': ' · '.join(t['label'] for t in tags)}


def telemetry_view(lanes, telemetry, machinery_count):
    lanes = obj(lanes)
    fleet = obj(telemetry.get('runners'))
    alerts = list(machinery_signals({'sync': telemetry.get('sync'), 'auth': telemetry.get('auth')}))
    alert_known = telemetry.get('sync') is not None and telemetry.get('auth') is not None
    return {**{k: v for k, v in telemetry.items() if k != 'runners'}, 'hub': hub_view(telemetry.get('hub')),
            'fleet': occupancy_view(fleet.get('gateways')), 'ci': occupancy_view(fleet.get('ci'), ci=True),
            'subscription': subscription_view(telemetry.get('subscription')),
            'alert_label': ' · '.join(r['title'] for r in alerts) if alerts else ('Sync/auth healthy' if alert_known else 'Sync/auth unknown'),
            'alert_state': 'degraded' if alerts else ('known' if alert_known else 'unknown'),
            'governors': lanes.get('governors')}


def project(lanes, panel, requests, telemetry, sources, *, q='', lane='', inventory=None):
    rows, human, machinery, chains = {}, {}, {}, {}
    for raw in obj(lanes).get('cards', []):
        row = session_row(raw)
        governor = raw.get('governor')
        if governor and governor != 'human':
            row.update(governor=governor, label='Waiting for admission to lane' if governor == 'lane_gate' else governor)
            machinery[row['key']] = row
        if obj(raw.get('source')).get('kind') == 'session':
            sid = row['session_id']
            rows.setdefault(row['key'], row)
            if raw.get('reason') == 'run':
                rows[row['key']]['state'] = 'running'
    bundles = obj(panel).get('bundles', {})
    for sid, held in bundles.items():
        bundle = obj(held.get('bundle', held))
        metadata = {k: bundle[k] for k in ('gateway', 'profile', 'owner', 'route', 'title') if k in bundle}
        current = session_row({**metadata, **obj(bundle.get('session')), 'session_id': sid}, sid)
        previous = rows.get(current['key'], {})
        row = {**previous, **current}
        row['route'] = {**obj(previous.get('route')), **{k: v for k, v in row['route'].items() if v is not None}}
        sections = obj(obj(bundle.get('model')).get('sections'))
        inferred = 'running' if sections.get('running') else previous.get('state', 'unknown')
        row['state'] = obj(bundle.get('wait')).get('wait_state') or (row['state'] if row['state'] != 'unknown' else inferred)
        if row.get('lane') is None:
            row['lane'] = previous.get('lane')
        rows[row['key']] = row
        for raw in bundle.get('tasks', []):
            task = session_row({**metadata, **raw}, sid)
            wait = obj(raw.get('wait'))
            governor = raw.get('governor') or wait.get('governor')
            is_human = (wait.get('wait_state') == 'human' or raw.get('wait_state') == 'human'
                        or obj(raw.get('wakes')).get('blocks_for_human') is True)
            prerequisite = raw.get('prerequisite') or wait.get('prerequisite')
            chain_waiter = isinstance(prerequisite, dict) and prerequisite.get('state', prerequisite.get('status')) == 'running'
            if is_human:
                task['state'] = 'human'
                human[task['key']] = task
            elif not chain_waiter and (governor or wait.get('wait_state') in ('machinery', 'stalled', 'waiting') or raw.get('reason') in ('gate', 'dep', 'slot', 'quota', 'review', 'time')):
                task['governor'] = governor
                task['label'] = 'Waiting for admission to lane' if governor == 'lane_gate' else (raw.get('label') or governor or 'Waiting for machinery')
                machinery[task['key']] = task
            # Canonical dependency identity, not historical block_kind.
            prerequisite = raw.get('prerequisite') or wait.get('prerequisite')
            if chain_waiter and not is_human and isinstance(prerequisite, dict):
                parent = session_row(prerequisite)

                chain = chains.setdefault(parent['key'], {'key': parent['key'], 'prerequisite': parent, 'waiters': []})
                if task['key'] not in {r['key'] for r in chain['waiters']}:
                    chain['waiters'].append(task)
    for row in inventory or []:
        previous = rows.get(row['key'], {})
        rows[row['key']] = {**previous, **row, 'lane': row.get('lane') or previous.get('lane')}
    for sid, candidates in obj(requests).items():
        if not isinstance(candidates, list):
            continue
        for request in candidates:
            lineage = obj(obj(bundles.get(sid)).get('bundle', bundles.get(sid))).get('lineage', [sid])
            owner_sid = obj(request.get('owner')).get('storedSessionId')
            if owner_sid in {sid, *lineage} and valid_request(request, owner_sid):
                owner = request['owner']
                base = next((r for r in rows.values() if r['session_id'] == sid
                             and r['route'].get('gatewayId') == owner['gatewayId']
                             and r['route'].get('targetProfile') == owner['profile']), None)
                row = {**(base or session_row({}, sid)), 'route': dict((base or {}).get('route', {})),
                       'key': 'request:' + request['requestId'],
                       'session_id': owner_sid,
                       'title': request.get('title') or rows.get(sid, {}).get('title') or sid,
                       'state': 'human', 'request': request}
                owner = request['owner']
                row['route'].pop('connectionId', None)
                row['route'].update(gatewayId=owner['gatewayId'], profile=owner['profile'], targetProfile=owner['profile'])
                if inventory is not None:
                    row['key'] = '/'.join((owner['gatewayId'], owner['profile'], row['key']))
                human[row['key']] = row
    # A human session itself need not have a task.
    for sid, row in rows.items():
        if row['state'] == 'human' and not any(session_key(r) == session_key(row) for r in human.values()):
            human[row['key']] = row
    for signal in machinery_signals(telemetry):
        key = signal['key']
        if key in machinery:
            machinery[key].update({k: v for k, v in signal.items() if k in ('finding', 'label')})
        elif key not in human and not any(key == w['key'] for c in chains.values() for w in c['waiters']):
            previous = rows.get(key, {})
            machinery[key] = {**previous, **{k: v for k, v in signal.items() if v is not None},
                              'route': {**obj(previous.get('route')), **{k: v for k, v in signal['route'].items() if v is not None}}}
    # One console object, one placement. Chain members own their placement;
    # prerequisites are not repeated in the general session list.
    chain_keys = {r['key'] for c in chains.values() for r in [c['prerequisite'], *c['waiters']]}
    for key in chain_keys | set(human):
        machinery.pop(key, None)
    placed = chain_keys | set(human) | set(machinery)
    rows = {key: row for key, row in rows.items() if row['key'] not in placed}
    known = {s['name']: s['state'] == 'healthy' for s in sources}
    status_complete, governors_complete = board_completeness(lanes)
    def matches(row):
        return (not lane or row.get('lane') == lane) and (not q or q.casefold() in (str(row.get('title', '')) + ' ' + str(row.get('session_id', ''))).casefold())
    def counts(h, m, s, filtered=False):
        census = (known.get('session-panel') and known.get('lanes') and status_complete
                  and known.get('session-inventory', True))
        visible_chains = [c for c in chains.values() if not filtered or matches(c['prerequisite']) or any(matches(w) for w in c['waiters'])]
        prerequisites = {c['prerequisite']['key']: c['prerequisite'] for c in visible_chains}
        return {'human': len(h) if census and known.get('user-steps') else None,
                'machinery': len(m) if census and governors_complete and all(known.get(n) for n in ('session-repair', 'sync', 'auth')) else None,
                'sessions': len(s) + len(prerequisites) if census else None,
                'running': sum(r['state'] == 'running' for r in [*s, *prerequisites.values()]) if census and all(r['state'] != 'unknown' for r in [*s, *prerequisites.values()]) else None}
    h, m, s = list(human.values()), list(machinery.values()), list(rows.values())
    totals = counts(h, m, s)
    h, m, s = ([r for r in group if matches(r)] for group in (h, m, s))
    totals['filtered'] = counts(h, m, s, filtered=True)
    incomplete_status = obj(lanes).get('incomplete_status_lanes')
    incomplete_governors = obj(obj(lanes).get('governors')).get('incomplete_lanes')
    lane_rows = [_lane(k, v,
        status_unknown=not status_complete and (not incomplete_status or k in incomplete_status),
        governor_unknown=not governors_complete and (incomplete_governors is None or k in incomplete_governors))
        for k, v in obj(lanes).get('lanes', {}).items() if not lane or k == lane]
    return {'schema': 'OrchestratorSnapshot', 'version': 1, 'lanes': lane_rows, 'totals': totals,
            'human': h, 'machinery': m, 'sessions': s,
            'chains': [c for c in chains.values() if matches(c['prerequisite']) or any(matches(w) for w in c['waiters'])],
            'telemetry': telemetry_view(lanes, telemetry, totals['machinery']), 'sources': sources,
            'unknown_counts': [key for key, value in totals.items() if value is None]}


async def snapshot(*, q='', lane='', adapter=None):
    adapter = adapter or adapters
    sources = []
    async def read(name, fn, *args):
        try:
            value = await _invoke(fn, *args)
            if value is None:
                raise ValueError('no producer snapshot')
            sources.append({'name': name, 'state': 'healthy'})
            return value
        except Exception:
            # Exception text can contain credentials/paths; name the missing authority only.
            sources.append({'name': name, 'state': 'unknown', 'reason': name + ' producer unavailable'})
            return None
    lanes, panel, repair, sync, auth = await asyncio.gather(
        read('lanes', adapter.lanes), read('session-panel', adapter.sessions),
        read('session-repair', adapter.repair), read('sync', adapter.sync), read('auth', adapter.auth))
    inventory = None
    # An absent optional seam denotes an explicitly injected, scoped fixture.
    # Defaults always require the global authority, even when offline or warming.
    if hasattr(adapter, 'inventory'):
        value = await read('session-inventory', adapter.inventory)
        source = next(s for s in sources if s['name'] == 'session-inventory')
        complete = inventory_complete(value)
        try:
            inventory = await _invoke(canonical_inventory_rows, value) if obj(value).get('sessions') else []
        except Exception:
            inventory, complete = [], False
        if not complete:
            source.update(state='unknown', reason='global session inventory unavailable or partial')
    # Older fixture adapters may omit telemetry; absence stays named unknown,
    # rather than falling through to a real host read from an injected fixture.
    hub, runners, subscription = await asyncio.gather(
        read('hub-status', getattr(adapter, 'hub', lambda: None)),
        read('runners', getattr(adapter, 'fleet', lambda: None)),
        read('throughput-runway', getattr(adapter, 'subscription', lambda: None)))
    for name, view in (('hub-status', hub_view(hub)),
                       ('fleet', occupancy_view(obj(runners).get('gateways'))),
                       ('ci', occupancy_view(obj(runners).get('ci'), ci=True)),
                       ('subscription', subscription_view(subscription))):
        source = next((s for s in sources if s['name'] == name), None)
        if source is None:
            source = {'name': name}
            sources.append(source)
        source.update(state='unknown' if view['state'] == 'unknown' else 'healthy')
        if view['state'] == 'unknown':
            source['reason'] = name + ' producer unavailable'
    requests = await read('user-steps', adapter.requests, panel) if panel is not None else None
    if panel is None:
        sources.append({'name': 'user-steps', 'state': 'unknown', 'reason': 'session-panel prerequisite unavailable'})
    governors = obj(lanes).get('governors')
    status_complete, _ = board_completeness(lanes)
    if lanes is not None and not status_complete:
        source = next(s for s in sources if s['name'] == 'lanes')
        source.update(state='unknown', reason='partial board status census',
                      incomplete_status_lanes=obj(lanes).get('incomplete_status_lanes'))
    sources.append({'name': 'governors', 'state': 'healthy' if obj(governors).get('available') else 'unknown'})
    result = project(lanes, panel, requests, {'repair': repair, 'sync': sync, 'auth': auth,
                     'hub': hub, 'runners': runners, 'subscription': subscription},
                     sorted(sources, key=lambda s: s['name']), q=q, lane=lane, inventory=inventory)
    result['cursor'] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    result['generated_at'] = time.time()
    return result


def delta(previous, current):
    if previous['cursor'] == current['cursor']:
        return None
    changes = {k: v for k, v in current.items() if k != 'cursor' and previous.get(k) != v}
    return {'type': 'delta', 'base_cursor': previous['cursor'], 'cursor': current['cursor'], 'changes': changes}


@router.get('/v1/snapshot')
async def get_snapshot(q: str = Query('', max_length=256), lane: str = Query('', max_length=128)):
    return await snapshot(q=q, lane=lane)


@router.websocket('/v1/stream')
async def stream(ws: WebSocket):
    # Authentication and profile scope belong to the core plugin router loader.
    await ws.accept()
    try:
        previous = await snapshot(q=ws.query_params.get('q', '')[:256], lane=ws.query_params.get('lane', '')[:128])
        await ws.send_json({'type': 'snapshot', 'snapshot': previous})
        while True:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=POLL_SECONDS)
                if message['type'] == 'websocket.disconnect':
                    return
            except asyncio.TimeoutError:
                pass
            current = await snapshot(q=ws.query_params.get('q', '')[:256], lane=ws.query_params.get('lane', '')[:128])
            change = delta(previous, current)
            if change is not None:
                await ws.send_json(change)
                previous = current
    except WebSocketDisconnect:
        return
