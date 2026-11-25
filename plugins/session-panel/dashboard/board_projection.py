"""Opt-in pure partial projection. No source acquisition or service activation.

SnapshotReaders remains strict. This adapter explicitly retains unknown-source
custody and last-known presentation; neither a partial projection nor publication
can grant coverage. Feed's writer validates the exact Admission under the store
lock, then releases that lock before wire serialization/transport.
"""
from dataclasses import dataclass, field, replace
from collections.abc import Mapping
import importlib


@dataclass(frozen=True)
class Projection:
    admission: object
    bundle: Mapping
    covered_scopes: Mapping
    missing_scopes: Mapping
    input_versions: Mapping
    dependencies: Mapping
    feed_epoch: str
    feed_owner: object
    watch_token: object
    external_capture: tuple = ()
    observation_parent: object = None
    publication_token: object = field(default_factory=object)
    evidence_attempt: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class PublishResult:
    disposition: str
    changed: bool = False
    emit_succeeded: bool = False
    revision: int | None = None
    # Private evidence, frozen at the serialized writer, never execution truth.
    prior_successful_publication: bytes | None = None
    identical_model: bytes | None = None


def canonical_overlay(board, canonical):
    """Independent canonical component, including its availability diagnostics."""
    old_board_errors = canonical.get('board_errors', ())
    errors = [e for e in canonical.get('input_errors', ()) if e not in old_board_errors]
    result = {**board, **{k: canonical[k] for k in ('canonical_seq', 'outcome_json', 'requests', 'processes') if k in canonical}}
    result['input_errors'] = list(dict.fromkeys(errors + list(board.get('board_errors', ()))))
    return result


def partial_readers(module, snapshot, admission, canonical_base):
    """Create memory-only adapters over one immutable materialization.

    Last-known memberships are selected only from this exact scope. Expanded
    scopes may retain same-lineage prior visible rows for known scoped keys; no
    dependency is promoted to membership and no other owner's row is borrowed.
    """
    class PartialReaders(module.SnapshotReaders):
        def _scopes(self):
            # Deliberately NOT the strict adapter's certification path.
            for source, demand in admission.required_scopes.items():
                part = snapshot.parts.get(source)
                if part is not None:
                    yield source, part, demand

        def _keys(self, part, demand):
            return part.scope_keys.get(demand.id, part.stale_scope_keys.get(demand.id, ()))

        def _listed(self, ids, *, include_terminal):
            if tuple(ids) != tuple(self.lineage(self.chat.session_id)):
                raise ValueError('lineage does not match the pinned scope')
            result = []
            for source, part, demand in self._scopes():
                missing = source in admission.missing_scopes
                selected = part.scope_rows.get(demand.id)
                if selected is not None:
                    # Reuse strict selection, but only for this source, avoiding
                    # its all-source coverage check without issuing new trust.
                    if 'linked' in selected and 'done' in selected:
                        values = module.thaw(selected['done' if include_terminal else 'linked'])
                    else:
                        values = self._membership_rows(part, demand, include_terminal)
                else:
                    values = self._membership_rows(part, demand, include_terminal)
                    if missing and not values:
                        keys = self._keys(part, demand)
                        values = [dict(row) for row in canonical_base.get('done' if include_terminal else 'tasks', ())
                                  if any(self._matches(row, key) for key in keys)]
                if missing:
                    values = [{**row, 'stale': True, 'partial_source_unknown': True} for row in values]
                for row in values:
                    row.pop('last_heartbeat_at', None)
                result.extend(values)
            return result

        @staticmethod
        def _matches(row, key):
            return row.get('id') == key.card_id and all(
                row.get(name) == getattr(key, name) for name in ('gateway', 'profile', 'board'))

        def _membership_rows(self, part, demand, terminal):
            roles = {}
            for member in part.scope_memberships.get(demand.id, ()):
                if member.session in demand.lineage and (member.role == 'created' or member.card not in roles):
                    roles[member.card] = member.role
            return [{**module.thaw(part.rows[key]), 'via': role} for key, role in roles.items()
                    if key in part.rows and (terminal or part.rows[key].get('status') not in ('done', 'archived', 'cancelled'))]

        def card_key(self, key):
            found = [(source, part.details[key]) for source, part, demand in self._scopes()
                     if key in self._keys(part, demand) and key in part.details]
            if len(found) != 1:
                if found:
                    self.diagnostics.append(f'ambiguous detail producer for {key}')
                return None
            source, detail = found[0]
            detail = module.thaw(detail)
            if source in admission.missing_scopes:
                detail['task'] = {**detail.get('task', {}), 'stale': True, 'partial_source_unknown': True}
            return {'gateway': key.gateway, 'profile': key.profile, 'board': key.board, 'detail': detail}

        def card_ref(self, card_id, board, row=None):
            row = row or {}
            owners = {key for _, part, demand in self._scopes() for key in self._keys(part, demand)
                      if key.card_id == card_id and key in part.details}
            if len(owners) > 1:
                # The legacy bundle.cards wire input is ID-keyed. Do not feed
                # either owner's detail to both rows through that lossy boundary.
                self.diagnostics.append(f'ambiguous legacy detail id {card_id}')
                return None
            keys = {key for _, part, demand in self._scopes() for key in self._keys(part, demand)
                    if key.card_id == card_id and key.board == (row.get('board') or board or 'default')
                    and all(not row.get(name) or row[name] == getattr(key, name) for name in ('gateway', 'profile'))}
            if len(keys) != 1:
                if keys:
                    self.diagnostics.append(f'ambiguous owner for {board}/{card_id}')
                return None
            return self.card_key(next(iter(keys)))

        def card(self, card_id, board):
            return self.card_ref(card_id, board)

    return PartialReaders(snapshot, admission.chat)


def project(feed, store, chat, *, canonical_base, pin=None, seq=None, external_capture=()):
    # Pin may be supplied by any board/full/catch-up consumer. It never reacquires
    # a source and never reads ActionStore, SessionDB or the live CoreReaders.
    module = importlib.import_module(type(store).__module__)
    snapshot, admission = store.pin(chat) if pin is None else pin
    if admission.chat != chat or snapshot.store_epoch != store.epoch:
        raise ValueError('projection pin belongs to another scope/epoch')
    watch = snapshot.watches.get(chat)
    if watch is None or watch.opaque_watch_token != admission.watch_token:
        raise ValueError('projection pin has no matching watch')
    if (canonical_base.get('session') != chat.session_id
            or tuple(canonical_base.get('lineage', ())) != watch.lineage_tuple):
        raise ValueError('canonical inputs require the exact current lineage')
    rd = partial_readers(module, snapshot, admission, canonical_base)
    with feed.lock:
        owner, token, epoch = feed._projection_owner, feed._watch_tokens.get(chat.session_id), feed.epoch
    bundle = feed.compute(chat.session_id, seq=seq, readers=rd, base=canonical_base)
    receipts = [snapshot.parts[s].scope_receipts[d.id] for s, d in admission.required_scopes.items()
                if s in admission.required_versions]
    # Retain actual acquisition age, never projection/completion time. Missing
    # sources and their failure custody are explicit even when no rows exist.
    bundle['computed_at'] = min((r.received_wall_ns / 1e9 for r in receipts),
                                default=canonical_base.get('computed_at', 0.))
    errors = [f'{s.producer} board scope unavailable' for s in admission.missing_scopes]
    errors += rd.diagnostics
    bundle['board_errors'] = list(dict.fromkeys(errors))
    bundle = canonical_overlay(bundle, canonical_base)
    bundle['model'] = feed.reducer(bundle, now=feed.clock())
    dependencies = {s: frozenset(snapshot.parts[s].scope_keys.get(d.id, ()))
                      | frozenset(snapshot.parts[s].stale_scope_keys.get(d.id, ()))
                      | d.refs if s in snapshot.parts else d.refs
                    for s, d in admission.required_scopes.items()}
    return Projection(admission, module.freeze(bundle),
        module.freeze({s: d for s, d in admission.required_scopes.items() if s not in admission.missing_scopes}),
        admission.missing_scopes, admission.required_versions, module.freeze(dependencies), epoch, owner, token,
        tuple(external_capture))


def observe(feed, store, previous, captured):
    """Carry only canonical pull clocks, never new evidence or source coverage.

    Capture acquisition/semantic comparison precedes this opt-in seam. Reuse the
    canonical pure identity/semantic helpers, not a second producer classifier.
    Admission and exact publication-parent CAS still apply, so a concurrent old
    producer cannot regress the clocks. Source reset/semantic changes require a
    new store capture and projection, not this operation.
    """
    semantic = feed.readers.external_semantic
    captured = tuple(captured)
    if not previous.external_capture or semantic(captured) != semantic(previous.external_capture):
        raise ValueError('observation carriage requires identical canonical source semantics')
    module = importlib.import_module(type(store).__module__)
    bundle = feed.readers.advance_external(module.thaw(previous.bundle), captured)
    bundle['model'] = feed.reducer(bundle, now=feed.clock())
    return replace(previous, bundle=module.freeze(bundle), external_capture=captured,
                   observation_parent=previous.publication_token, publication_token=object())
