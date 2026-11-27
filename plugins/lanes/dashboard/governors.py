"""Read dispatcher receipts, never re-run admission in the presentation path.

Counts use complete governor counters, not capped card-id samples or overlaps.
All snapshots are local to this gateway; the device adapter is footnote-only.
"""
from collections import Counter, defaultdict
import json
import math
import time
from pathlib import Path


def project(snapshots, cards, registry, *, adapter=None, now=None):
    now = time.time() if now is None else now
    scope, groups = [], defaultdict(list)
    charges = {}
    idle = 0
    complete = True
    for snap in snapshots:
        fresh = bool(snap.get('available') and not snap.get('stale'))
        source = dict(source='kanban_governors.snapshot', gateway='hub', board=snap.get('board'),
                      age_s=snap.get('age_s'), available=fresh,
                      flow_available=snap.get('flow_available', True), truncated=bool(snap.get('truncated')))
        scope.append(source)
        reasons = []
        if not fresh:
            reasons.append(snap.get('reason') or ('stale dispatcher snapshot' if snap.get('stale') else 'no dispatcher snapshot'))
        if not snap.get('flow_available', True):
            reasons.append(snap.get('flow_reason') or 'flow unavailable')
        numeric = [r.get('cards') for r in snap.get('governors', [])]
        accounted = all(isinstance(n, int) for n in numeric) and snap.get('idle_total') is not None
        balanced = accounted and sum(numeric) == snap['idle_total']
        if snap.get('phase') == 'preclaim_aborted':
            reasons.append('tick aborted at ' + str(snap.get('global_stop') or 'unknown governor'))
        if fresh and not balanced:
            reasons.append('incomplete governor accounting' if not accounted else 'governor accounting mismatch')
        if reasons:
            source['reason'] = '; '.join(reasons)
        source['accounting_available'] = bool(fresh and balanced)
        complete &= fresh and balanced and snap.get('flow_available', True)
        if fresh:
            idle += snap.get('idle_total') or 0
        for row in snap.get('governors', []):
            groups[row['governor']].append((row, source, snap))
        if fresh:
            for tid, charge in snap.get('cards', {}).items():
                charges[snap.get('board'), tid] = charge
    lane_complete = complete and not any(s.get('truncated') for s in snapshots)
    incomplete_sources = [snap for snap, source in zip(snapshots, scope)
                          if not source['accounting_available'] or not source['flow_available'] or source['truncated']]
    incomplete_lanes = (None if any('open_lanes' not in s for s in incomplete_sources) else
                        sorted({name for s in incomplete_sources for name in s['open_lanes']}))
    rows = []
    by_lane = defaultdict(Counter)
    for card in cards:
        charge = charges.get((card.get('board'), card['id']))
        # Todo and scheduled charges are complete counters, not per-card entries.
        if charge is None:
            ident = {'todo': 'dependencies', 'scheduled': 'schedule'}.get(card.get('status'))
            if ident and any(s['board'] == card.get('board') and s['available'] for s in scope):
                charge = {'governor': ident}
        if charge:
            card['governor'] = charge['governor']
            card['governor_evidence'] = charge
            if card.get('reason') != 'you':
                card['reason'] = 'gov:' + charge['governor']
            by_lane[card['lane']][charge['governor']] += 1
    # A newer dispatcher may emit an id this producer's registry does not know.
    # Keep its complete charged counters, but never invent a healthy state/name.
    registered = {entry['id'] for entry in registry}
    unsupported = sorted(set(groups) - registered)
    entries = list(registry) + [dict(id=ident, label=f'Unknown governor ({ident})',
                                   kind='unknown', threshold='Unknown dispatcher governor')
                                for ident in unsupported]
    for entry in entries:
        ident = entry['id']
        observations = groups[ident]
        known = [(r, s, b) for r, s, b in observations if s['available']]
        # A receipt that did not observe this governor reports cards=None
        # (storage-admission abort): unknown, never a zero and never a crash.
        count = sum(r.get('cards') or 0 for r, _, _ in known)
        observed = [(r, s, b) for r, s, b in known
                    if r.get('cards') is not None and not r.get('unknown') and not any(
                        g.get('verdict') == 'unknown' for g in b.get('globals') or [] if g['governor'] == ident)]
        unknown = not observed
        partial = len(known) != len(observations) or len(observed) != len(known) or not complete
        # Prefer the largest charged gauge, then the freshest. A sibling's
        # 0/50 must not replace the 10/50 attached to the dominant held set.
        gauges = [r for r, s, _ in sorted(known, key=lambda item: (-(item[0].get('cards') or 0), item[1].get('age_s') or 0))
                  if r.get('reading') is not None]
        reading_row = gauges[0] if gauges else {}
        at_cap = any(g.get('verdict') == 'stop' for _, _, b in known
                     for g in b.get('globals') or [] if g['governor'] == ident)
        numeric_caps = {'lane_gate', 'lane_daily_limit', 'lane_max_workers', 'host_slots', 'max_spawn', 'profile_cap'}
        at_cap |= any(ident in numeric_caps and isinstance(r.get('reading'), (int, float)) and
                      isinstance(r.get('threshold'), (int, float)) and r['reading'] >= r['threshold']
                      for r, _, _ in known)
        unknown |= ident in unsupported
        state = 'unknown' if ident in unsupported else 'on' if count else 'unknown' if unknown else 'at_cap' if at_cap else 'off'
        readings = [dict(board=s['board'], lane=r.get('scope'), reading=r.get('reading'),
                         threshold=r.get('threshold'), detail=r.get('detail'), cards=r.get('cards', 0))
                    for r, s, _ in known if r.get('reading') is not None or r.get('cards')]
        lanes = [dict(lane=lane, count=counts[ident]) for lane, counts in by_lane.items() if counts[ident]]
        row_scope = []
        for source in scope:
            local = [(r, b) for r, s, b in observations if s is source]
            observed_here = source['available'] and bool(local) and all(
                r.get('cards') is not None and not r.get('unknown') for r, _ in local)
            reached = not any(g.get('verdict') == 'unknown' for _, b in local
                              for g in b.get('globals') or [] if g['governor'] == ident)
            row_source = {**source, 'available': bool(observed_here and reached)}
            if source['available'] and not row_source['available']:
                row_source['reason'] = '; '.join(filter(None, [source.get('reason'), 'governor not observed this tick']))
            row_scope.append(row_source)
        rows.append(dict(id=ident, name=entry['label'], kind=entry['kind'], state=state,
                         cards_charged=count, holding=[dict(board=s['board'], id=tid)
                         for r, s, _ in known for tid in r.get('card_ids', [])],
                         reading=reading_row.get('reading'), threshold=reading_row.get('threshold', entry['threshold']),
                         readings=readings, detail=reading_row.get('detail'), lanes=lanes,
                         since=min((r['since'] for r, _, _ in known if r.get('since')), default=None),
                         scope=row_scope, partial=bool(partial or unknown), clears=entry['threshold']))
    charged = sum(r['cards_charged'] for r in rows)
    if charged != idle or unsupported:
        complete = lane_complete = False
        if unsupported:
            incomplete_lanes = None
        # A board's incomplete census cannot invalidate another governor's
        # positive charge. Scope diagnostics retain the accounting failure.
    device = adapter or {}
    try:
        stamp = float(device.get('read_at') or 0)
        age = now - stamp if stamp and math.isfinite(stamp) else None
        cadence = float(device.get('cadence_s', 60))
        device_fresh = bool(device.get('available') and not device.get('stale') and
                            age is not None and 0 < cadence < float('inf') and -5 <= age <= 3 * cadence)
    except (TypeError, ValueError, OverflowError):
        age, device_fresh = None, False
    return dict(rows=rows, scope=scope, available=complete, lane_accounting_available=lane_complete,
                incomplete_lanes=incomplete_lanes,
                idle_total=idle if complete else None,
                known_idle_total=idle, charged_total=charged,
                counts={state: sum(r['state'] == state for r in rows)
                        for state in ('on', 'off', 'at_cap', 'unknown')},
                lane_reasons={lane: [dict(id=k, count=v) for k, v in counts.most_common()]
                              for lane, counts in by_lane.items()},
                device_footnote=dict(state='known' if device_fresh else 'unknown', age_s=age,
                                     label='Residual device-only cards on mini',
                                     status_counts=device.get('status_counts') if device_fresh else None))


def read_snapshot(*, conn=None, path=None, board=None):
    """Use the CLI reader without migrations, tracking registration or a flow deadline."""
    import sqlite3
    from hermes_cli import kanban_governors as gov, kanban_lockguard as guard
    owned = None
    try:
        if conn is None:
            owned = guard.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.25)
            owned.execute('PRAGMA query_only=ON')
            conn = owned
        return gov.snapshot(conn)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        return dict(board=board, available=False, stale=True, reason=str(exc), governors=[])
    finally:
        if owned is not None:
            owned.close()


def read_adapter(home):
    try:
        doc = json.loads((Path(home) / 'state/governors/mac-mini.json').read_text())
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None
