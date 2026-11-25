"""Panel exec provider: complete Feed/board/decision/events. Not plugin register()."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import queue
from collections import OrderedDict


class EventRing:
    """Bounded ordered events: seq 1.. contiguous; a cursor older than the ring gets reset+snapshot."""

    MAX_EVENTS = 256
    MAX_BYTES = 2 * 1024 * 1024
    MAX_BATCH_BYTES = 512 * 1024

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []  # [(seq, payload, size)]
        self._bytes = 0
        self._seq = 0

    def append(self, payload):
        size = len(json.dumps(payload, ensure_ascii=True, separators=(',', ':')))
        with self._lock:
            self._seq += 1
            self._items.append((self._seq, payload, size))
            self._bytes += size
            while len(self._items) > self.MAX_EVENTS or self._bytes > self.MAX_BYTES:
                self._bytes -= self._items.pop(0)[2]
            return self._seq

    def since(self, after, limit=64):
        with self._lock:
            head = self._seq
            if after is None:
                return {'seq': head, 'events': [], 'reset': False}
            oldest = self._items[0][0] if self._items else head + 1
            if after == -1 or after > head or after + 1 < oldest:
                return {'seq': head, 'events': [], 'reset': True}
            out, total = [], 0
            for seq, payload, size in self._items:
                if seq <= after:
                    continue
                if len(out) >= limit or (out and total + size > self.MAX_BATCH_BYTES):
                    break
                out.append([seq, payload])
                total += size
            return {'seq': head, 'events': out, 'reset': False}


def open_provider(config):
    if 'gateway.run' in sys.modules or 'run_agent' in sys.modules:
        raise RuntimeError('producer process bootstrapped an agent')
    name = '_session_panel_producer_api_v1'
    path = Path(__file__).with_name('plugin_api.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    provider = config.get('provider') if isinstance(config, dict) else None
    if isinstance(provider, dict) and 'callback_registry' in provider.get('manifest', {}):
        module._callback_bootstrap_mod().bootstrap(provider['manifest'])
    home_context = config.get('home_context') if isinstance(config, dict) else None
    installation = config.get('installation') if isinstance(config, dict) else None
    spec = config.get('provider') if isinstance(config, dict) else None
    if isinstance(spec, dict) and spec.get('partitioned'):
        return PanelProducerSet(module)
    return PanelProducer(module, home_context=home_context, installation=installation)


class PanelProducerSet:
    """One child, several canonical homes: a bounded set of explicit-home producers keyed by the server-bound
    (home, lifecycle generation, authorization generation). A request without a valid claim is refused; a stale
    or revoked claim closes that home's producers before anything is answered."""

    MAX_PARTITIONS = 8

    def __init__(self, api, *, factory=None):
        self.api = api
        self._factory = factory or self._make
        self._lock = threading.Lock()
        self._parts = OrderedDict()  # identity -> PanelProducer, least recently used first

    def _make(self, binding):
        ctx = self.api._home_context_mod().PanelHomeContext.from_authorized_home(
            binding.home, lifecycle_generation=binding.lifecycle_generation,
            authorization_generation=binding.authorization_generation)
        return PanelProducer(self.api, home_context=ctx)

    def _close_home(self, home):
        with self._lock:
            old = [self._parts.pop(ident) for ident in [i for i in self._parts if i[0] == home]]
        for producer in old:
            producer.close()

    def _producer(self, wire):
        from hermes_cli import serve_producer_home as homes
        binding = homes.HomeBinding.from_wire(wire)
        try:
            path, life = homes.lifecycle_generation(binding.home)
        except homes.HomeRevoked as exc:
            self._close_home(binding.home)
            raise RuntimeError('home revoked') from exc
        if str(path) != binding.home or life != binding.lifecycle_generation:
            self._close_home(binding.home)
            raise RuntimeError('stale home generation')
        closing = []
        with self._lock:
            found = self._parts.get(binding.identity)
            if found is not None:
                self._parts.move_to_end(binding.identity)
                return found
            # An older generation of this very home is revoked by the arrival of the newer one.
            closing.extend(self._parts.pop(i) for i in [i for i in self._parts if i[0] == binding.home])
            while len(self._parts) >= self.MAX_PARTITIONS:
                closing.append(self._parts.popitem(last=False)[1])
            producer = self._factory(binding)
            self._parts[binding.identity] = producer
        for old in closing:
            old.close()
        return producer

    def __call__(self, payload):
        body = payload['body'] if isinstance(payload, dict) and 'body' in payload else payload
        wire = body.get('_partition') if isinstance(body, dict) else None
        if wire is None:
            raise ValueError('home partition required')
        producer = self._producer(wire)
        clean = {key: value for key, value in body.items() if key != '_partition'}
        # Reaching here requires a mapping body and a valid partition. A non-dict payload
        # was also the body above and could not pass that requirement.
        if 'body' in payload:
            return producer(dict(payload, body=clean))
        return producer(clean)

    def events_since(self, after, limit=64, partition=None):
        if partition is None:
            raise ValueError('home partition required')
        return self._producer(partition).events_since(after, limit)

    def partitions(self):
        with self._lock:
            return list(self._parts)

    def close(self):
        with self._lock:
            old = list(self._parts.values())
            self._parts.clear()
        for producer in old:
            producer.close()


class PanelProducer:
    def __init__(self, api, *, home_context=None, installation=None, start_lanes=True):
        self.api = api
        if home_context is None:
            self.readers = api.CoreReaders()
        else:
            self.readers = api.CoreReaders(home_context=home_context, installation=installation)
        self.readers.producer_snapshot_scope = True
        self.events = queue.Queue()
        self.ring = EventRing()
        self._lease_lock = threading.Lock()
        self._leases = {}
        self.feed = api._load_feed().Feed(self.readers, emit=self._emit)
        self._threads = []
        if start_lanes:
            self._start_lanes()

    def _emit(self, payload):
        """Child-side producer events go to a bounded ordered ring, never straight to a socket."""
        raw = dict(payload)
        pushed = self.api.push_payload(raw)
        # Serving identity belongs to the serve process that re-stamps it.
        pushed.pop('feed', None)
        pushed.pop('profile', None)
        self.ring.append(pushed)

    def events_since(self, after, limit=64):
        batch = self.ring.since(after, limit)
        if batch['reset']:
            f = self.feed
            with f.lock:
                names = sorted(f.bundles)
            snap = json.loads(f.prepared_changes_bytes(names, since=0))
            pushed = self.api.push_payload(snap)
            pushed.pop('feed', None)
            pushed.pop('profile', None)
            batch['snapshot'] = pushed
        return batch

    def _start_lanes(self):
        core = self.api._load_feed()
        feed = self.feed
        t = threading.Thread(target=self.api._run, args=(feed,), name='session-panel-feed', daemon=True)
        feed._feed_thread = t
        self._threads.append(t)
        t.start()
        d = threading.Thread(target=self.api._run_decisions, args=(feed,),
                             name='session-panel-decisions', daemon=True)
        feed._decision_thread = d
        self._threads.append(d)
        d.start()
        b = threading.Thread(target=self.api._run_board, args=(feed, core.BoardLane),
                             name='session-panel-board', daemon=True)
        feed._board_thread = b
        self._threads.append(b)
        b.start()
        with feed.lock:
            feed._board_service_required = True

    def __call__(self, payload):
        body = payload['body'] if isinstance(payload, dict) and 'body' in payload else payload
        identity = body['_wire_identity']
        if set(identity) != {'feed', 'profile'} or not all(isinstance(v, str) for v in identity.values()):
            raise ValueError('invalid serving identity')
        prefix = json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()[:-1] + b','
        verb = body.get('_verb', 'changes')
        accepted = payload.get('accepted', time.monotonic()) if isinstance(payload, dict) else time.monotonic()
        now = time.monotonic()
        if now < accepted or accepted + 900 <= now:
            raise RuntimeError('expired browser interest')
        f = self.feed
        if verb == 'status':
            status = f.status()
            running = not f.stopped.is_set()
            wire = json.dumps(self.api.json_safe({'running': running, **status}), ensure_ascii=False, separators=(',', ':')).encode()
            return prefix + wire[1:]
        if verb == 'decision-refresh':
            session = body['session']
            try:
                result = f.refresh_decision(session, readers=self.readers)
            except ValueError as exc:
                # Canonical conflict semantics travel as a publication, not a transport failure.
                return json.dumps({'_conflict': str(exc)}, ensure_ascii=False, separators=(',', ':')).encode()
            result['bundle'] = f.decision_projection(result['bundle'])
            wire = json.dumps(self.api.json_safe(result), ensure_ascii=False, separators=(',', ':')).encode()
            return prefix + wire[1:]
        sessions = body.get('sessions', [])
        wall = f.clock()
        subscriber = body.get('subscriber')
        visible = body.get('visible_session')
        lease = wall + max(0, accepted + 75 - now)  # 75s runs from original HTTP acceptance
        with self._lease_lock:
            self._leases = {key: value for key, value in self._leases.items() if value[1] > wall}
            if subscriber and subscriber not in self._leases and len(self._leases) >= 256:
                raise RuntimeError('subscriber budget')
            if subscriber:
                self._leases[subscriber] = (visible, lease)
        f.changes_bytes(sessions, accepted_at=wall - (now - accepted))
        f._drain()
        if subscriber:
            # The board lane is the single owner of _panel_subscribers.
            f._panel_updates.put((subscriber, visible, lease))
            f._board.set()
            lane = getattr(f, '_board_lane', None)
            if lane is not None:
                lane.service_wake.set()
        try:
            wire = f.prepared_changes_bytes(sessions, since=body.get('since', 0), epoch=body.get('epoch'))
            unavailable = self._unavailable_error(sessions)
            if unavailable is not None:
                raise unavailable
            if any(session in f.failures and session not in f._wire[2] for session in sessions):
                raise RuntimeError('panel source capture unavailable')
            return prefix + wire[1:]
        finally:
            self.readers.release()

    def _unavailable_error(self, sessions):
        """The typed adapter error when every failed session was refused by an explicit-home adapter gate.

        A failure that is not a recorded gate refusal stays the generic error: unavailability is never inferred."""
        seen = getattr(self.readers, 'unavailable_seen', None)
        scopes = seen() if callable(seen) else frozenset()
        failed = [self.feed.failures[s].get('error', '') for s in sessions if s in self.feed.failures]
        if not scopes or not failed or not all(e.startswith('UnsupportedHomeAdapter:') for e in failed):
            return None
        named = sorted(scope for scope in scopes if failed[0].endswith(scope))
        return self.api._home_context_mod().UnsupportedHomeAdapter((named or sorted(scopes))[0])

    def close(self):
        self.feed.stopped.set()
        for attr in ('_wake', '_decide', '_board'):
            event = getattr(self.feed, attr, None)
            if event is not None:
                event.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self.readers.release()
