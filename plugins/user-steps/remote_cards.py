"""Exact-linked remote asks; immutable source binding and authenticated courier receipts.

No board replica is writable here. The owner revalidates the frozen generation
and independently retrieves the recorded source operation before any transition.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


def load_file(path):
    key = '_user_steps_remote_' + hashlib.sha256(str(path).encode()).hexdigest() + str(path.stat().st_mtime_ns)
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(key, mod)
    return sys.modules[key]


def linked_cards(owner):
    """Same core membership reader as the panel, then exact origin/chat filtering.

    Replica/link rows may omit the full ask or portable owner generation. Hydrate
    only those qualified targets from the courier's published owner records.
    """
    # Missing core is unavailable, never evidence that a prior remote ask departed.
    # [daveey] Last good projection (stale_ok): the action queue (/user-steps/requests) must never wait on
    # ``_links_lock`` while a links rebuild runs. Strict reads queued 10-60 s behind it on the hub (2026-11-23) and
    # Desktop showed "Action queue unavailable" at its 10 s budget. The answer is the shared links facts snapshot
    # (up to its ~90 s validity) or the warm links cache; membership changes can show late, card content does not
    # (it comes from the hub view below).
    from hermes_cli.kanban_session_activity import _remote_cards
    cards = _remote_cards([owner['storedSessionId']], ('blocked', 'triage'), True)
    # A replica-only fallback (origin custody unreadable, e.g. the links facts not fresh) omits ledger-carried and
    # other-profile asks. Absence in it is not departure evidence: raise so reconcile defers instead of withdrawing.
    if any(card.get('partial_source_unknown') for card in cards):
        raise OSError('Remote ask membership partial: ' + str(next(
            c['partial_source_unknown'] for c in cards if c.get('partial_source_unknown'))))
    view_module = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py')
    view = view_module.open_view()
    if view is None:
        raise OSError('Remote ask owner projection unavailable')
    try:
        gateway = view.gateway
        selected = {}
        for card in cards:
            if (card.get('session') != owner['storedSessionId']
                    and card.get('origin_session') != owner['storedSessionId']):
                continue
            if (card.get('origin_gateway', gateway) != gateway
                    or card.get('origin_profile', owner['profile']) != owner['profile']
                    or card.get('gateway') == gateway):
                continue
            target = tuple(card.get(k) for k in ('gateway', 'profile', 'board', 'id'))
            if all(isinstance(x, str) and x for x in target):
                selected[target] = card
        if not selected:
            return []
        rows = view.rows('tasks', pick=lambda candidates: [c for c in candidates
                         if (c[0], *c[1].split('/')) in selected])
        return [dict(r, session=owner['storedSessionId']) for r in rows]
    finally:
        view.conn.close()


def source_gateway():
    """Trusted transport identity, never the ActionStore install namespace."""
    view = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py').open_view()
    if view is None:
        raise OSError('Local courier identity unavailable')
    try:
        if not isinstance(view.gateway, str) or not re.fullmatch('[A-Za-z0-9_.-]+', view.gateway):
            raise OSError('Local courier identity unavailable')
        return view.gateway
    finally:
        view.conn.close()


class Courier:
    """Durably enqueue once per operation; retries only read the same request.

    The normal courier sync owns transport/recovery. Never turn a queued envelope
    into a successful user-steps receipt. Read back the authenticated owner record.
    """
    def __init__(self):
        self.view = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py')

    def read(self, envelope, rid):
        """Authenticated read-only receipt observation; never queue an answer."""
        client = self.view.load_client()
        if client is None:
            raise OSError('Authenticated card-answer courier unavailable')
        try:
            cfg = client.load_config()
            api = client.Api(cfg['hub_url'], client.read_key())
            status, data = api.call('GET', '/v2/sync/requests/' + rid)
        except (Exception, SystemExit) as exc:
            raise OSError('Card-answer receipt unavailable') from exc
        if status == 404:
            return None
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get('request'), dict):
            raise OSError('Card-answer receipt unavailable')
        return data['request']

    def local_state(self, rid):
        """Local courier journal state of this request id (None: never queued)."""
        client = self.view.load_client()
        if client is None:
            raise OSError('Authenticated card-answer courier unavailable')
        db = client.open_db(self.view.db_path())
        try:
            row = db.execute('SELECT state FROM sent WHERE id=?', (rid,)).fetchone()
        except Exception as exc:
            raise OSError('Card-answer local state unavailable') from exc
        finally:
            db.close()
        return row[0] if row else None

    def exchange(self, envelope, rid):
        client = self.view.load_client()
        if client is None:
            raise OSError('Authenticated card-answer courier unavailable')
        db = client.open_db(self.view.db_path())
        try:
            if client.mget(db, 'gateway') != envelope['args']['source']['gateway']:
                raise OSError('Card-answer source gateway mismatch')
            client.queue_request(db, envelope['to_gw'], 'tasks', envelope['key'], 'card-answer',
                                 envelope['args'], rid=rid)
            db.commit()
        finally:
            db.close()
        try:
            self.view._send_now(client, self.view.db_path(), self.view.SEND_WAIT_S)
            cfg = client.load_config()
            api = client.Api(cfg['hub_url'], client.read_key())
            status, data = api.call('GET', '/v2/sync/requests/' + rid)
        except (Exception, SystemExit) as exc:
            # Optional transport/key failures must not terminate the Desktop backend.
            raise OSError('Card-answer courier unavailable; check the same operation') from exc
        if status != 200 or not isinstance(data, dict):
            raise OSError('Card-answer receipt unavailable; retry the same operation')
        return data.get('request')


def _strict_db_stamp(path):
    """Required DB plus optional WAL; metadata errors must reach input_stamp.

    hubview._file_sig deliberately conflates missing and unreadable files for its
    advisory caches. That contract cannot certify this successful-work shortcut.
    """
    path = Path(path)
    def signature(p):
        st = p.stat()
        return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
    database = signature(path)
    try:
        wal = signature(Path(str(path) + '-wal'))
    except FileNotFoundError:
        wal = ('absent-wal',)
    return database, wal


def _known_revision(value):
    # Opaque/unknown sentinels must not become reusable success evidence. This
    # intentionally fails conservative for older core fingerprint formats.
    if isinstance(value, (tuple, list)):
        return bool(value) and all(_known_revision(part) for part in value)
    return type(value) in (str, int) and (not isinstance(value, str) or bool(value))


class DeliveryPresentation:
    """Pure captured-operation reducer: no board, runtime, courier or store."""

    RETIRING = 'remote-retiring'
    UNCONFIRMED_AFTER_S = 3600

    @staticmethod
    def _age(op):
        from datetime import datetime, timezone
        try:
            at = datetime.fromisoformat((op.get('retired') or {})['at'])
            return max(0.0, (datetime.now(timezone.utc) - at).total_seconds())
        except (KeyError, ValueError, TypeError):
            return 0.0

    def project_delivery_note(self, op, target):
        if op['dispatchState'] != 'cancelled' or (op.get('retired') or {}).get('evidence') != self.RETIRING:
            return None
        age = int(self._age(op))
        gateway = json.loads(target['snapshot'])['remote']['target']['ownerGateway']
        unconfirmed = age >= self.UNCONFIRMED_AFTER_S
        return dict(state='delivering', ageSeconds=age, unconfirmed=unconfirmed,
                    text=(f'Delivery to {gateway} not confirmed ({age // 60} min)' if unconfirmed else 'Delivering…'))


class Remote(DeliveryPresentation):
    def __init__(self, mirror, *, cards=linked_cards, courier=None, gateway=source_gateway):
        self.mirror, self.cards = mirror, cards
        self.gateway = gateway
        self.courier = courier

    def input_stamp(self, store_path):
        """Cheap source revisions, not another membership projection.

        An injected membership adapter has no declared inputs. Unreadable stamps
        disable success memoization, never vouch for an empty remote inventory.
        The caller retains a 60s full-pass ceiling for time/courier transitions.
        """
        if self.cards is not linked_cards:
            return None
        try:
            from hermes_cli import kanban_session_activity as activity
            view = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py')
            revision = activity._activity_fingerprint()
            if not _known_revision(revision):
                return None
            return (revision,
                    _strict_db_stamp(view.db_path()),
                    _strict_db_stamp(self.mirror.core._state_db()),
                    _strict_db_stamp(store_path))
        except Exception:
            return None

    def membership_capture(self, owner):
        """Owner-scoped complete membership key; None always means changed.

        Kept separate from input_stamp until the plugin dependency memo also
        captures request/binding/operation and historical namespace inputs.
        """
        if self.cards is not linked_cards:
            return None
        import time
        try:
            module = load_file(Path(__file__).with_name('panel_membership.py'))
            view = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py')
            return module.for_remote(self, owner, now=time.time(), view=view)
        except Exception:
            return None

    def snapshots(self, owner):
        for row in self.cards(owner):
            try:
                for snap in self._snapshot(owner, row):
                    self.mirror._request(owner, snap['remote']['target']['board'], snap)
                    yield snap
            except (OSError, ValueError, TypeError, KeyError, self.mirror.core.ActionError):
                # Only this target is unknown. Healthy rows still publish; the
                # unknown row is not authority to retire its prior operation.
                yield {'unknown': tuple(row.get(k) for k in ('gateway', 'profile', 'board', 'id'))}

    def _snapshot(self, owner, row):
        if row.get('stale'):
            raise OSError('Remote ask projection stale; retain prior request')
        if row.get('status') not in ('blocked', 'triage'):
            return
        hr, generation = row.get('human_request'), row.get('card_answer_generation')
        if (not isinstance(hr, dict) or not isinstance(hr.get('action'), str) or not hr['action'].strip()
                or not isinstance(row.get('title'), str) or not row['title'].strip()
                or hr.get('interaction') not in ('reply', 'approval', 'user_terminal', 'physical', 'external')
                or not isinstance(generation, str) or not re.fullmatch('[0-9a-f]{64}', generation)):
            raise OSError('Remote ask incomplete; retain prior request')
        target = dict(zip(('ownerGateway', 'ownerProfile', 'board', 'task'),
                          (row.get(k) for k in ('gateway', 'profile', 'board', 'id'))))
        if (not all(isinstance(x, str) and re.fullmatch('[A-Za-z0-9_.-]+', x) for x in target.values())
                or target['ownerGateway'] == self.gateway()):
            return
        target['generation'] = generation
        options = hr.get('choices') or [dict(label='Unblock', value='unblock'), dict(label='Archive', value='archive')]
        if (not isinstance(options, list) or any(not isinstance(ch, dict)
                or not isinstance(ch.get('label'), str) or not ch['label'].strip()
                or not isinstance(ch.get('value'), str) or not ch['value'].strip() for ch in options)
                or len({ch['value'] for ch in options}) != len(options)):
            raise OSError('Remote ask choices malformed; retain prior request')
        yield dict(task=target['task'], session=owner['storedSessionId'], generation=generation,
                   ask=hr, title=row.get('title') or hr['action'],
                   remote=dict(version=1, target=target,
                               choiceValues={f'card-{i}': ch['value'] for i, ch in enumerate(options)}))

    def reconcile(self, owner):
        m = self.mirror
        try:
            snapshots = list(self.snapshots(owner))  # failure is not departure evidence
        except ImportError:
            return  # core-free installation: no remote decision authority or withdrawal
        valid = [snap for snap in snapshots if 'unknown' not in snap]
        unknown = {snap['unknown'] for snap in snapshots if 'unknown' in snap}
        current = {self._target(snap['remote']['target']): snap for snap in valid}
        display_current = dict(current)
        with m.store._read() as c:
            bindings = [dict(r) for r in c.execute(
                "SELECT * FROM kanban_projections WHERE namespace=? AND session=? AND json_type(snapshot,'$.remote')='object'",
                (m.store._ns(owner), owner['storedSessionId']))]
        # Retire old intent before minting the current generation. Unknown rows
        # deliberately preserve their own binding, not everybody else's.
        held_targets = set()
        for binding in bindings:
            frozen = json.loads(binding['snapshot'])
            target = self._target(frozen['remote']['target'])
            if target in unknown:
                held_targets.add(target)
                continue
            request = m.store.get(owner, binding['request_id'])
            if m.explicit_revision(request, binding):
                held_targets.add(target)  # explicit chat revision owns this ask now
                continue
            snap = current.get(target)
            # Custody survives display identity: an original generation can return
            # while its answer is retiring. Observe it before the display shortcut.
            oid = (request.get('resolution') or {}).get('operationId')
            op = m.store.operation(owner, oid)['operation'] if oid else None
            if op and (op.get('retired') or {}).get('evidence') == self.RETIRING:
                if not self._retire(owner, oid, 'Source card ask changed before delivery'):
                    held_targets.add(target)
                    if snap is not None and snap['generation'] == frozen['generation']:
                        self._display(owner, request['requestId'], display_current[target])
                    continue
                request = m.store.get(owner, binding['request_id'])
            if snap is not None and snap['generation'] == frozen['generation']:
                self._display(owner, request['requestId'], display_current[target])
                # The immutable projection/operation keeps its original bytes.
                # Display refreshes never mint another decision-capable binding.
                if request['state'] in ('open', 'needs_context'):
                    held_targets.add(target)
                else:
                    current[target] = frozen
                continue
            if request.get('resolution'):
                if not self._retire(owner, request['resolution']['operationId'], 'Source card ask changed before delivery'):
                    held_targets.add(target)
            elif request['state'] in ('open', 'needs_context'):
                if not m._withdraw(owner, request):
                    # A click committed between our read and the withdrawal: its
                    # pending intent suppresses a replacement decision tile.
                    held_targets.add(target)
        for target, snap in current.items():
            if target in held_targets:
                continue
            board = snap['remote']['target']['board']
            request = m._current_request(owner, board, snap)
            if request is not None:
                m._publish(owner, board, snap, request)
                self._display(owner, request['requestId'], display_current[target])

        return not unknown

    def request_for(self, owner, target):
        """Explicit tool lookup: reuse only a current producer-bound linked ask.

        The caller supplies identity, never a generation, choice mapping or
        source proof. No recorded ad-hoc operation is retroactively bound.
        """
        m = self.mirror
        keys = ('ownerGateway', 'ownerProfile', 'board', 'task')
        m.core._object(target, keys)
        if any(not isinstance(target[k], str) or not re.fullmatch('[A-Za-z0-9_.-]+', target[k]) for k in keys):
            raise m.core.ActionError('Invalid remote card identity')
        try:
            self.reconcile(owner)
            snapshots = [s for s in self.snapshots(owner) if 'remote' in s
                         and self._target(s['remote']['target']) == self._target(target)]
            if len(snapshots) != 1:
                raise m.core.ActionError('Current linked remote ask unavailable; no ad-hoc answer binding created', 409)
            snap = snapshots[0]
            with m.store._read() as c:
                bindings = [dict(r) for r in c.execute(
                    'SELECT * FROM kanban_projections WHERE namespace=? AND session=? AND board=? AND task=?',
                    (m.store._ns(owner), owner['storedSessionId'], target['board'], target['task']))]
            matches = []
            for binding in bindings:
                frozen = json.loads(binding['snapshot'])
                if (frozen.get('remote', {}).get('target') != snap['remote']['target']):
                    continue
                request = m.store.get(owner, binding['request_id'])
                if (request['state'] in ('open', 'needs_context') and not request.get('resolution')
                        and not m.explicit_revision(request, binding)):
                    matches.append(request)
            if len(matches) != 1:
                raise m.core.ActionError('No unique undecided native remote ask; do not publish a substitute', 409)
            return matches[0]
        except (OSError, ImportError) as exc:
            raise m.core.ActionError('Remote ask unavailable; retry native lookup, not an ad-hoc copy', 409) from exc

    def _display(self, owner, rid, snap):
        with self.mirror.store._tx() as c:
            c.execute('INSERT INTO kanban_projection_display VALUES (?,?,?) '
                      'ON CONFLICT(namespace,request_id) DO UPDATE SET body=excluded.body '
                      'WHERE body!=excluded.body',
                      (self.mirror.store._ns(owner), rid, json.dumps(snap, sort_keys=True)))

    @staticmethod
    def _target(target):
        return tuple(target.get(k) for k in ('ownerGateway', 'ownerProfile', 'board', 'task'))

    STABLE_REFUSAL_S = 120      # proof refusal must have been in force this long before absence counts
    TERMINAL = ('rejected', 'expired', 'failed')

    def delivery_note(self, owner, request):
        """Display-only: a held (retiring) remote operation is `Delivering`, never `Cancelled`."""
        oid = (request.get('resolution') or {}).get('operationId')
        if not oid:
            return None
        op = self.mirror.store.operation(owner, oid)['operation']
        if op['dispatchState'] != 'cancelled' or (op.get('retired') or {}).get('evidence') != self.RETIRING:
            return None
        return self.project_delivery_note(op, self.mirror.binding(owner, request['requestId']))

    def _retire(self, owner, oid, reason):
        """Departure/generation change: state machine in docs/remote-card-answer-states.md.

        Returns True only when the operation is terminal (delivered, or refused/absent with stable proof
        refusal) so the target may remint; False keeps the target held and visible."""
        from types import SimpleNamespace
        store = self.mirror.store
        with store.delivery_lease(owner, oid) as held:
            if not held:
                return False
            op = store.operation(owner, oid)['operation']
            retiring = (op['dispatchState'] == 'cancelled'
                        and (op.get('retired') or {}).get('evidence') == self.RETIRING)
            if op['dispatchState'] == 'delivered' or (op['dispatchState'] == 'cancelled' and not retiring):
                return True
            binding = self.mirror._bound(op)
            try:
                record, receipt = self._observe(op, binding)
                if receipt is not None:
                    if retiring:
                        _, again = self._observe(op, binding)  # independent readback before settling
                        if again != receipt:
                            return False
                        store.settle_late_receipt(owner, oid, receipt, marker=self.RETIRING)
                        return True
                    receiver = SimpleNamespace(deliver=lambda operation: receipt,
                        readback=lambda operation, value: self._observe(operation, binding)[1] == value)
                    result = store._dispatch(owner, oid, receiver)
                    return result['operation']['dispatchState'] == 'delivered'
                local = None
                if record is None:
                    local_state = getattr(self.courier or Courier(), 'local_state', None)
                    local = local_state(self._rid(op)) if local_state is not None else 'unknown'
            except (OSError, AttributeError, self.mirror.core.ActionError):
                return False  # unverifiable preserves intent and blocks a fresh tile
            if record is not None and record['state'] in self.TERMINAL:
                if retiring:
                    store.finalize_retirement(owner, oid, marker=self.RETIRING,
                                              evidence='Authenticated owner terminal refusal')
                else:
                    with store._tx() as c:
                        store._retire_operation(c, owner, oid, by='system', reason='Remote card answer retired',
                                                evidence='Authenticated owner terminal refusal')
                return True
            if not retiring:
                # Turn proof refusal on first; absence/404/in-flight is never a terminal fact.
                with store._tx() as c:
                    store._retire_operation(c, owner, oid, by='system', reason='Remote card answer retiring',
                                            evidence=self.RETIRING)
                return False
            if record is None and local in (None, 'rejected') and self._age(op) >= self.STABLE_REFUSAL_S:
                store.finalize_retirement(owner, oid, marker=self.RETIRING,
                                          evidence='Courier envelope absent with stable proof refusal')
                return True
            return False

    @staticmethod
    def _rid(op):
        return 'hr-card-answer-' + hashlib.sha256(json.dumps([op['owner'], op['operationId']], sort_keys=True).encode()).hexdigest()

    def _observe(self, op, binding):
        envelope = self.envelope(op, binding)
        record = (self.courier or Courier()).read(envelope, self._rid(op))
        if record is None:
            return None, None
        return record, self._receipt(envelope, self._rid(op), record)

    def envelope(self, op, binding):
        snap = json.loads(binding['snapshot'])
        remote = snap['remote']
        choice = remote['choiceValues'].get(op['decision']['choiceId'])
        other = op['decision']['choiceId'] == 'answer-text'
        if other:
            if (op.get('provenance') != dict(type='kanban_other', version=1)
                    or op['intent'].get('executionAuthorized') is not False
                    or op['intent'].get('permissionGrants') != []):
                raise self.mirror.core.ActionError('Bound informational answer required', 409)
            choice = op['intent']['revisionText']
        if choice is None:
            raise self.mirror.core.ActionError('Unknown remote card choice', 409)
        answer = dict(remote['target'], choice=choice, operationId=op['operationId'])
        if other:
            answer['mode'] = 'comment'
        owner = op['owner']
        return dict(to_gw=answer['ownerGateway'], coll='tasks',
                    key='/'.join(answer[k] for k in ('ownerProfile', 'board', 'task')), action='card-answer',
                    args=dict(answer=answer, source=dict(gateway=self.gateway(), profile=owner['profile'],
                                                       session=owner['storedSessionId'])))

    def deliver(self, op, binding):
        envelope = self.envelope(op, binding)
        rid = self._rid(op)
        record = (self.courier or Courier()).exchange(envelope, rid)
        receipt = self._receipt(envelope, rid, record)
        if record.get('state') in ('rejected', 'expired', 'failed'):
            raise self.mirror.core.DeliveryRefused(
                'Remote owner refused or terminated the exact card answer: ' + record['state'], 409)
        if receipt is None:
            raise OSError('Remote answer pending; check status or retry this operation')
        return receipt

    @staticmethod
    def _receipt(envelope, rid, record):
        if (not isinstance(record, dict) or record.get('id') != rid
                or any(record.get(k) != v for k, v in envelope.items())
                or record.get('from') != 'gw:' + envelope['args']['source']['gateway']):
            raise OSError('Card-answer courier envelope mismatch')
        if record.get('state') != 'done':
            return None
        receipt = record.get('result')
        answer = envelope['args']['answer']
        if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in answer.items())
                or receipt.get('mode') != answer.get('mode')
                or receipt.get('originGateway') != envelope['args']['source']['gateway']
                or receipt.get('destination') != 'kanban_ask' or receipt.get('executionStarted') is not False
                or receipt.get('decisionProvenance') != 'recorded_desktop_operation'):
            raise OSError('Invalid remote answer receipt')
        return receipt
