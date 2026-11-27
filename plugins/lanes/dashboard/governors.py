"""Read dispatcher receipts, never re-run admission in the presentation path.

Counts use complete governor counters, not capped card-id samples or overlaps.
All snapshots are local to this gateway; the device adapter is footnote-only.
"""
from collections import Counter, defaultdict
import json
import math
import time
from pathlib import Path


# Presentation grouping only; the dispatcher registry is unchanged (42 ids).
_G = {
    'wait': ('dependencies', 'schedule'),
    'reorder': ('board_share', 'lane_share', 'ratio_routing'),
    'lane_dial': ('lane_gate', 'lane_paused', 'lane_max_workers', 'lane_daily_limit'),
    'fleet_dial': ('host_slots', 'max_spawn', 'profile_cap', 'llm_pool', 'admission_pool'),
    'budget': ('spend_budget', 'no_progress', 'respawn_guard', 'spawn_failed'),
    'safety': ('gateway_drain', 'core_update_drain', 'update_fence', 'quiet', 'memory_pressure', 'disk_floor',
               'hub_cpu', 'worker_resources', 'storage_admission', 'provider_walls', 'class_walls',
               'review_provider_wall', 'owner_down', 'board_lease', 'containment', 'hub_claim_screen', 'start_gate'),
    'fault': ('unassigned', 'nonspawnable', 'model_pin', 'capsule', 'placement', 'refusal_fallback', 'unrecorded'),
}
GROUPS = {ident: group for group, ids in _G.items() for ident in ids}
DIALS = ('lane_dial', 'fleet_dial')
NEEDS_YOU = ('budget', 'fault')


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
        # State is finalized from board-qualified scope below; a positive
        # charge is known-on, but one board's zero cannot certify fleet-off.
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
        unknown |= any(not source['available'] for source in row_scope)
        state = 'unknown' if ident in unsupported else 'on' if count else 'unknown' if unknown else 'at_cap' if at_cap else 'off'
        rows.append(dict(id=ident, name=entry['label'], kind=entry['kind'], state=state,
                         cards_charged=count, holding=[dict(board=s['board'], id=tid)
                         for r, s, _ in known for tid in r.get('card_ids', [])],
                         reading=reading_row.get('reading'), threshold=reading_row.get('threshold', entry['threshold']),
                         readings=readings, detail=reading_row.get('detail'), lanes=lanes,
                         since=min((r['since'] for r, _, _ in known if r.get('since')), default=None),
                         scope=row_scope, partial=bool(partial or unknown), clears=entry['threshold']))
    charged = sum(r['cards_charged'] for r in rows)
    # Per-lane holds. A lane dial's receipt (scope = lane) is a complete counter; card charges are the
    # only evidence for the rest and are fenced by incomplete_lanes in the view, never extended here.
    by_id = {r['id']: r for r in rows}
    scoped = defaultdict(list)
    for ident, observations in groups.items():
        if GROUPS.get(ident) != 'lane_dial':
            continue
        for r, source, snap in observations:
            if source['available'] and r.get('scope') and r.get('cards') is not None and not r.get('unknown') and (
                    r.get('cards') or r.get('reading') is not None):
                scoped[r['scope'], ident].append((r, source))
    lane_counts = defaultdict(dict)
    for lane, counts in by_lane.items():
        for ident, n in counts.items():
            lane_counts[lane][ident] = n
    for (lane, ident), items in scoped.items():
        lane_counts[lane][ident] = sum(r.get('cards') or 0 for r, _ in items)
    holds, lane_dial = {}, {}
    for lane, counts in lane_counts.items():
        chips = []
        for ident, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            group, row = GROUPS.get(ident), by_id.get(ident)
            if not n or row is None:
                continue
            if group in DIALS:
                lane_dial[lane] = lane_dial.get(lane, 0) + n
            if group not in ('lane_dial', 'budget', 'fault'):
                continue
            items = sorted(scoped.get((lane, ident), []),
                           key=lambda it: (-(it[0].get('cards') or 0), it[1].get('age_s') or 0))
            gauge = next((r for r, _ in items if r.get('reading') is not None), {})
            chips.append(dict(id=ident, name=row['name'], count=n, reading=gauge.get('reading'),
                              threshold=gauge.get('threshold', row['clears']), group=group, clears=row['clears'],
                              needs_you=group in NEEDS_YOU, detail=gauge.get('detail'),
                              readings=[dict(board=s['board'], lane=r.get('scope'), reading=r.get('reading'),
                                             threshold=r.get('threshold'), detail=r.get('detail'),
                                             cards=r.get('cards', 0)) for r, s in items]))
        # Acceptance 5: every fault governor is one red "can't start" chip, never one chip per cause.
        faults = [c for c in chips if c['group'] == 'fault']
        if faults:
            merged = dict(faults[0], id='fault', name="can't start", count=sum(c['count'] for c in faults),
                          reading=None, detail=None, readings=[],
                          members=[dict(id=c['id'], name=c['name'], count=c['count']) for c in faults])
            chips = [merged] + [c for c in chips if c['group'] != 'fault']
        # Priority: red fault > budget > lane dial; the renderer keeps this order and never reorders by count.
        order = {'fault': 0, 'lane_dial': 1, 'budget': 2}
        chips.sort(key=lambda c: (order.get(c['group'], 3), -c['count'], c['id']))
        if chips:
            holds[lane] = chips
    stops = {ident for ident, observations in groups.items() for r, s, b in observations if s['available']
             for g in b.get('globals') or [] if g.get('governor') == ident and g.get('verdict') == 'stop'}
    fleet = []
    for row in rows:
        group = GROUPS.get(row['id'])
        stop = row['id'] in stops
        if group in ('fleet_dial', 'safety') and (row['cards_charged'] or (group == 'safety' and stop)):
            fleet.append(dict(id=row['id'], name=row['name'], count=row['cards_charged'], reading=row['reading'],
                              threshold=row['threshold'], group=group, clears=row['clears'], stop=stop,
                              readings=row['readings'], detail=row['detail'], since=row['since']))
    fleet.sort(key=lambda f: (f['group'] != 'safety', not f['stop'], -f['count'], f['id']))
    stale_sources = [dict(board=s['board'], reason=s.get('reason') or (
                         'governor receipts truncated' if s.get('truncated') else 'stale dispatcher snapshot'))
                     for s in scope
                     if not s['available'] or not s['accounting_available'] or not s['flow_available'] or s['truncated']]
    if unsupported:
        stale_sources.append(dict(board='dispatcher', reason='unsupported governor id: ' + ', '.join(unsupported)))
    held_total = sum(r['cards_charged'] for r in rows if GROUPS.get(r['id']) in DIALS)
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
                holds=holds, fleet=fleet, stale_sources=stale_sources, held_total=held_total,
                fleet_stop=any(f['group'] == 'safety' and f['stop'] for f in fleet), lane_dial=lane_dial,
                lane_reasons={lane: [dict(id=k, count=v) for k, v in counts.most_common()]
                              for lane, counts in by_lane.items()},
                device_footnote=dict(state='known' if device_fresh else 'unknown', age_s=age,
                                     label='Residual device-only cards on mini',
                                     status_counts=device.get('status_counts') if device_fresh else None))


def read_snapshot(*, conn=None, path=None, board=None):
    """Use the CLI reader without migrations, tracking registration or a flow deadline."""
    import sqlite3
    from hermes_cli import kanban_governors as gov
    try:
        if conn is None:
            if path is None:
                raise RuntimeError('governor sidecar requires a database path')
            filename = str(Path(path).resolve())
            # snapshot only needs the database filename, not SQLite access.
            # Even a read-only tracked open can fail on registry contention.
            # Keep the CLI's decoding, freshness and registry normalization.
            class SidecarPath:
                def execute(self, query):
                    if query != 'PRAGMA database_list':
                        raise RuntimeError('governor sidecar reader cannot query SQLite')
                    return [(0, 'main', filename)]
            conn = SidecarPath()
        return gov.snapshot(conn)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        return dict(board=board, available=False, stale=True, reason=str(exc), governors=[])


def read_adapter(home):
    try:
        doc = json.loads((Path(home) / 'state/governors/mac-mini.json').read_text())
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None
