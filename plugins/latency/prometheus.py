"""Prometheus projection of the retained (rolling 90-day) histogram store.

This is a reconstruction, not process-lifetime counters: retention/reconciliation
can decrease values. Sums use the stored log-bucket upper edge (<=0.5% error).
"""
from collections import defaultdict
import json
import math
import time

from .store import _edge, _stats


def label(value):
    return json.dumps(str(value), ensure_ascii=False)


def render(store, definitions, now=None):
    now = time.time() if now is None else now
    allowed = {d['id'] for d in definitions}
    groups, recent = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    lower = math.floor((now-86400)/3600)*3600
    with store._connection() as db:
        rows = db.execute('SELECT metric,host,hour,bucket,n FROM hist WHERE hour+3600>? AND hour<?', (now-90*86400,now)).fetchall()
    for metric, host, hour, bucket, n in rows:
        if metric not in allowed:
            continue
        groups[(metric,host)][bucket] += n
        if hour >= lower:
            recent[metric][bucket] += n
    lines = ['# HELP hermes_latency_seconds Retained rolling histogram; sum approximated by log-bucket upper edges (0.5% error).',
             '# TYPE hermes_latency_seconds histogram']
    for (metric,host), hist in sorted(groups.items()):
        labels = f'metric={label(metric)},host={label(host)}'
        count, total = 0, 0.0
        for bucket, n in sorted(hist.items()):
            edge = _edge(bucket)/1000
            count += n
            total += edge*n
            lines.append(f'hermes_latency_seconds_bucket{{{labels},le="{edge:.17g}"}} {count}')
        lines.extend([f'hermes_latency_seconds_bucket{{{labels},le="+Inf"}} {count}',
                      f'hermes_latency_seconds_sum{{{labels}}} {total:.17g}',
                      f'hermes_latency_seconds_count{{{labels}}} {count}'])
    lines.extend(['# HELP hermes_latency_p90_seconds Quantile of retained whole-hour samples in the last 24h.',
                  '# TYPE hermes_latency_p90_seconds gauge'])
    for metric, hist in sorted(recent.items()):
        lines.append(f'hermes_latency_p90_seconds{{metric={label(metric)},range="24h"}} {_stats(hist)["p90"]/1000:.17g}')
    return '\n'.join(lines)+'\n'
