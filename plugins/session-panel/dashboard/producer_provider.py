"""Explicit panel-only exec fixture provider; no plugin discovery or startup.

This walking skeleton does not certify the full safe-provider registration
manifest. It is intentionally unreachable from normal plugin registration.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import time
import queue


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
    def __init__(self, api, *, home_context=None, installation=None):
        self.api = api
        if home_context is None:
            self.readers = api.CoreReaders()
        else:
            self.readers = api.CoreReaders(home_context=home_context, installation=installation)
        self.feed = api._load_feed().Feed(self.readers, emit=lambda event: None)

    def __call__(self, payload):
        body = payload['body']
        # Bound by the serving endpoint, not child argv or browser input. The
        # relay keys the entire command, isolating serving slots/profiles.
        identity = body['_wire_identity']
        if set(identity) != {'feed', 'profile'} or not all(isinstance(v, str) for v in identity.values()):
            raise ValueError('invalid serving identity')
        prefix = json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()[:-1] + b','
        sessions = body.get('sessions', [])
        accepted = payload['accepted']
        now = time.monotonic()
        if now < accepted or accepted + 900 <= now:
            raise RuntimeError('expired browser interest')
        f = self.feed
        wall = f.clock()
        f._panel_subscribers = {key: value for key, value in f._panel_subscribers.items() if value[1] > wall}
        subscriber = body.get('subscriber')
        if subscriber and subscriber not in f._panel_subscribers and len(f._panel_subscribers) >= 256:
            raise RuntimeError('subscriber budget')
        # Consume the browser command exactly once, then preserve its original
        # HTTP acceptance deadline instead of granting a fresh TTL on dequeue.
        f.changes_bytes(sessions, subscriber=body.get('subscriber'), visible_session=body.get('visible_session'))
        f._drain()
        # This thin slice has no board-lane consumer. It owns visibility updates
        # directly, so do not retain the legacy lane's unconsumed queue.
        while True:
            try:
                f._panel_updates.get_nowait()
            except queue.Empty:
                break
        for session in sessions:
            if session in f.interest:
                f.interest[session] = f.clock() + max(0, accepted + 900 - now)
        if subscriber:
            f._panel_subscribers[subscriber] = (body.get('visible_session'), f.clock() + max(0, accepted + 75 - now))
        try:
            f.tick()
            wire = f.changes_bytes(sessions, since=body.get('since', 0), epoch=body.get('epoch'))
            # changes_bytes is reused for byte parity, not another browser renewal.
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
        self.readers.release()
