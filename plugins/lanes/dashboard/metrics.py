"""Hub-owned lane occupancy and spend; the renderer never sums usage receipts."""
from copy import deepcopy
import threading
import time

WINDOWS = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}


def registry_slots(registry):
    """Sum configured active, nonheld capacity; unset/unbounded is unknown.

    A successfully fetched empty fleet has zero slots. Never substitute the
    local dispatcher cap, live utilization, pickup policy or machine count.
    """
    if not isinstance(registry, dict) or not isinstance(registry.get('machines'), dict):
        return None
    machines = registry['machines'].values()
    if any(not isinstance(m, dict) for m in machines):
        return None
    entries = [m for m in machines
               if m.get('state') in (None, 'active') and not m.get('hold')]
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
    """All chart scales and hypothetical cap counts are hub-owned, after board merge.

    Forecast red means observed running demand exceeds the draft cap, not a
    historical rejection. Unknown samples stay unknown in every preview.
    """
    view = deepcopy(view)
    view['fleet_slots'] = slots
    for row in view.get('lanes', {}).values():
        ticks = row.get('ticks')
        if not isinstance(ticks, dict):
            continue
        # A previously presented snapshot must not retain draft choices after
        # registry capacity becomes unknown.
        ticks.pop('previews', None)
        cells = ticks['cells']
        peak = max((c.get('running') or 0 for c in cells), default=0) or 1
        def visual(cell, cap, forecast=False):
            cell = dict(cell)
            running = cell.get('running')
            if forecast:
                cell.update(cap=cap, forecast=True)
            if running is None:
                cell.update(state='unknown', visual=dict(height=1, opacity=1))
                return cell
            if forecast:
                cell['state'] = ('rejecting' if cap is not None and running > cap else
                                 'full' if cap is not None and running >= cap and running else
                                 'partial' if running else 'empty')
            scale = cap if cap is not None and cap > 0 else peak
            ratio = min(1, running / scale)
            cell['visual'] = dict(height=(max(.15, ratio) if cell['state'] == 'partial' else
                                          .15 if cell['state'] == 'empty' else 1),
                                  opacity=.25 + .5 * ratio if cell['state'] == 'partial' else 1)
            return cell
        ticks['cells'] = [visual(c, c.get('cap')) for c in cells]
        if type(slots) is int and slots >= 0:
            previews = {}
            for cap in [*range(slots + 1), None]:
                previews['unlimited' if cap is None else str(cap)] = dict(
                    cells=[visual(c, cap, True) for c in cells], total=len(cells),
                    affected=sum(c.get('running') is not None and cap is not None and c['running'] > cap for c in cells),
                    unknown=sum(c.get('running') is None for c in cells),
                    now=row.get('status', {}).get('run'))
            ticks['previews'] = previews
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
        at, bt = a.get('ticks'), b.get('ticks')
        series = None
        if (at is not None and bt is not None and at['window'] == bt['window'] and
                len(at['cells']) == len(bt['cells'])):
            series = []
            for x, y in zip(at['cells'], bt['cells']):
                tick = dict(x)
                if x['t'] != y['t'] or x['running'] is None or y['running'] is None:
                    tick.update(state='unknown', running=None, rejected=None)
                else:
                    tick['running'] = x['running'] + y['running']
                    tick['rejected'] = (None if x['rejected'] is None or y['rejected'] is None else
                                        x['rejected'] + y['rejected'])
                    cap = x['cap'] if x['cap'] == y['cap'] else None
                    tick['cap'] = cap
                    tick['state'] = ('rejecting' if 'rejecting' in (x['state'], y['state']) else
                                     'full' if cap is not None and tick['running'] >= cap and tick['running'] else
                                     'partial' if tick['running'] else 'empty')
                    if tick['rejected'] is None:
                        tick['rejection_unknown'] = True
                series.append(tick)
        envelope = (dict(window=at['window'], bucket_s=at['bucket_s'],
                         cap=at['cap'] if at['cap'] == bt['cap'] else None, cells=series)
                    if series is not None else None)
        out[name] = dict(ticks=envelope, spend=spending)
    return out
