"""Hub-owned lane occupancy and spend; the renderer never sums usage receipts."""
import time

WINDOWS = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}


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
