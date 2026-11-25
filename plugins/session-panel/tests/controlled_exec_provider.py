"""Fixture-only clock/identity injection around the REAL production provider.

No fixture code is loaded by production. The manifest pins this file, and runtime still execs broker/child.
"""
import importlib.util
from pathlib import Path
import sys
import time
from contextlib import contextmanager

WALL = 1_000_000.0
MONO = 100.0


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def clocks():
    old = time.time, time.monotonic, time.perf_counter
    time.time = lambda: WALL
    time.monotonic = lambda: MONO
    time.perf_counter = lambda: MONO
    try:
        yield
    finally:
        time.time, time.monotonic, time.perf_counter = old


def open_provider(config):
    dashboard = Path(__file__).resolve().parents[1] / 'dashboard'
    api = load('_deterministic_exec_api', dashboard / 'plugin_api.py')
    provider = load('_deterministic_exec_provider', dashboard / 'producer_provider.py')
    return Controlled(provider.PanelProducer(api, start_lanes=False))


class Controlled:
    def __init__(self, provider):
        self.provider = provider
        self.feed = provider.feed
        self.feed.epoch = 'parity-epoch'
        self.feed.clock = lambda: WALL

    def __call__(self, payload):
        body = payload.get('body', payload)
        # Test-only scheduler: one real capture before serving, no background race.
        with clocks():
            self.feed.changes_bytes(body.get('sessions') or ['chat'])
            self.feed._drain()
            self.feed.tick()
            return self.provider({'body': body, 'accepted': MONO})

    def events_since(self, after, limit=64):
        return self.provider.events_since(after, limit)

    def close(self):
        self.provider.close()
