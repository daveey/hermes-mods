"""Fleet view: machines with a fixed number of slots and what fills them.

Gateway workers come from the Board machine registry plus live Board leases in
the local sync-shadow replicas; CI workers come from the collector inventory,
grouped into machines by runner name. Pure functions; callers do the I/O.
"""
import re
import sqlite3
from pathlib import Path

UNSEEN_MS = 10 * 60 * 1000
# The core refreshes the registry every 60 s; three missed refreshes means the Board is not answering.
REGISTRY_STALE_S = 180
# Labels every runner on a platform carries; they say nothing about the machine.
_PLATFORM = {'self-hosted', 'linux', 'x64', 'macos', 'arm64', 'windows'}
_SLOT_LABEL = re.compile(r'^ci-capacity-slot-\d+$')
_EPHEMERAL = re.compile(r'^(.+?)[-_]i-[0-9a-f]{8,}(?:-.*)?$')
_NUMBERED = re.compile(r'^(.+?)-(\d{1,3})$')


def live_leases(shadow_dir, now_ms):
    """Live leases from every board replica: [{board, task, title, holder, started_at}]."""
    out = []
    for path in sorted(Path(shadow_dir).glob('*.db')):
        try:
            db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        except sqlite3.Error:
            continue
        try:
            rows = db.execute(
                'SELECT id, title, lease_holder, started_at FROM tasks '
                'WHERE lease_holder IS NOT NULL AND lease_expires > ? ORDER BY started_at, id',
                (now_ms,)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            db.close()
        out += [dict(board=path.stem, task=t, title=title, holder=holder, started_at=started)
                for t, title, holder, started in rows]
    return out


def gateway_workers(registry, leases, now_ms, *, local_cap=None, registry_age_s=None):
    """One row per registered machine (and per unregistered lease holder).

    registry: ``{me, machines: {name: entry}}`` or None when the Board is unreachable.
    Slots are the registry's ``max_card_runs``; this gateway falls back to its own
    dispatcher cap. None means no fixed slot count is set. A registry older than
    REGISTRY_STALE_S keeps its slots and labels but says nothing about who is up.
    """
    stale = registry is not None and registry_age_s is not None and registry_age_s > REGISTRY_STALE_S
    machines = dict((registry or {}).get('machines') or {})
    me = (registry or {}).get('me')
    by_holder = {}
    for lease in leases:
        by_holder.setdefault(lease['holder'], []).append(lease)
    rows = []
    for name in sorted(set(machines) | set(by_holder)):
        entry = machines.get(name)
        running = by_holder.get(name, [])
        if entry is not None and entry.get('state') not in (None, 'active'):
            if not running:
                continue
        slots, slots_source = None, None
        if entry is not None and isinstance(entry.get('max_card_runs'), int):
            slots, slots_source = entry['max_card_runs'], 'registry'
        elif name == me and isinstance(local_cap, int):
            slots, slots_source = local_cap, 'local'
        seen = entry.get('last_seen') if entry else None
        if registry is None:
            state, reason = 'unknown', 'machine registry unreachable'
        elif stale:
            state, reason = 'unknown', f'Board not answering; registry read {int(registry_age_s / 60)} min ago'
        elif entry is None:
            state, reason = 'unknown', 'not in the machine registry'
        elif entry.get('hold'):
            state, reason = 'draining', 'on hold: finishes running cards, takes no new ones'
        elif not isinstance(seen, (int, float)):
            state, reason = 'unknown', 'never seen by the Board'
        elif now_ms - seen > UNSEEN_MS:
            # Asleep or off is normal for a laptop; it is a problem only while it holds cards.
            state = 'lost' if running else 'asleep'
            reason = f'not seen for {int((now_ms - seen) / 60000)} min'
        else:
            state, reason = 'active', None
        rows.append(dict(
            name=name, me=name == me, state=state, reason=reason, last_seen=seen,
            labels=list(entry.get('labels') or []) if entry else [],
            pinned_only=bool(entry and entry.get('pinned_only')),
            slots=slots, slots_source=slots_source,
            running=[dict(board=l['board'], task=l['task'], title=l['title'], started_at=l['started_at'])
                     for l in running]))
    return dict(state='unknown' if registry is None else ('stale' if stale else 'ready'), machines=rows)


def machine_of(display_name):
    """Group key for a GitHub runner: its machine, with the per-slot suffix removed."""
    name = display_name or '?'
    m = _EPHEMERAL.match(name)
    if m:
        return m.group(1), True
    m = _NUMBERED.match(name)
    return (m.group(1) if m else name), False


def _slot(row, fresh):
    if not fresh or row.get('freshness') != 'fresh':
        return 'unknown'
    if row.get('registration') == 'online':
        return 'busy' if row.get('activity') == 'busy' else 'idle'
    return 'offline' if row.get('registration') == 'offline' else 'unknown'


def ci_workers(inventory):
    """Group the collector inventory into machines; one GitHub runner = one slot.

    Deregistered (``missing``) runners are history, not slots: only their count is kept.
    """
    meta, data = inventory['meta'], inventory['data']
    fresh = meta.get('freshness') == 'fresh'
    groups, hidden = {}, 0
    for row in data.get('runners') or []:
        if row.get('registration') == 'missing':
            hidden += 1
            continue
        key, ephemeral = machine_of(row.get('display_name'))
        g = groups.setdefault(key, dict(name=key, ephemeral=ephemeral, slots=[], warnings=[]))
        g['slots'].append(dict(runner=row.get('display_name'), state=_slot(row, fresh),
                               labels=list(row.get('labels') or [])))
        if row.get('readiness') == 'fail' and row.get('registration') == 'online':
            failed = (row.get('readiness_evidence') or {}).get('failed') or []
            g['warnings'].append(f"{row.get('display_name')}: "
                                 + (', '.join(f'{c} failed' for c in failed) if failed else 'a check failed'))
    for g in groups.values():
        g['slots'].sort(key=lambda s: s['runner'] or '')
        common = set.intersection(*(set(s['labels']) for s in g['slots']))
        g['labels'] = sorted(l for l in common if l.lower() not in _PLATFORM and not _SLOT_LABEL.match(l))
    return dict(state='ready' if fresh else 'stale', observed_at=meta.get('observed_at'),
                machines=sorted(groups.values(), key=lambda g: (g['ephemeral'], g['name'])),
                hidden_deregistered=hidden)
