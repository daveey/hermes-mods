"""Detached board-snapshot/1 values and scope-certified, memory-only custody.

This module owns no threads, readers, connections, clocks used for execution
truth, or feed activation. Capture owners freeze demands before I/O; the store
accepts only still-current demand identities. A cursor is never coverage.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from threading import RLock
from types import MappingProxyType
import time
from uuid import uuid4
from typing import Any


class FrozenMap(Mapping):
    """Owned deep-frozen mapping; unlike an arbitrary proxy it has no live backing alias."""
    __slots__ = ('_data',)

    def __init__(self, value=()):
        object.__setattr__(self, '_data', MappingProxyType({freeze(k): freeze(v) for k, v in dict(value).items()}))

    def __setattr__(self, name, value):
        raise TypeError('immutable mapping')

    def __delattr__(self, name):
        raise TypeError('immutable mapping')

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def freeze(value: Any) -> Any:
    if type(value) is FrozenMap:
        return value
    if isinstance(value, Mapping):
        return FrozenMap(value)
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(v) for v in value)
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    if hasattr(value, '__dataclass_fields__') and value.__dataclass_params__.frozen:
        _detached(value)
        return value
    raise TypeError(f'not a detached snapshot value: {type(value).__name__}')


def _detached(value):
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return
    if type(value) is FrozenMap:
        return
    if isinstance(value, MappingProxyType):
        raise TypeError('unowned mapping proxy inside frozen value')
    if isinstance(value, (tuple, frozenset)):
        for item in value:
            _detached(item)
        return
    if hasattr(value, '__dataclass_fields__') and value.__dataclass_params__.frozen:
        for item in fields(value):
            _detached(getattr(value, item.name))
        return
    raise TypeError(f'mutable nested snapshot value: {type(value).__name__}')


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    if isinstance(value, frozenset):
        return {thaw(v) for v in value}
    return value


def empty():
    return FrozenMap()


@dataclass(frozen=True, order=True)
class SourceKey:
    gateway: str
    profile: str
    board: str
    producer: str
    canonical_path: str


@dataclass(frozen=True, order=True)
class CardKey:
    gateway: str
    profile: str
    board: str
    card_id: str


@dataclass(frozen=True, order=True)
class ChatKey:
    serving_gateway: str
    profile: str
    session_id: str


@dataclass(frozen=True)
class Watch:
    chat: ChatKey
    opaque_watch_token: str
    lineage_tuple: tuple
    lineage_generation: int

    def __post_init__(self):
        object.__setattr__(self, 'lineage_tuple', tuple(self.lineage_tuple))


@dataclass(frozen=True, order=True)
class SourceVersion:
    store_epoch_uuid: str
    source_generation: int
    source_revision: int


@dataclass(frozen=True, order=True)
class ScopeId:
    chat: ChatKey
    opaque_watch_token: str
    lineage_generation: int
    refs_generation: int


@dataclass(frozen=True)
class ScopeDemand:
    id: ScopeId
    lineage: tuple
    refs: frozenset = frozenset()
    selector_schema: str = 'board-scope/1'

    def __post_init__(self):
        object.__setattr__(self, 'lineage', tuple(self.lineage))
        object.__setattr__(self, 'refs', frozenset(self.refs))
        if self.selector_schema != 'board-scope/1':
            raise ValueError('unsupported scope selector')


@dataclass(frozen=True)
class Coverage:
    capture_id: str
    captured_demands: Mapping
    completed_scopes: frozenset
    unknown_scopes: frozenset

    def __post_init__(self):
        object.__setattr__(self, 'captured_demands', freeze(self.captured_demands))
        object.__setattr__(self, 'completed_scopes', frozenset(self.completed_scopes))
        object.__setattr__(self, 'unknown_scopes', frozenset(self.unknown_scopes))
        if (not self.completed_scopes.isdisjoint(self.unknown_scopes)
                or self.completed_scopes | self.unknown_scopes != self.captured_demands.keys()):
            raise ValueError('every captured demand needs exactly one disposition')
        if any(k != v.id for k, v in self.captured_demands.items()):
            raise ValueError('scope identity mismatch')


@dataclass(frozen=True)
class Receipt:
    source: SourceKey
    version: SourceVersion
    before_stamp: object
    cursor: object
    remote_parent_digest: object
    coverage: Coverage
    received_wall_ns: int
    received_mono_ns: int
    read_started_ns: int
    read_finished_ns: int
    owner_token: str

    def __post_init__(self):
        for name in ('before_stamp', 'cursor', 'remote_parent_digest'):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class Membership:
    card: CardKey
    session: str
    role: str
    binding_coordinates: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'binding_coordinates', freeze(self.binding_coordinates))


@dataclass(frozen=True)
class SourcePart:
    receipt: Receipt
    rows: Mapping = field(default_factory=empty)
    memberships: tuple = ()
    edges: Mapping = field(default_factory=empty)
    remote_parents: Mapping = field(default_factory=empty)
    details: Mapping = field(default_factory=empty)
    tombstones: frozenset = frozenset()
    errors: tuple = ()
    observations: Mapping = field(default_factory=empty)
    # Exact reachable keys and receipt per scope, including authoritative empties.
    scope_keys: Mapping = field(default_factory=empty)
    scope_receipts: Mapping = field(default_factory=empty)
    metrics: Mapping = field(default_factory=empty)
    stale_scope_keys: Mapping = field(default_factory=empty)
    scope_memberships: Mapping = field(default_factory=empty)
    scope_rows: Mapping = field(default_factory=empty)
    # Cursor-observed changes outside the captured membership still revoke stale
    # carried certificates; not tombstones and never delete last-known payload.
    invalidated_keys: frozenset = frozenset()

    def __post_init__(self):
        for name in ('rows', 'edges', 'remote_parents', 'details', 'observations',
                     'scope_keys', 'scope_receipts', 'metrics', 'stale_scope_keys', 'scope_memberships', 'scope_rows'):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        for name in ('memberships', 'errors'):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, 'tombstones', frozenset(self.tombstones))
        object.__setattr__(self, 'invalidated_keys', frozenset(self.invalidated_keys))


@dataclass(frozen=True)
class Snapshot:
    store_epoch: str
    store_sequence: int
    parts: Mapping
    watches: Mapping
    reverse_refs: Mapping
    reverse_membership: Mapping
    failures: Mapping = field(default_factory=empty)
    demands: Mapping = field(default_factory=empty)
    schema: str = 'board-snapshot/1'

    def __post_init__(self):
        for name in ('parts', 'watches', 'reverse_refs', 'reverse_membership', 'failures', 'demands'):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class Admission:
    id: str
    chat: ChatKey
    watch_token: str
    lineage_generation: int
    first_admitted_mono_ns: int
    first_admitted_wall_ns: int
    required_versions: Mapping
    receipt_ids: tuple
    reasons: frozenset
    dirty_keys: frozenset
    required_scopes: Mapping
    missing_scopes: Mapping
    required_failures: Mapping = field(default_factory=empty)
    missing_versions: Mapping = field(default_factory=empty)

    def __post_init__(self):
        for name in ('required_versions', 'receipt_ids', 'reasons', 'dirty_keys',
                     'required_scopes', 'missing_scopes', 'required_failures', 'missing_versions'):
            object.__setattr__(self, name, freeze(getattr(self, name)))


class StaleCapture(ValueError):
    """Wrong epoch/owner/version: caller must not advance its cursor."""


class CoverageUnavailable(RuntimeError):
    """Known missing/error scope; authoritative empty cannot be returned."""


class SnapshotCapacity(RuntimeError):
    """Explicit custody refusal; no cursor or certificate was advanced."""


def rehome(value) -> Any:
    """Rebind compatible detached v1 values across content-qualified modules.

    Same-schema module generations have distinct dataclass identity. Copying
    their keys verbatim silently orphans demands. Validate exact field layouts;
    never migrate a different schema or accept a mutable/unknown object.
    """
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return freeze({rehome(k): rehome(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(rehome(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(rehome(v) for v in value)
    allowed = {cls.__name__: cls for cls in (SourceKey, CardKey, ChatKey, Watch,
        SourceVersion, ScopeId, ScopeDemand, Coverage, Receipt, Membership, SourcePart,
        Snapshot, Admission)}
    target = allowed.get(type(value).__name__)
    if target is None or not hasattr(value, '__dataclass_fields__'):
        raise StaleCapture('incompatible detached transfer value: ' + type(value).__name__)
    if tuple(f.name for f in fields(value)) != tuple(f.name for f in fields(target)):
        raise StaleCapture('incompatible transfer field layout: ' + target.__name__)
    if type(value) is target:
        return freeze(value)
    return target(**{f.name: rehome(getattr(value, f.name)) for f in fields(target)})


class SnapshotStore:
    _schema = 'board-snapshot/1'

    def __init__(self, *, max_watches=400, max_sources=64, max_tokens=65536, max_pending_receipts=4096):
        if min(max_watches, max_sources, max_tokens, max_pending_receipts) < 1:
            raise ValueError('snapshot capacities must be positive')
        self._limits = (max_watches, max_sources, max_tokens, max_pending_receipts)
        self.epoch = uuid4().hex
        self._lock = RLock()
        self._sequence = 0
        self._closed = False
        self._parts = {}
        self._watches = {}
        self._refs = {}
        self._refs_gen = {}
        self._versions = {}
        self._owners = {}
        self._failures = {}
        self._admissions = {}
        self._used_tokens = set()

    def _live(self):
        if self._closed:
            raise StaleCapture('snapshot owner is stopped')

    def add_source(self, source):
        with self._lock:
            self._live()
            if source not in self._versions:
                if len(self._versions) >= self._limits[1]:
                    raise SnapshotCapacity('source capacity reached')
                self._versions[source] = SourceVersion(self.epoch, 0, 0)
                self._owners[source] = uuid4().hex
                for chat in self._watches:
                    self._admit(chat, 'scope_missing')

    def owner_token(self, source):
        with self._lock:
            return self._owners[source]

    def version(self, source):
        with self._lock:
            return self._versions[source]

    def register(self, watches):
        with self._lock:
            self._live()
            watches = tuple(watches)
            if len(set(self._watches) | {w.chat for w in watches}) > self._limits[0]:
                raise SnapshotCapacity('watch capacity reached')
            new_chats = {w.chat for w in watches} - self._watches.keys()
            if len(self._used_tokens) + len(new_chats) > self._limits[2]:
                raise SnapshotCapacity('watch-token capacity reached; replace the stopped owner epoch')
            dirty = set()
            for proposed in watches:
                chat = proposed.chat
                old = self._watches.get(chat)
                if old and proposed.opaque_watch_token not in ('', old.opaque_watch_token):
                    raise ValueError('forget the old watch before replacement')
                if old and old.lineage_tuple == proposed.lineage_tuple:
                    continue
                if old:
                    watch = replace(old, lineage_tuple=proposed.lineage_tuple,
                                    lineage_generation=old.lineage_generation + 1)
                else:
                    token = proposed.opaque_watch_token or uuid4().hex
                    if token in self._used_tokens:
                        raise ValueError('watch tokens cannot be reused')
                    self._used_tokens.add(token)
                    watch = replace(proposed, opaque_watch_token=token, lineage_generation=0)
                    self._refs[chat], self._refs_gen[chat] = frozenset(), 0
                self._watches[chat] = watch
                self._retire_obsolete(chat)
                self._admit(chat, 'scope_missing')
                dirty.add(chat)
            return frozenset(dirty)

    def require(self, chat, refs):
        with self._lock:
            self._live()
            refs = frozenset(refs)
            if len(refs) > 4096:
                raise SnapshotCapacity('dependency reference capacity reached')
            if self._refs[chat] == refs:
                return False
            self._refs[chat] = refs
            self._refs_gen[chat] += 1
            self._retire_obsolete(chat)
            self._admit(chat, 'scope_missing')
            return True

    def _demand(self, chat):
        w = self._watches[chat]
        return ScopeDemand(ScopeId(chat, w.opaque_watch_token, w.lineage_generation,
                                   self._refs_gen[chat]), w.lineage_tuple, self._refs[chat])

    def demand(self, source, chat):
        with self._lock:
            if source not in self._versions:
                raise KeyError(source)
            return self._demand(chat)

    def demands(self, source):
        with self._lock:
            self._live()
            if source not in self._versions:
                raise KeyError(source)
            return freeze({self._demand(c).id: self._demand(c) for c in self._watches})

    def _covered(self, source, demand):
        part = self._parts.get(source)
        receipt = part.scope_receipts.get(demand.id) if part else None
        return (receipt is not None and receipt.coverage.captured_demands.get(demand.id) == demand
                and demand.id in receipt.coverage.completed_scopes
                and (source, receipt.version.source_generation, demand.id) not in self._failures)

    def pending(self, source):
        with self._lock:
            return frozenset(d.id for d in self.demands(source).values() if not self._covered(source, d))

    def _retire_obsolete(self, chat):
        current = self._demand(chat).id
        self._failures = {k: v for k, v in self._failures.items() if k[2].chat != chat or k[2] == current}
        # Old coverage is removed, not inherited by a new token/generation.
        for source, part in list(self._parts.items()):
            receipts = {k: v for k, v in part.scope_receipts.items() if k.chat != chat or k == current}
            stale = {k: v for k, v in part.stale_scope_keys.items() if k.chat != chat}
            previous = set().union(*(v for k, v in part.scope_keys.items() if k.chat == chat))
            previous.update(*(v for k, v in part.stale_scope_keys.items() if k.chat == chat))
            if previous:
                stale[current] = frozenset(previous)
            keys = {k: v for k, v in part.scope_keys.items() if k in receipts}
            members = {k: v for k, v in part.scope_memberships.items() if k in receipts}
            self._parts[source] = self._prune(replace(part, scope_receipts=receipts, scope_keys=keys,
                stale_scope_keys=stale, scope_memberships=members,
                scope_rows={k: v for k, v in part.scope_rows.items() if k in receipts}))

    @staticmethod
    def _prune(part):
        used = set().union(*part.scope_keys.values(), *part.stale_scope_keys.values())
        return replace(part, rows={k: v for k, v in part.rows.items() if k in used},
                       details={k: v for k, v in part.details.items() if k in used},
                       edges={k: v for k, v in part.edges.items() if k in used},
                       memberships=tuple(dict.fromkeys(m for group in part.scope_memberships.values()
                                                       for m in group if m.card in used)),
                       remote_parents=part.remote_parents if used else {},
                       observations=part.observations if used else {})

    def _admit(self, chat, reason, receipt=None, dirty=frozenset()):
        watch = self._watches[chat]
        old = self._admissions.get(chat)
        if old and old.watch_token != watch.opaque_watch_token:
            old = None
        scopes = {s: self._demand(chat) for s in self._versions}
        versions, missing = {}, {}
        for source, demand in scopes.items():
            if self._covered(source, demand):
                versions[source] = self._parts[source].scope_receipts[demand.id].version
            else:
                missing[source] = demand
        ids = old.receipt_ids if old else ()
        if receipt and receipt.coverage.capture_id not in ids:
            ids += (receipt.coverage.capture_id,)
        self._admissions[chat] = Admission(
            old.id if old else uuid4().hex, chat, watch.opaque_watch_token, watch.lineage_generation,
            old.first_admitted_mono_ns if old else time.monotonic_ns(),
            old.first_admitted_wall_ns if old else time.time_ns(), freeze(versions), ids,
            (old.reasons if old else frozenset()) | {reason},
            (old.dirty_keys if old else frozenset()) | frozenset(dirty), freeze(scopes), freeze(missing),
            freeze(self._scope_failures(scopes)), freeze({s: self._versions[s] for s in missing}))

    def _scope_failures(self, scopes):
        return {key: error for key, error in self._failures.items()
                if key[0] in scopes and key[2] == scopes[key[0]].id}

    def accept_local(self, part, prior_version):
        if part.receipt.source.producer != 'local':
            raise ValueError('local acquisition requires a local source')
        return self._accept(part, prior_version)

    def accept_external(self, part, prior_version):
        if part.receipt.source.producer == 'local':
            raise ValueError('external acquisition cannot commit a local source')
        return self._accept(part, prior_version)

    def _accept(self, part, prior_version):
        with self._lock:
            self._live()
            receipt, source = part.receipt, part.receipt.source
            current = self._versions.get(source)
            if (current != prior_version or receipt.version.store_epoch_uuid != self.epoch
                    or receipt.owner_token != self._owners.get(source)
                    or receipt.version.source_generation != current.source_generation
                    or receipt.version.source_revision != current.source_revision + 1):
                raise StaleCapture('source compare-and-swap refused')
            demands = self.demands(source)
            completed = {sid for sid in receipt.coverage.completed_scopes
                         if demands.get(sid) == receipt.coverage.captured_demands[sid]}
            for sid in demands:
                pending = self._admissions.get(sid.chat)
                if (pending and len(pending.receipt_ids) >= self._limits[3]
                        and receipt.coverage.capture_id not in pending.receipt_ids):
                    raise SnapshotCapacity('unserved receipt custody capacity reached')
            old = self._parts.get(source)
            old_rows = old.rows if old else {}
            changed = set(part.tombstones) | set(part.invalidated_keys)
            certified_keys = set().union(*(part.scope_keys.get(sid, ()) for sid in completed))
            for name in ('rows', 'details', 'edges'):
                previous = getattr(old, name) if old else {}
                supplied = getattr(part, name)
                changed.update(k for k, value in supplied.items() if previous.get(k) != value)
                changed.update(k for k in certified_keys if k in previous and k not in supplied)
            if old and old.remote_parents != part.remote_parents:
                changed.update(old.rows)
            # Only this capture's exact, still-current completed demands may
            # hold certificates. Neither merged payload nor equal/null stamps
            # attest membership of an omitted scope. Keep its payload, not trust.
            receipts = {k: v for k, v in (old.scope_receipts.items() if old else ()) if k in completed}
            revoked = {k for k in (old.scope_receipts if old else ()) if k in demands and k not in completed}
            keys = {k: v for k, v in (old.scope_keys.items() if old else ()) if k in demands}
            stale = {k: v for k, v in (old.stale_scope_keys.items() if old else ()) if k in demands}
            scoped_members = {k: v for k, v in (old.scope_memberships.items() if old else ()) if k in demands}
            scoped_rows = {k: v for k, v in (old.scope_rows.items() if old else ()) if k in demands}
            unknown = {sid for sid in receipt.coverage.unknown_scopes
                       if demands.get(sid) == receipt.coverage.captured_demands[sid]}
            for sid in unknown:
                receipts.pop(sid, None)
                self._failures[(source, current.source_generation, sid)] = 'capture scope unknown'
            renewed = set()
            for sid in completed:
                new_keys = frozenset(part.scope_keys.get(sid, ()))
                new_members = part.scope_memberships.get(sid, tuple(
                    m for m in part.memberships if m.card in new_keys and m.session in demands[sid].lineage))
                if (not self._covered(source, demands[sid]) or keys.get(sid) != new_keys
                        or scoped_members.get(sid) != new_members
                        or scoped_rows.get(sid) != part.scope_rows.get(sid)
                        or changed.intersection(set(keys.get(sid, ())) | set(new_keys) | demands[sid].refs)):
                    receipts[sid] = receipt
                    renewed.add(sid)
                keys[sid] = new_keys
                stale.pop(sid, None)
                scoped_members[sid] = new_members
                if sid in part.scope_rows:
                    scoped_rows[sid] = part.scope_rows[sid]
                else:
                    scoped_rows.pop(sid, None)
                self._failures.pop((source, current.source_generation, sid), None)
            rows = {k: v for k, v in old_rows.items() if k not in certified_keys}
            rows.update(part.rows)
            details = {k: v for k, v in old.details.items() if k not in certified_keys} if old else {}
            details.update(part.details)
            edges = {k: v for k, v in old.edges.items() if k not in certified_keys} if old else {}
            edges.update(part.edges)
            # Tombstones only remove keys from the scopes this result actually covered.
            for sid in completed:
                keys[sid] = frozenset(keys[sid]) - part.tombstones
            merged = replace(part, rows=rows, details=details, edges=edges,
                             scope_receipts=receipts, scope_keys=keys, stale_scope_keys=stale,
                             scope_memberships=scoped_members, scope_rows=scoped_rows)
            self._parts[source] = self._prune(merged)
            self._versions[source] = receipt.version
            self._sequence += 1
            affected = set()
            for sid, demand in demands.items():
                old_keys = old.scope_keys.get(sid, ()) if old else ()
                new_keys = keys.get(sid, ())
                if sid in renewed or sid in unknown or sid in revoked or changed.intersection(set(old_keys) | set(new_keys) | demand.refs):
                    self._admit(sid.chat, 'source_accepted', receipt, changed)
                    affected.add(sid.chat)
            return self._snapshot(), tuple(self._admissions[c] for c in sorted(affected))

    def fail(self, source, generation, error, affected, *, prior_version=None):
        with self._lock:
            self._live()
            if self._versions[source].source_generation != generation:
                return False
            if prior_version is not None and self._versions[source] != prior_version:
                return False
            current = self.demands(source)
            for sid in affected:
                if sid in current:
                    self._failures[(source, generation, sid)] = str(error)
                    self._admit(sid.chat, 'source_error')
            return True

    def reset(self, source):
        with self._lock:
            self._live()
            v = self._versions[source]
            self._versions[source] = SourceVersion(self.epoch, v.source_generation + 1, 0)
            self._owners[source] = uuid4().hex
            if source in self._parts:
                self._parts[source] = replace(self._parts[source], scope_receipts={})
            self._failures = {k: v for k, v in self._failures.items() if k[0] != source}
            for chat in self._watches:
                self._admit(chat, 'source_reset')

    def _snapshot(self):
        refs, members = {}, {}
        for chat, watch in self._watches.items():
            for key in self._refs[chat]:
                refs.setdefault(key, set()).add(chat)
            for session in watch.lineage_tuple:
                members.setdefault(session, set()).add(chat)
        return Snapshot(self.epoch, self._sequence, freeze(self._parts), freeze(self._watches),
                        freeze(refs), freeze(members), freeze(self._failures),
                        freeze({s: self.demands(s) for s in self._versions}))

    def pin(self, chat):
        with self._lock:
            self._live()
            # Refresh the vector, never its original first-admission clocks.
            self._admit(chat, 'pin')
            return self._snapshot(), self._admissions[chat]

    def current(self, admission):
        with self._lock:
            if self._closed or admission.chat not in self._watches:
                return False
            pending = self._admissions.get(admission.chat)
            if pending is None or pending.id != admission.id:
                return False
            if admission.required_failures != self._scope_failures(admission.required_scopes):
                return False
            if set(admission.required_scopes) != set(self._versions):
                return False
            for source, demand in admission.required_scopes.items():
                if self._demand(admission.chat) != demand:
                    return False
                if source in admission.required_versions:
                    if not self._covered(source, demand):
                        return False
                    if self._parts[source].scope_receipts[demand.id].version != admission.required_versions[source]:
                        return False
                elif (self._covered(source, demand)
                        or self._versions[source] != admission.missing_versions.get(source)):
                    return False
            return True

    @contextmanager
    def publication(self, admission):
        """Memory-only CAS borrow. Writer stores before release, emits AFTER.

        Lock order is Feed.compute_lock -> Feed.lock -> this lock. Acquisition
        never takes a Feed lock while holding this lock. No I/O or coverage
        granting is allowed in the borrowed scope.
        """
        with self._lock:
            yield self.current(admission)

    def acknowledge(self, admission):
        """Retire a successfully published exact vector, preserving superseded custody.

        The publisher performs its existing publication CAS before invoking this
        method. This store never publishes a bundle or clears another episode.
        """
        with self._lock:
            if not self.current(admission):
                return False
            pending = self._admissions[admission.chat]
            if pending.receipt_ids != admission.receipt_ids:
                return False
            del self._admissions[admission.chat]
            return True

    def forget(self, chat, token):
        with self._lock:
            watch = self._watches.get(chat)
            if watch is None or watch.opaque_watch_token != token:
                return False
            del self._watches[chat]
            self._refs.pop(chat)
            self._refs_gen.pop(chat)
            self._admissions.pop(chat, None)
            self._failures = {k: v for k, v in self._failures.items() if k[2].chat != chat}
            for source, part in list(self._parts.items()):
                receipts = {k: v for k, v in part.scope_receipts.items() if k.chat != chat}
                self._parts[source] = self._prune(replace(part, scope_receipts=receipts,
                    scope_keys={k: v for k, v in part.scope_keys.items() if k.chat != chat},
                    stale_scope_keys={k: v for k, v in part.stale_scope_keys.items() if k.chat != chat},
                    scope_memberships={k: v for k, v in part.scope_memberships.items() if k.chat != chat},
                    scope_rows={k: v for k, v in part.scope_rows.items() if k.chat != chat}))
            return True

    def close(self, epoch):
        with self._lock:
            if epoch != self.epoch:
                return False
            self._closed = True
            return True

    def export_resume(self):
        with self._lock:
            return self._snapshot()

    @classmethod
    def transfer(cls, stopped_owner):
        """Compatible in-process transfer only; no connection or live owner crosses.

        Capture owner tokens rotate even though exact immutable source versions,
        watch identities, failures and first-unserved admission ages are retained.
        """
        with stopped_owner._lock:
            if (not stopped_owner._closed or getattr(stopped_owner, '_transferred', False)
                    or getattr(stopped_owner, '_schema', None) != 'board-snapshot/1'):
                raise StaleCapture('compatible transfer requires a stopped snapshot owner')
            new = cls(max_watches=stopped_owner._limits[0], max_sources=stopped_owner._limits[1],
                      max_tokens=stopped_owner._limits[2], max_pending_receipts=stopped_owner._limits[3])
            new.epoch, new._sequence = stopped_owner.epoch, stopped_owner._sequence
            for name in ('_parts', '_watches', '_refs', '_refs_gen', '_versions', '_failures', '_admissions'):
                setattr(new, name, dict(rehome(getattr(stopped_owner, name))))
            new._used_tokens = set(stopped_owner._used_tokens)
            new._owners = {source: uuid4().hex for source in new._versions}
            stopped_owner._transferred = True
            return new


class SnapshotReaders:
    """Pure scoped projection adapters. No canonical decision reader is exposed.

    Missing coverage raises explicitly; the caller can keep the prior bundle as
    stale presentation, but cannot turn a failed source into authoritative empty.
    Legacy detail lookup resolves only a unique owner in this chat's scope.
    """
    def __init__(self, snapshot, chat):
        self.snapshot = snapshot
        self.chat = chat
        self.diagnostics = []
        if chat not in snapshot.watches:
            raise KeyError(chat)

    def lineage(self, session):
        if session != self.chat.session_id:
            raise ValueError('snapshot belongs to another chat')
        return list(self.snapshot.watches[self.chat].lineage_tuple)

    def _scopes(self):
        for source, demands in self.snapshot.demands.items():
            demand = next((d for d in demands.values() if d.id.chat == self.chat), None)
            part = self.snapshot.parts.get(source)
            receipt = part.scope_receipts.get(demand.id) if part and demand else None
            if (receipt is None or receipt.coverage.captured_demands.get(demand.id) != demand
                    or (source, receipt.version.source_generation, demand.id) in self.snapshot.failures):
                raise CoverageUnavailable(f'unknown {source.producer} scope for {self.chat.session_id}')
            yield source, part, demand

    def _listed(self, ids, *, include_terminal):
        if tuple(ids) != tuple(self.lineage(self.chat.session_id)):
            raise ValueError('lineage does not match the pinned scope')
        result = []
        for source, part, demand in self._scopes():
            if demand.id in part.scope_rows:
                selected = part.scope_rows[demand.id]['done' if include_terminal else 'linked']
                result.extend(thaw(selected))
                continue
            roles = {}
            for member in part.scope_memberships.get(demand.id, ()):
                if member.session in demand.lineage:
                    if member.role == 'created' or member.card not in roles:
                        roles[member.card] = member.role
            for key, role in roles.items():
                row = part.scope_rows.get(demand.id, {}).get(key, part.rows.get(key))
                if row is None or not include_terminal and row.get('status') in ('done', 'archived', 'cancelled'):
                    continue
                value = {**thaw(row), 'via': role}
                value.pop('last_heartbeat_at', None)
                result.append(value)
        return result

    def linked(self, ids):
        return self._listed(ids, include_terminal=False)

    def done(self, ids):
        return self._listed(ids, include_terminal=True)

    def card_key(self, key):
        found = []
        for source, part, demand in self._scopes():
            if key in part.scope_keys.get(demand.id, ()) and key in part.details:
                found.append((source, part.details[key]))
        if len(found) != 1:
            if found:
                self.diagnostics.append(f'ambiguous detail producer for {key}')
            return None
        return {'board': key.board, 'gateway': key.gateway, 'profile': key.profile,
                'detail': thaw(found[0][1])}

    def card(self, card_id, board):
        keys = {key for _, part, demand in self._scopes()
                for key in part.scope_keys.get(demand.id, ())
                if key.card_id == card_id and key.board == (board or 'default') and key in part.details}
        if len(keys) != 1:
            if keys:
                self.diagnostics.append(f'ambiguous owner for {board}/{card_id}')
            return None
        return self.card_key(next(iter(keys)))
