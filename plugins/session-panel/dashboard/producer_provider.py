"""Panel exec provider: complete Feed/board/decision/events. Not plugin register()."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import queue


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
    home_context = config.get('home_context') if isinstance(config, dict) else None
    installation = config.get('installation') if isinstance(config, dict) else None
    return PanelProducer(module, home_context=home_context, installation=installation)


class PanelProducer:
    def __init__(self, api, *, home_context=None, installation=None, start_lanes=True):
        self.api = api
        if home_context is None:
            self.readers = api.CoreReaders()
        else:
            self.readers = api.CoreReaders(home_context=home_context, installation=installation)
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
            snap = f.changes(names, since=0)
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
            wire = json.dumps({'running': running, **status}, ensure_ascii=False, separators=(',', ':')).encode()
            return prefix + wire[1:]
        if verb == 'decision-refresh':
            session = body['session']
            try:
                result = f.refresh_decision(session, readers=self.readers)
            except ValueError as exc:
                # Canonical conflict semantics travel as a publication, not a transport failure.
                return json.dumps({'_conflict': str(exc)}, ensure_ascii=False, separators=(',', ':')).encode()
            result['bundle'] = f.decision_projection(result['bundle'])
            wire = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
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
        f.changes_bytes(sessions)
        f._drain()
        if subscriber:
            # The board lane is the single owner of _panel_subscribers.
            f._panel_updates.put((subscriber, visible, lease))
            f._board.set()
            lane = getattr(f, '_board_lane', None)
            if lane is not None:
                lane.service_wake.set()
        with f.lock:
            for session in sessions:
                if session in f.interest:
                    f.interest[session] = f.clock() + max(0, accepted + 900 - now)
        try:
            wire = f.changes_bytes(sessions, since=body.get('since', 0), epoch=body.get('epoch'))
            while not f._registrations.empty():
                f._registrations.get_nowait()
            if any(session in f.failures for session in sessions):
                raise self._unavailable_error(sessions) or RuntimeError('panel source capture unavailable')
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
