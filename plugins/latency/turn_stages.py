"""Privacy-limited, coherent conversational traces for the Ledger stage bar.

Names are internal telemetry, not additional registry metrics. The send root
covers send-to-painted; its four children are point events. Wall clock skew is
not corrected or clamped: an inconsistent cross-host interval is unmeasured.
"""
import json

from . import otel

NAMES = ('send', 'hub_accepted', 'model_admitted', 'first_token', 'painted')
METRICS = frozenset('turn.' + name for name in NAMES)
LABELS = ('Send → hub', 'Hub → admitted', 'Admitted → first token', 'First token → painted')
MAX_TRACES = 5000
RETENTION = 3600


def valid(row):
    try:
        otel.identifier(row['trace_id'], 32)
        otel.identifier(row['span_id'], 16)
        if 'parent_span_id' in row:
            otel.identifier(row['parent_span_id'], 16)
        start = otel.nanos(row['start_time_unix_nano'])
        end = otel.nanos(row['end_time_unix_nano'])
        if row['metric'] == 'turn.send':
            return not row.get('parent_span_id') and end >= start
        return (row['metric'] in METRICS and start == end
                and bool(row.get('parent_span_id')) and row['parent_span_id'] != row['span_id'])
    except (KeyError, TypeError, ValueError):
        return False


def join(rows):
    """Join one root and its direct children. Missing/conflicting facts stay null.

    Exact duplicate rows are replay safe. Multiple roots or conflicting same-name
    children are ambiguous even when their timestamps happen to be equal.
    """
    unique = {json.dumps(r, sort_keys=True): r for r in rows if valid(r)}
    rows = list(unique.values())
    identities = {}
    for row in rows:
        key = (row['trace_id'], row['span_id'])
        identities[key] = identities.get(key, 0) + 1
    conflicts = {key for key, count in identities.items() if count > 1}
    roots = [r for r in rows if r['metric'] == 'turn.send' and not r.get('parent_span_id')]
    result: dict = dict(trace_id=None, root_span_id=None, end_to_end_ms=None,
                  accounted_ms=None, complete=False,
                  stages=[dict(name=label, dur_ms=None) for label in LABELS])
    if len(roots) != 1:
        return result
    root = roots[0]
    if (root['trace_id'], root['span_id']) in conflicts:
        return result
    start = int(root['start_time_unix_nano'])
    end = int(root['end_time_unix_nano'])
    result.update(trace_id=root['trace_id'], root_span_id=root['span_id'],
                  end_to_end_ms=(end-start)/1e6)
    points: list[int | None] = [start]
    for name in NAMES[1:]:
        children = [r for r in rows if r['metric'] == 'turn.'+name
                    and r['trace_id'] == root['trace_id']
                    and (r['trace_id'], r['span_id']) not in conflicts
                    and r.get('parent_span_id') == root['span_id']]
        at = int(children[0]['start_time_unix_nano']) if len(children) == 1 else None
        if at is not None and (not start <= at <= end or
                               children[0]['start_time_unix_nano'] != children[0]['end_time_unix_nano']):
            at = None
        if name == 'painted' and at != end:
            at = None
        points.append(at)
    for i, (a, b) in enumerate(zip(points, points[1:])):
        if a is not None and b is not None and b >= a:
            result['stages'][i]['dur_ms'] = (b-a)/1e6
    result['complete'] = all(s['dur_ms'] is not None for s in result['stages'])
    if result['complete']:
        result['accounted_ms'] = sum(s['dur_ms'] for s in result['stages'])
    return result


def ingest(store, samples, now):
    """Bounded separate projection; never inflate existing turn histograms."""
    admitted = [r for r in samples if r['metric'] in METRICS]
    inserted = 0
    with store._connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS turn_spans '
                   '(trace TEXT, span TEXT, start REAL, sample TEXT, PRIMARY KEY(trace,span,sample))')
        for row in admitted:
            if not valid(row):
                raise ValueError('invalid turn stage')
            inserted += db.execute('INSERT OR IGNORE INTO turn_spans VALUES (?,?,?,?)',
                                   (row['trace_id'], row['span_id'], row['start'], json.dumps(row, sort_keys=True))).rowcount
        db.execute('DELETE FROM turn_spans WHERE start<?', (now-RETENTION,))
        # Keep whole traces, not arbitrary stage rows. Five stages per trace in
        # ordinary operation; malformed/conflicting inputs also have a hard cap.
        db.execute('DELETE FROM turn_spans WHERE trace NOT IN '
                   '(SELECT trace FROM turn_spans GROUP BY trace ORDER BY MAX(start) DESC,trace LIMIT ?)', (MAX_TRACES,))
        db.execute('DELETE FROM turn_spans WHERE rowid NOT IN '
                   '(SELECT rowid FROM turn_spans ORDER BY start DESC,rowid DESC LIMIT ?)', (MAX_TRACES*10,))
    return inserted


def latest(store, since, until):
    with store._connection() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_spans'").fetchone()
        if not exists:
            return join([])
        # Select a root, then load ALL rows of that trace to detect ambiguity.
        roots = db.execute('SELECT trace,sample FROM turn_spans WHERE start>=? AND start<=? '
                           'ORDER BY start DESC,trace,span', (since, until))
        trace = next((t for t, raw in roots if json.loads(raw)['metric'] == 'turn.send'), None)
        if trace is None:
            return join([])
        rows = [json.loads(raw) for (raw,) in db.execute('SELECT sample FROM turn_spans WHERE trace=?', (trace,))]
    return join(rows)
