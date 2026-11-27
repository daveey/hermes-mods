"""Hub-owned lane occupancy and spend; the renderer never sums usage receipts."""
from copy import deepcopy
import threading
import time

WINDOWS = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}


def registry_slots(registry):
    """Shared-pool schedulable slots, excluding held/retired/pinned-only hosts.

    Missing node capacity is excluded; unavailable registry evidence is unknown.
    Never substitute the local cap, utilization, pickup policy or machine count.
    """
    if not isinstance(registry, dict) or not isinstance(registry.get('machines'), dict):
        return None
    machines = registry['machines'].values()
    if any(not isinstance(m, dict) for m in machines):
        return None
    entries = [m for m in machines
               if m.get('state') in (None, 'active') and not m.get('hold')
               and not m.get('pinned_only')]
    # Unknown-capacity nodes are not part of the finite schedulable pool.
    # Malformed explicit capacities still invalidate the registry evidence.
    entries = [m for m in entries if m.get('max_card_runs') is not None]
    if any(type(m.get('max_card_runs')) is not int or m['max_card_runs'] < 0 for m in entries):
        return None
    return sum(m['max_card_runs'] for m in entries)


_REGISTRY_TTL_S = 60
_registry_lock = threading.Lock()
_registry_at = None
_registry_value = None
_registry_busy = False


def _fetch_registry_slots():
    from hermes_cli import kanban_worker_node as node
    # Core's ordinary discovery cache retains last-good data during outages.
    return registry_slots(node.registry(require_fresh=True))


def _refresh_registry():
    global _registry_busy, _registry_at, _registry_value
    try:
        value = _fetch_registry_slots()
    except Exception:  # Optional enrichment must fail unknown, never strand a flight.
        value = None
    with _registry_lock:
        _registry_value = value
        _registry_at = time.monotonic()
        _registry_busy = False


def fleet_slots():
    """Nonblocking registry refresh: never put Board HTTP on a sidebar read.

    Expired capacity is unknown, including while a refresh is in flight. Age
    successful and failed attempts from completion, so outages retry at most
    once a minute rather than on every sidebar poll.
    """
    global _registry_busy, _registry_at, _registry_value
    with _registry_lock:
        fresh = _registry_at is not None and time.monotonic() - _registry_at < _REGISTRY_TTL_S
        value = _registry_value if fresh else None
        if not fresh and not _registry_busy:
            _registry_busy = True
            try:
                threading.Thread(target=_refresh_registry, daemon=True, name='lane-fleet-slots').start()
            except RuntimeError:
                _registry_busy = False
                _registry_value = None
                _registry_at = time.monotonic()
        return value


def present(view, slots):
    """Observed chart visuals are hub-owned, after board merge.

    Draft-cap previews are pure presentation computed client-side from each
    cell's observed ``running`` value (precomputing every cap cost ~1.65MB per
    variant). Legacy ``previews`` are stripped so they never ship.
    """
    view = deepcopy(view)
    view['fleet_slots'] = slots
    for row in view.get('lanes', {}).values():
        row.pop('_tick_sources', None)
        ticks = row.get('ticks')
        if not isinstance(ticks, dict):
            continue
        # A previously presented snapshot must not retain draft choices after
        # registry capacity becomes unknown.
        ticks.pop('previews', None)
        cells = ticks['cells']
        peak = max((c.get('running') or 0 for c in cells), default=0) or 1
        def visual(cell, cap, source_peak=None):
            cell = dict(cell)
            running = cell.get('running')
            if running is None:
                cell.update(state='unknown', visual=dict(height=1, opacity=1))
                return cell
            scale = cap if cap is not None and cap > 0 else source_peak or peak
            ratio = min(1, running / scale)
            cell['visual'] = dict(height=(max(.15, ratio) if cell['state'] == 'partial' else
                                          .15 if cell['state'] == 'empty' else 1),
                                  opacity=.25 + .5 * ratio if cell['state'] == 'partial' else 1)
            return cell
        source_peaks = {}
        for cell in cells:
            for comp in cell.get('components', []):
                source_peaks[comp['source']] = max(source_peaks.get(comp['source'], 1), comp.get('running') or 0)
        ticks['cells'] = [visual(c, c.get('cap')) for c in cells]
        for cell in ticks['cells']:
            if cell.get('components'):
                cell['components'] = [visual(c, c.get('cap'), source_peaks[c['source']])
                                      for c in cell['components']]
    return view


def spend(db, policy, *, now=None):
    """Same per-task/run MAX receipts and rounding as core kanban_lanes.usage.

    Apply each cutoff *before* taking that window's maximum: a repeated run-end
    receipt outside the hour must not inflate the hour's smaller later receipt.
    """
    now = int(time.time() if now is None else now)
    out = {name: dict(hour=0., day=0., week=0.) for name in policy.lanes}
    for tenant, hour, day, week in db.execute(
            "SELECT tenant,SUM(hour),SUM(day),SUM(week) FROM ("
            "SELECT t.tenant AS tenant, "
            "MAX(CASE WHEN e.created_at>? THEN COALESCE(json_extract(e.payload,'$.usage.cost_usd'),0) ELSE 0 END) hour, "
            "MAX(CASE WHEN e.created_at>? THEN COALESCE(json_extract(e.payload,'$.usage.cost_usd'),0) ELSE 0 END) day, "
            "MAX(COALESCE(json_extract(e.payload,'$.usage.cost_usd'),0)) week "
            "FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE e.kind='worker_result' AND e.created_at>? AND json_extract(e.payload,'$.usage') IS NOT NULL "
            "GROUP BY e.task_id,e.run_id) GROUP BY tenant", (now - 3600, now - 86400, now - 604800)):
        row = out[policy.lane_of(tenant)]
        for key, value in zip(('hour', 'day', 'week'), (hour, day, week)):
            row[key] = round(row[key] + float(value or 0), 2)
    return out


def project(sample_db, usage_db, policy, *, window='24h', now=None):
    now = int(time.time() if now is None else now)
    totals = spend(usage_db, policy, now=now)
    try:
        from hermes_cli.kanban_lane_samples import ticks
    except ImportError:
        series = None
    else:
        series = ticks(sample_db, policy, window_s=WINDOWS[window], count=48, now=now)
    return {name: dict(ticks=(dict(window=window, bucket_s=WINDOWS[window] / 48,
                                  cap=policy.lanes[name].max_workers, cells=series[name])
                             if series is not None else None), spend=totals[name])
            for name in policy.lanes}


def qualify(rows, source):
    """Attach board provenance before any fleet reduction (also for no-table boards)."""
    for row in rows.values():
        row['_tick_sources'] = [(source, row.get('ticks'))]


def _sources(row, fallback):
    return row.get('_tick_sources', [(fallback, row.get('ticks'))])


def _source_envelope(sources):
    available = [(name, ticks) for name, ticks in sources if ticks is not None]
    if not available:
        return None
    ref = available[0][1]
    cells = []
    for i, cell in enumerate(ref['cells']):
        components = []
        for name, ticks in sources:
            candidate = (ticks['cells'][i] if ticks is not None and
                         ticks['window'] == ref['window'] and i < len(ticks['cells']) else None)
            if candidate is None or candidate['t'] != cell['t']:
                candidate = dict(t=cell['t'], state='unknown', running=None, cap=None,
                                 rejected=None, reason='no samples' if ticks is None else 'unaligned samples')
            else:
                candidate = dict(candidate)
                if candidate.get('running') is None:
                    candidate['reason'] = 'no samples in this period'
            components.append(dict(candidate, source=name))
        # Board bucket peaks are independent observations, not simultaneous
        # fleet occupancy. Never add them or use their sum for cap forecasts.
        cells.append(dict(t=cell['t'], state='unknown', running=None, cap=None,
                          rejected=None, components=components))
    return dict(window=ref['window'], bucket_s=ref['bucket_s'], cap=None, cells=cells)


def merge(first, second):
    """Independent boards add accounting; a missing observation never becomes zero."""
    out = {}
    for name in first.keys() | second.keys():
        a, b = first.get(name), second.get(name)
        if a is None or b is None:
            out[name] = a if b is None else b
            continue
        spending = (None if a.get('spend') is None or b.get('spend') is None else
                    {key: round(a['spend'][key] + b['spend'][key], 2) for key in ('hour', 'day', 'week')})
        sources = _sources(a, 'first board') + _sources(b, 'second board')
        out[name] = dict(ticks=_source_envelope(sources), spend=spending, _tick_sources=sources)
    return out
