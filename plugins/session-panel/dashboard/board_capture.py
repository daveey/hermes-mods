"""Unactivated acquisition adapters: owned transactions, detached source parts.

Only explicit capture callers enter here. No scheduler, feed consumer, thread or
persistent SQL reader is installed by importing this module.
"""
from __future__ import annotations

from contextlib import closing, ExitStack
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from . import board_snapshot as snapshot
from .board_snapshot import (
    CardKey, Coverage, Membership, Receipt, SourcePart, SourceVersion, freeze, thaw,
)


@dataclass(frozen=True)
class CaptureHints:
    prior_version: SourceVersion
    owner_token: str
    observed_at: float
    deadline_s: float = 2.0
    variable_limit: int = 999

    def __post_init__(self):
        if not 0 < self.deadline_s <= 2.0 or not 32 <= self.variable_limit <= 999:
            raise ValueError('capture limits exceed the bounded acquisition contract')


def _key(source, card_id):
    return CardKey(source.gateway, source.profile, source.board, card_id)


def _reuse(previous, values):
    return {key: previous[key] if key in previous and previous[key] == value else freeze(value)
            for key, value in values.items()}


def _require_adapter(readers):
    """Legacy omitted-context still gates; explicit-home readers capture via home-local scope."""
    if getattr(readers, 'home_context', None) is not None:
        return
    require = getattr(readers, 'require_scope', None)
    if callable(require):
        require('snapshot_capture')


def _scope_for(readers, path):
    fn = getattr(readers, 'card_scope_for', None)
    if callable(fn) and getattr(readers, 'home_context', None) is not None:
        return fn(path)
    from hermes_cli.kanban_execution_observations import card_scope
    return card_scope(path)


def capture_local(readers, source, prior_part, demands, hints):
    """Capture exact frozen ScopeDemands on one caller-owned query-only snapshot.

    Source identity and BEFORE stamp are not execution truth. A failed/over-budget
    transaction raises; its caller retains cursor/failure custody in SnapshotStore.
    Membership is selected in a JSON-bound union even on unchanged cursor stamps.
    """
    _require_adapter(readers)
    from hermes_cli import kanban_task_change as changes
    from hermes_cli.kanban_execution_observations import add_cards
    from hermes_cli.kanban_session_acquisition import enrich_session_tasks
    from hermes_cli.kanban_session_outcome_tasks import select_listed_local_task_set
    from hermes_cli.kanban_session_activity import LISTED_STATUSES

    if source.producer != 'local':
        raise ValueError('canonical local capture requires a local source')
    demands = freeze(demands)
    path = Path(source.canonical_path)
    scope = _scope_for(readers, path)  # identity/config resolution precedes the SQL snapshot
    before = readers._stat(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    start = time.monotonic_ns()
    deadline = time.monotonic() + hints.deadline_s
    metrics = {'opens': 1, 'sql_calls': 0, 'vm_steps': 0, 'details_materialized': 0,
               'membership_pairs': 0, 'rows_returned': 0}
    def trace(_statement):
        metrics['sql_calls'] += 1
    def progress():
        metrics['vm_steps'] += 100
        return int(time.monotonic() >= deadline)
    lineage = tuple(dict.fromkeys(s for d in demands.values() for s in d.lineage))
    statuses = tuple(LISTED_STATUSES) + ('done', 'archived')
    previous = prior_part
    if previous and (previous.receipt.source != source
            or previous.receipt.version.store_epoch_uuid != hints.prior_version.store_epoch_uuid
            or previous.receipt.version.source_generation != hints.prior_version.source_generation):
        previous = None  # a new source generation cannot inherit old acquisition trust
    reusable = set()
    if previous:
        for sid in previous.receipt.coverage.completed_scopes:
            reusable.update(previous.scope_keys.get(sid, ()))
    with closing(sqlite3.connect(f'file:{path}?mode=rw', uri=True, timeout=0.5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, hints.variable_limit)
        conn.set_trace_callback(trace)
        conn.set_progress_handler(progress, 100)
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        changed, cursor = changes.changed_since(conn, previous.receipt.cursor if previous else None)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # All canonical columns are semantic: status-only digests lose reasons,
        # result changes, owner coordinates and execution refresh evidence.
        remote_rows = [dict(r) for r in conn.execute(
            'SELECT * FROM kanban_sync_remote_parents ORDER BY parent_id')] if 'kanban_sync_remote_parents' in tables else []
        remote = tuple(tuple(sorted(row.items())) for row in remote_rows)
        full = changed is changes.FULL or previous is None
        dirty = set(changed) if not full else set()
        if previous and previous.receipt.remote_parent_digest != remote:
            dirty.update(k.card_id for k in previous.details)
        if previous and not full:
            # Enrichment reads deeper than display hydration (including past a
            # running node). Invalidate descendants in one set query rather than
            # assuming the displayed depth/card-bounded detail graph is complete.
            dirty.update(row[0] for row in conn.execute(
                'WITH RECURSIVE affected(id) AS ('
                'SELECT value FROM json_each(?) UNION '
                'SELECT l.child_id FROM task_links l JOIN affected a ON l.parent_id=a.id) '
                'SELECT id FROM affected', (json.dumps(sorted(dirty)),)))
            # Child wait/enrichment/link_tasks also embeds explicit producer refs.
            reverse = {}
            for child, parents in previous.edges.items():
                for parent in parents:
                    reverse.setdefault(parent.card_id, set()).add(child.card_id)
            queue = list(dirty)
            while queue:
                for child in reverse.get(queue.pop(), ()):
                    if child not in dirty:
                        dirty.add(child)
                        queue.append(child)
        selected, pairs = select_listed_local_task_set(conn, lineage, statuses)
        metrics['membership_pairs'] = len(pairs)
        metrics['rows_returned'] += len(selected) + len(pairs) + len(remote)
        refresh = {tid: row for tid, row in selected.items()
                   if (full or tid in dirty or _key(source, tid) not in reusable
                       or _key(source, tid) not in (previous.rows if previous else {}))}
        enrich_session_tasks(conn, path, refresh, observed_at=hints.observed_at, scope=scope, replica=None)
        raw = {}
        for tid, row in selected.items():
            key = _key(source, tid)
            if tid in refresh:
                raw[key] = {**refresh[tid], 'board': source.board, 'key': f'{source.board}/{tid}'}
            else:
                raw[key] = thaw(previous.rows[key])
        memberships = tuple(Membership(_key(source, tid), sid, role) for (sid, tid), role in pairs.items())
        details, edges, per_scope = {}, {}, {}
        feed = readers.snapshot_feed_helpers()
        # Walk exactly the existing breadth-first depth/card-bound chain. Work is
        # shared by CardKey across all scopes, never hydrated once per chat.
        for sid, demand in demands.items():
            seeds = [tid for tid in selected if any((s, tid) in pairs for s in demand.lineage)
                     and selected[tid].get('status') in LISTED_STATUSES]
            seeds.extend(k.card_id for k in sorted(demand.refs)
                         if (k.gateway, k.profile, k.board) == (source.gateway, source.profile, source.board))
            queue = [(tid, selected.get(tid), 0) for tid in dict.fromkeys(seeds)]
            seen, reachable = set(), set()
            while queue and len(reachable) < feed.CHAIN_CARDS:
                tid, listed, depth = queue.pop(0)
                if tid in seen:
                    continue
                seen.add(tid)
                key = _key(source, tid)
                if key not in details:
                    if previous and not full and tid not in dirty and key in reusable and key in previous.details:
                        detail = previous.details[key]
                        if 'actionability' in detail or 'actionability_inputs' in detail:
                            # This unactivated acquisition capability cannot attest
                            # dirty coverage for every optional diagnostic input.
                            detail = {k: v for k, v in detail.items() if k not in ('actionability', 'actionability_inputs')}
                    else:
                        detail = readers._detail(conn, tid, source.board, False,
                                                 snapshot_at=hints.observed_at, parent_connection=None)
                        metrics['details_materialized'] += 1
                    if detail is None:
                        continue
                    # Enrichment is unsupported throughout this unactivated
                    # capability, including fresh captures; dropping it only on
                    # reuse would create a spurious revision on the next pass.
                    if 'actionability' in detail or 'actionability_inputs' in detail:
                        detail = {k: v for k, v in detail.items() if k not in ('actionability', 'actionability_inputs')}
                    details[key] = freeze(detail)
                    edges[key] = tuple(_key(source, p) for p in dict.fromkeys(
                        list(detail['links']['parents']) + feed.chain_refs(raw.get(key), thaw(detail))))
                detail = details[key]
                reachable.add(key)
                status = detail['task'].get('status')
                if depth >= feed.CHAIN_DEPTH or status in feed.TERMINAL or status == 'running':
                    continue
                for ref in feed.chain_refs(listed, thaw(detail)):
                    if ref not in seen:
                        queue.append((ref, None, depth + 1))
            # Membership includes done rows even though Feed only walks live rows.
            reachable.update(_key(source, tid) for tid in selected
                             if any((s, tid) in pairs for s in demand.lineage))
            per_scope[sid] = frozenset(reachable)
        conn.rollback()
    # Projection is canonical and detached, outside the SQL transaction. Cached
    # rows retain their original observation: reuse never renews an execution lease.
    projected = [raw[_key(source, tid)] for tid in refresh]
    add_cards(projected, now=hints.observed_at, gateway=scope[0], profile=scope[1])
    rows = _reuse(previous.rows if previous else {}, raw)
    details = _reuse(previous.details if previous else {}, details)
    metrics['full'] = full
    finish = time.monotonic_ns()
    if time.monotonic() > deadline:
        raise TimeoutError('local snapshot acquisition exceeded its budget')
    metrics['elapsed_ns'] = finish - start
    version = replace(hints.prior_version, source_revision=hints.prior_version.source_revision + 1)
    receipt = Receipt(source, version, before, cursor, remote,
                      Coverage(uuid4().hex, demands, frozenset(demands), frozenset()),
                      time.time_ns(), finish, start, finish, hints.owner_token)
    present = set().union(*per_scope.values())
    tombstones = frozenset(k for k in (previous.rows if previous else ()) if k not in present)
    # The canonical remote-parent table has no owner column. Preserve its exact
    # source coordinates rather than inventing the remote gateway from a bare ID.
    remote_parents = {(source, row['parent_id']): row for row in remote_rows}
    invalidated = (frozenset(previous.rows) | frozenset(previous.details) if previous and full
                   else frozenset(_key(source, tid) for tid in dirty))
    return SourcePart(receipt, rows, memberships, edges, remote_parents, details, tombstones,
                      scope_keys=per_scope, metrics=metrics, invalidated_keys=invalidated)


@dataclass(frozen=True)
class ExternalCapture:
    """One strict union read, bound to exact demands/owner/version before hydration.

    Private in-process acquisition input, not a source certificate or wire schema.
    Inventory and raw rows are detached and frozen together; neither may be read
    again during materialization or observation-only carriage.
    """
    source: object
    demands: object
    external_demands: object
    hints: CaptureHints
    before_stamp: object
    read_started_ns: int
    captured: object
    inventory: object

    def __post_init__(self):
        for name in ('demands', 'external_demands', 'before_stamp', 'captured', 'inventory'):
            object.__setattr__(self, name, freeze(getattr(self, name)))


def capture_external(readers, demands, refs, prior_parts, hints, *, raw=None):
    raw = capture_external_raw(readers, demands, refs, hints) if raw is None else raw
    source, hint = next(iter(hints.items()))
    if (raw.source != source or raw.demands != demands or raw.hints != hint):
        raise ValueError('strict external capture provenance does not match materialization')
    return materialize_external(readers, raw, prior_parts)


def capture_external_raw(readers, demands, refs, hints, *, previous_keys=None):
    """Strict canonical union input, certified only for its frozen demands.

    All strict adapters remain one external failure domain. This adapter never
    calls capture_local and never touches a Feed or SnapshotStore lock. ``hints``
    is a SourceKey -> CaptureHints map (one aggregate external source).
    """
    _require_adapter(readers)
    if len(hints) != 1:
        raise ValueError('canonical external adapters have one atomic failure domain')
    source, hint = next(iter(hints.items()))
    if source.producer == 'local':
        raise ValueError('external capture cannot own a local source')
    demands = freeze(demands)
    start = time.monotonic_ns()
    deadline = time.monotonic() + hint.deadline_s
    before = readers.external_stamps()
    held = {}
    local_boards = {board for board, _ in readers.boards()}
    external_demands = {}
    for sid, demand in demands.items():
        owned_refs = frozenset(demand.refs) | frozenset(refs.get(sid, ()))
        if owned_refs != demand.refs:
            raise ValueError('external refs must be frozen in the ScopeDemand')
        owned_refs = frozenset(key for key in owned_refs if not (
            key.gateway == source.gateway and key.profile == source.profile and key.board in local_boards))
        external_demands[sid] = replace(demand, refs=owned_refs)
        held[sid] = {'lineage': demand.lineage, 'refs': {key.card_id for key in owned_refs},
                     'bundle': {'tasks': [{'id': key.card_id, 'gateway': key.gateway,
                                          'profile': key.profile, 'board': key.board}
                                         for key in owned_refs]}}
    captured, inventory = readers.external_capture(held, include_inventory=True)
    if not isinstance(captured, dict) or set(captured) != set(demands):
        raise ValueError('incomplete canonical external acquisition')
    if any(not isinstance(encoded, (tuple, list)) for encoded in captured.values()):
        raise ValueError('invalid canonical external collection')
    inventory = dict(inventory)
    inventory['dependency_inputs'] = _dependency_inputs(
        readers, external_demands, hint, deadline, previous_keys or {}, captured)
    return ExternalCapture(source, demands, external_demands, hint, before, start, captured, inventory)


def _dependency_inputs(readers, demands, hint, deadline, previous_keys, captured):
    """Source-owned semantic fence for detached dependencies of strict members.

    Membership inventory excludes unlinked ancestors. Covered board revisions
    name the semantic task fields, events, runs, and links. Content comparison
    also permits a value reverted during an older in-flight capture to carry.
    This conservative union is acquired once per board, not once per chat, and
    never enriches details or promotes dependencies into chat membership.
    """
    boards = {}
    for sid, demand in demands.items():
        keys = demand.refs | frozenset(previous_keys.get(sid, ()))
        boards[sid] = {key.board for key in keys}
        for encoded in captured[sid]:
            row = json.loads(encoded)
            if row.get('board'):
                boards[sid].add(row['board'])
    facts = {}
    for board in sorted({board for values in boards.values() for board in values}):
        path = Path(readers._shadow_path(board))
        if not path.is_file():
            facts[board] = None
            continue
        with closing(sqlite3.connect(f'file:{path}?mode=rw', uri=True, timeout=.5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 100)
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            from hermes_cli import kanban_activity_revision as revisions
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            digest = hashlib.sha256()
            for table in revisions.SHADOW_TABLES:
                digest.update(table.encode())
                if table not in tables:
                    digest.update(b'absent')
                    continue
                excluded = revisions.BOOKKEEPING_COLUMNS.get(table, ())
                for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                    if time.monotonic() > deadline:
                        raise TimeoutError('external dependency attestation exceeded its budget')
                    if table == 'task_events' and row['kind'] in revisions.UNREAD_EVENT_KINDS:
                        continue
                    value = {key: row[key] for key in row.keys() if key not in excluded}
                    digest.update(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
                    digest.update(b'\n')
            ident = path.stat()
            facts[board] = (ident.st_dev, ident.st_ino, digest.hexdigest())
            conn.rollback()
    return {sid: {board: facts[board] for board in values} for sid, values in boards.items()}


def materialize_external(readers, raw, prior_parts):
    _require_adapter(readers)
    source, hint, demands = raw.source, raw.hints, raw.demands
    captured, inventory = raw.captured, thaw(raw.inventory)
    external_demands = raw.external_demands
    start, before = raw.read_started_ns, raw.before_stamp
    deadline = start / 1e9 + hint.deadline_s
    rows, memberships, scope_keys, scoped_rows, decoded, scoped_memberships = {}, set(), {}, {}, {}, {}
    for sid, encoded in captured.items():
        if not isinstance(encoded, (tuple, list)):
            raise ValueError('invalid canonical external collection')
        reachable = set()
        scoped_rows[sid] = {}
        for encoded_row in encoded:
            if encoded_row not in decoded:
                decoded[encoded_row] = freeze(json.loads(encoded_row))
            row = decoded[encoded_row]
            if not isinstance(row, snapshot.Mapping) or not row.get('id') or not row.get('gateway') or not row.get('board'):
                raise ValueError('unqualified canonical external row')
            key = CardKey(row['gateway'], row.get('owner_profile', row.get('profile')) or source.profile,
                          row['board'], row['id'])
            rows.setdefault(key, row)
            scoped_rows[sid].setdefault(key, row)
            reachable.add(key)
        scope_keys[sid] = frozenset(reachable)
        scoped_rows[sid] = readers.snapshot_select_external(
            inventory, demands[sid].lineage, observed_at=hint.observed_at)
        members = []
        for row in scoped_rows[sid]['done']:
            key = CardKey(row['gateway'], row.get('owner_profile', row.get('profile')) or source.profile,
                          row['board'], row['id'])
            binding = tuple((name, row.get(name)) for name in (
                'origin_gateway', 'origin_profile', 'origin_session', 'binding_id', 'session', 'resolved_session'))
            for session in demands[sid].lineage:
                if session in (row.get('session'), row.get('origin_session'), row.get('resolved_session')):
                    member = Membership(key, session, row.get('via', 'shepherd'), binding)
                    if member not in members:
                        members.append(member)
                    memberships.add(member)
        scoped_memberships[sid] = tuple(members)
    prior = prior_parts.get(source)
    details, edges, unknown = _external_closure(readers, rows, scope_keys, external_demands, hint, deadline)
    for sid, keys in scope_keys.items():
        scope_keys[sid] = frozenset(keys)
    receipt = Receipt(source, replace(hint.prior_version, source_revision=hint.prior_version.source_revision + 1),
                      before, None, None,
                      Coverage(uuid4().hex, demands, frozenset(demands) - unknown, unknown),
                      time.time_ns(), time.monotonic_ns(), start, time.monotonic_ns(), hint.owner_token)
    return (SourcePart(receipt, rows=_reuse(prior.rows if prior else {}, rows),
                       memberships=tuple(sorted(memberships, key=lambda m: (m.card, m.session, m.role))),
                       details=details, edges=edges, scope_keys=scope_keys, scope_rows=scoped_rows,
                       scope_memberships=scoped_memberships,
                       metrics={'union_captures': 1}),)


def _external_closure(readers, rows, scope_keys, demands, hints, deadline):
    """Capture remote details without an ID-only local/default-board fallback.

    A missing shadow/detail is explicitly unknown, not an attestation of empty
    dependency closure. Canonical strict row acquisition and these per-board
    snapshots are separate observations, not falsely labelled one transaction.
    """
    feed = readers.snapshot_feed_helpers()
    details, edges, unknown, conns = {}, {}, set(), {}
    with ExitStack() as stack:
        for sid, demand in demands.items():
            seeds = set(scope_keys[sid]) | set(demand.refs)
            queue = [(key, rows.get(key), 0) for key in sorted(seeds)]
            seen, reachable = set(), set(scope_keys[sid])
            hydrated = 0
            while queue and hydrated < feed.CHAIN_CARDS:
                key, row, depth = queue.pop(0)
                if key in seen:
                    continue
                seen.add(key)
                if key not in details:
                    # shadow_path(board) is the canonical shared/default-profile
                    # replica. Profile-owned boards have a different authority;
                    # gateway equality alone cannot attest their detail identity.
                    if key.profile != 'default':
                        unknown.add(sid)
                        continue
                    path = Path(readers._shadow_path(key.board))
                    if not path.is_file():
                        unknown.add(sid)
                        continue
                    if path not in conns:
                        conn = stack.enter_context(closing(sqlite3.connect(
                            f'file:{path}?mode=rw', uri=True, timeout=0.5)))
                        conn.row_factory = sqlite3.Row
                        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, hints.variable_limit)
                        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 100)
                        conn.execute('PRAGMA query_only=ON')
                        conn.execute('BEGIN')
                        stack.callback(conn.rollback)
                        conns[path] = conn
                    conn = conns[path]
                    # Never hydrate an identically named card from another owner.
                    owner = conn.execute('SELECT created_on FROM tasks WHERE id=?', (key.card_id,)).fetchone()
                    if owner is None or owner[0] != key.gateway:
                        unknown.add(sid)
                        continue
                    detail = readers._detail(conn, key.card_id, key.board, True,
                                             snapshot_at=hints.observed_at, parent_connection=conn)
                    if detail is None:
                        unknown.add(sid)
                        continue
                    details[key] = freeze(detail)
                    edges[key] = tuple(replace(key, card_id=pid) for pid in detail['links']['parents'])
                reachable.add(key)
                hydrated += 1
                status = details[key]['task'].get('status')
                if depth < feed.CHAIN_DEPTH and status not in feed.TERMINAL and status != 'running':
                    queue.extend((replace(key, card_id=ref), None, depth + 1)
                                 for ref in feed.chain_refs(row, thaw(details[key]))
                                 if replace(key, card_id=ref) not in seen)
            scope_keys[sid] = frozenset(reachable)
    if time.monotonic() > deadline:
        raise TimeoutError('external detail acquisition exceeded its budget')
    return details, edges, frozenset(unknown)
