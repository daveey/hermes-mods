"""Session-panel feed API, mounted at /api/plugins/session-panel (see feed.py and ../README.md).

POST /changes {sessions, since, epoch} -> {feed, profile, epoch, rev, reset, bundles}: watch these chats, catch up.
Pushes: `plugin.session-panel.changed` {feed, profile, epoch, prev, rev, bundles | heartbeat} on the gateway stream.
GET /status: the feed's revision, watched chats, failures and last recompute time.

The feed is launch-profile scoped; other profiles return profile_unsupported.
Production starts on the serving process, never by computing on a request.
"""
from __future__ import annotations

import contextlib
import dataclasses
import errno
import functools
import hashlib
import copy
from contextlib import contextmanager
import importlib.util
import json
import logging
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response


# Thread lanes (core ``hermes_cli.web_thread_lanes``): this plugin's Desktop-polled reads get their own thread
# budget and a queue deadline (503 + Retry-After, never started) instead of the shared AnyIO pool, which slow
# reads filled for ~2 h on 2026-11-21. A core without lanes keeps the shared pool, as before. [daveey]
try:
    from hermes_cli.web_thread_lanes import run_in_lane as _run_in_lane
except ImportError:  # core before thread lanes
    _run_in_lane = None


async def _on_lane(lane, fn, *args, request=None, deadline=None, **kwargs):
    if _run_in_lane is None:
        return await run_in_threadpool(functools.partial(fn, *args, **kwargs))
    return await _run_in_lane(lane, fn, *args, request=request, deadline=deadline, **kwargs)

PLUGIN_ID = 'session-panel'
TICK_S = float(os.environ.get('HERMES_SESSION_PANEL_FEED_TICK_S', '1'))
# Run outcomes that count as a failure in a card's "N failures" (not reclaims, rate limits or host reboots).
FAILED_RUN_OUTCOMES = ('crashed', 'spawn_failed', 'oom_killed', 'timed_out', 'gave_up', 'failed', 'signaled')
EVENTS_KEPT = 40   # newest events per card record (heartbeats left out): the park reader needs the newest park and what followed
log = logging.getLogger(PLUGIN_ID)


_FEED_KEY = '_session_panel_feed_core'
_FEED_PATH = Path(__file__).resolve().parent / 'feed.py'
# Process-wide (not per plugin_api generation: a hot reload re-imports this file while the old generation's threads
# may still call their own _load_feed): one lock and a generation counter for loading feed.py.
_LOADER = sys.modules.setdefault('_session_panel_feed_loader', types.ModuleType('_session_panel_feed_loader'))
_LOADER.__dict__.setdefault('lock', threading.Lock())
_LOADER.__dict__.setdefault('generation', 0)


def _home_context_mod():
    path = Path(__file__).with_name('home_context.py')
    key = '_session_panel_home_context'
    mod = sys.modules.get(key)
    if mod is None or not hasattr(mod, 'UnsupportedHomeAdapter'):
        spec = importlib.util.spec_from_file_location(key, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


def _explicit_home_mod():
    path = Path(__file__).with_name('explicit_home.py')
    key = '_session_panel_explicit_home'
    mod = sys.modules.get(key)
    if mod is None or not hasattr(mod, 'identity'):
        spec = importlib.util.spec_from_file_location(key, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


def _external_projection():
    """Dormant pre-import selection; never hand over an existing legacy owner."""
    external = getattr(_LOADER, 'external_projection', None)
    if external is not None:
        starting = getattr(_LOADER, 'starting', None)
        legacy = getattr(sys.modules.get(_FEED_KEY), 'PROCESS_FEED', None)
        if legacy is not None or (starting is not None and starting.is_alive()):
            raise RuntimeError('external projection conflicts with legacy panel custody')
        if getattr(_LOADER, 'facts_registration', None) is not None:
            raise RuntimeError('external projection conflicts with legacy facts custody')
    return external


def _feed_stamp() -> tuple:
    try:
        st = os.stat(_FEED_PATH)
        lane = _FEED_PATH.with_name('board_lane.py')
        extra = lane.stat() if lane.exists() else None
        facts = _FEED_PATH.with_name('fact_subscription.py')
        facts_stat = facts.stat() if facts.exists() else None
        coordinator = _FEED_PATH.with_name('source_coordinator.py')
        coordinator_stat = coordinator.stat() if coordinator.exists() else None
        return (st.st_ino, st.st_mtime_ns, st.st_size,
                (extra.st_ino, extra.st_mtime_ns, extra.st_size) if extra else None,
                (facts_stat.st_ino, facts_stat.st_mtime_ns, facts_stat.st_size) if facts_stat else None,
                (coordinator_stat.st_ino, coordinator_stat.st_mtime_ns, coordinator_stat.st_size) if coordinator_stat else None)
    except OSError:
        return ()


def _load_feed():
    """feed.py as a module of this process: it holds the process's one feed (PROCESS_FEED), so it outlives plugin
    reloads (plugin_api.py is re-imported; this module is not). When feed.py's bytes change on disk the new code is
    loaded and the running feed handed over: it is stopped and resumed under the new code at once (same epoch and
    revisions). [daveey 2026-11-21] a hot mods-reload re-imported plugin_api.py but kept the old feed.py, so a feed
    fix did not take effect until the backend process restarted."""
    if _external_projection() is not None:
        raise RuntimeError('external projection excludes legacy feed loading')
    stamp = _feed_stamp()
    old = sys.modules.get(_FEED_KEY)
    # Current, or feed.py unreadable (keep what runs). A module loaded by older code carries no stamp: checked.
    if old is not None and (not stamp or getattr(old, 'SOURCE_STAMP', None) == stamp):
        return old
    restart = False
    with _LOADER.lock:
        old = sys.modules.get(_FEED_KEY)
        if old is not None and (not stamp or getattr(old, 'SOURCE_STAMP', None) == stamp):
            return old
        try:
            source = _FEED_PATH.read_bytes()
        except OSError:
            if old is not None:
                return old
            raise
        lane = _FEED_PATH.with_name('board_lane.py')
        facts = _FEED_PATH.with_name('fact_subscription.py')
        coordinator = _FEED_PATH.with_name('source_coordinator.py')
        digest = hashlib.sha256(source + (lane.read_bytes() if lane.exists() else b'')
                                + (facts.read_bytes() if facts.exists() else b'')
                                + (coordinator.read_bytes() if coordinator.exists() else b'')).hexdigest()
        if old is not None and getattr(old, 'SOURCE_SHA', None) == digest:
            old.SOURCE_STAMP = stamp  # touched or rewritten with the same bytes: nothing to load
            return old
        _LOADER.generation += 1
        # Executed under its own name (dataclasses resolve it in sys.modules), so no caller ever sees a half-built
        # module at _FEED_KEY.
        name = f'{_FEED_KEY}_g{_LOADER.generation}'
        spec = importlib.util.spec_from_file_location(name, _FEED_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            if old is None:
                raise
            log.warning('session-panel: feed.py failed to load; keeping the running feed until it changes again',
                        exc_info=True)
            old.SOURCE_STAMP = stamp  # not retried on every call; the next change on disk is
            return old
        mod.SOURCE_STAMP, mod.SOURCE_SHA, mod.RESTART = stamp, digest, feed
        mod.SNAPSHOT_READER_FACTORY = getattr(_LOADER, 'snapshot_reader_factory', None)
        for hook in ('INSTALL_FACTS', 'STOP_FACTS'):
            if old is not None and hasattr(old, hook):
                setattr(mod, hook, getattr(old, hook))
        if old is None:
            sys.modules[_FEED_KEY] = mod
            return mod
        with old.PROCESS_LOCK:
            running = getattr(old, 'PROCESS_FEED', None)
            mod.PROCESS_LOCK = old.PROCESS_LOCK  # one lock for every generation of this process's feed
            mod.PROCESS_FEED = running
            sys.modules[_FEED_KEY] = mod  # swapped under the lock feed() re-checks with
            if getattr(old, '__name__', '').startswith(_FEED_KEY + '_g'):
                sys.modules.pop(old.__name__, None)  # the previous generation's own name: nothing refers to it
            # Any feed found here is of an older class (a reload always makes a new one): hand it over now. A stopped
            # one with no successor was stopped by this plugin's unload during the same reload (loading plugin_api.py
            # means the plugin is being loaded, not disabled), so it is resumed too instead of waiting for a request.
            if running is not None and getattr(running, '_successor', None) is None:
                if getattr(_LOADER, 'facts_registration', None) is not None:
                    _LOADER.facts_transition_pending = True
                running.stopped.set()
                restart = True
    if restart:  # resume it now, not at the next request: pushes and heartbeats continue
        target = _resume_facts_reload if getattr(_LOADER, 'facts_registration', None) is not None else feed
        threading.Thread(target=target, name='session-panel-feed-resume', daemon=True).start()
    return mod


@dataclasses.dataclass
class BoardChanges:
    """Semantic invalidation, preserving each source's board coordinate."""
    sources: dict[tuple[str, str], set[str]] = dataclasses.field(default_factory=dict)
    sessions: set[str] = dataclasses.field(default_factory=set)
    full: bool = False

    def affects(self, held):
        if self.full or held['lineage'] & self.sessions:
            return True
        bundle = held['bundle']
        rows = list(bundle.get('tasks', [])) + list(bundle.get('done', []))
        rows += [dict(rec.get('detail', {}).get('task', {}), board=rec.get('board', 'default'))
                 for rec in bundle.get('cards', {}).values() if isinstance(rec, dict)]
        rows += [dict(id=parent, board=rec.get('board', 'default'))
                 for rec in bundle.get('cards', {}).values() if isinstance(rec, dict)
                 for parent in _diagnostic_refs(rec.get('detail', {}))]
        # Local and replica sources may share a board slug. Keep their cursors
        # and changed IDs separate; conservatively match both against that board's
        # held inputs (legacy chain records don't always retain gateway scope).
        return any(str(row.get('id')) in ids and (row.get('board') or 'default') == board
                   for row in rows for (_path, board), ids in self.sources.items())


# Scopes with no profile-home adapter. Explicit mode refuses these before any ambient resolver.
# Working acquisition (H) implements the former blanket refusals via home-local canonical readers.
EXPLICIT_UNAVAILABLE_SCOPES = frozenset({
    'hub_db',       # hub replica DB: not a profile-home API
    'mail_config',  # mail config path: not a profile-home API
})


class CoreReaders:
    """Feed reads against this gateway's own boards, board replicas and state.db, read-only (query_only). Each thread has its own
    connections (the feed thread and catch-up request threads read concurrently); a connection is reopened when its
    file was replaced (another inode) and dropped when the file is gone."""

    def __init__(self, state_db: Optional[Path] = None, *, home_context=None, installation=None):
        if home_context is not None and state_db is not None:
            raise TypeError('state_db is not combined with home_context')
        from hermes_cli import kanban_sync_client as ksc
        self.ksc = ksc
        self._local = threading.local()
        self._boards: list[tuple[str, Path]] = []
        self._boards_at = 0.0
        self._boards_lock = threading.Lock()
        self.home_context = home_context
        self.installation = installation
        self._unavailable_lock = threading.Lock()
        self._unavailable_hits: set[str] = set()
        if home_context is not None:
            state_db = Path(home_context.home) / 'state.db'
            self.unavailable_scopes = EXPLICIT_UNAVAILABLE_SCOPES
        else:
            self.unavailable_scopes = frozenset()
            if state_db is None:
                from hermes_constants import get_hermes_home
                state_db = Path(get_hermes_home()) / 'state.db'
        self._state_db = Path(state_db)

    def _explicit_home(self):
        return getattr(self, 'home_context', None)

    def _home_sync_config(self) -> dict:
        return _explicit_home_mod().parse_home_sync_config(self.home_context.home)

    def _home_identity(self) -> dict:
        return _explicit_home_mod().identity(self.home_context.home, self.installation)

    def card_scope_for(self, path) -> tuple:
        """(gateway, profile) for a board path under the authorized home. Never card_scope()."""
        ident = self._home_identity()
        return ident.get('gateway'), ident.get('profile')

    def _refuse(self, scope: str):
        with self._unavailable_lock:
            self._unavailable_hits.add(scope)
        raise _home_context_mod().UnsupportedHomeAdapter(scope)

    def _origin_rows(self) -> list:
        ident = self._home_identity()
        return _explicit_home_mod().origin_rows(
            self.home_context.home, gateway=ident.get('gateway') or '', profile=ident.get('profile') or 'default')

    def cache_identity(self):
        ctx = self._explicit_home()
        if ctx is None:
            return ('legacy', str(self._state_db))
        return ctx.identity

    def require_scope(self, scope: str):
        """Fail closed in explicit mode: call this BEFORE any acquisition that resolves an ambient home or identity.

        Legacy (omitted-context) readers never raise here. The refused scope is recorded so a producer can report
        typed unavailability instead of a generic or empty failure."""
        if scope in getattr(self, 'unavailable_scopes', frozenset()):
            with self._unavailable_lock:
                self._unavailable_hits.add(scope)
            raise _home_context_mod().UnsupportedHomeAdapter(scope)

    def unavailable_seen(self) -> frozenset:
        with self._unavailable_lock:
            return frozenset(self._unavailable_hits)

    def snapshot_path(self) -> Path:
        ctx = self._explicit_home()
        if ctx is None:
            return _snapshot_path()
        safe = re.sub(r'[^A-Za-z0-9_.-]', '_', f'{FEED_ROLE}-{self._home_identity()["profile"]}-{ctx.namespace()}')
        return ctx.home / 'session-panel' / f'feed-{safe}.json'

    def timing_path(self) -> Path:
        if self._explicit_home() is None:
            return timing_path()
        path = self.snapshot_path()
        return path.with_name('timing-' + path.name)

    def _shadow_path(self, slug: str) -> Path:
        ctx = self._explicit_home()
        if ctx is None:
            return Path(self.ksc.shadow_path(slug))
        from hermes_cli import kanban_db_sync
        return ctx.home / 'kanban' / kanban_db_sync.SHADOW_DIR / f'{slug}.db'

    def _explicit_boards(self) -> list[tuple[str, Path]]:
        home = self._explicit_home().home
        from hermes_cli.kanban_sync_client import BOARD_ID_RE
        from hermes_cli import kanban_db_sync
        found: list[tuple[str, Path]] = []
        default = home / 'kanban.db'
        if default.exists():
            found.append(('default', default))
        boards_root = home / 'kanban' / 'boards'
        if boards_root.is_dir():
            for child in sorted(boards_root.iterdir(), key=lambda p: p.name):
                db = child / 'kanban.db'
                if BOARD_ID_RE.match(child.name) and db.is_file():
                    found.append((child.name, db))
        local = {slug for slug, _ in found}
        links = home / 'kanban' / kanban_db_sync.LINKS_DIR
        if links.is_dir():
            found += [(p.stem, p) for p in sorted(links.glob('*.db'))
                      if BOARD_ID_RE.match(p.stem) and p.stem not in local]
        config = _explicit_home_mod().parse_home_sync_config(home)
        selected = [(slug, path) for slug, path in found if kanban_db_sync.board_selected(str(path), config)]
        if any(not path.resolve().is_relative_to(home.resolve()) for _, path in selected):
            self._refuse('outside_home_store')
        return selected

    # files
    def boards(self) -> list[tuple[str, Path]]:
        with self._boards_lock:
            if not self._boards_at:
                if self._explicit_home() is not None:
                    self._boards = self._explicit_boards()
                else:
                    self._boards = list(self.ksc.board_paths())
                self._boards_at = time.monotonic()
            return list(self._boards)

    def invalidate_inventory(self):
        with self._boards_lock:
            self._boards_at = 0.0

    def board_paths(self) -> list[Path]:
        boards = self.boards()
        return list(dict.fromkeys([Path(p) for _, p in boards] +
                                  [self._shadow_path(s) for s, _ in boards]))

    def files(self) -> list[Path]:
        return [p for p in self.board_paths() if p.exists()]

    def state_db(self) -> Path:
        return self._state_db

    def release(self) -> None:
        """Close this thread's connections (a request thread after its catch-up; the feed thread keeps its own)."""
        for c, _ in (getattr(self._local, 'conns', None) or {}).values():
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local.conns = {}

    def conn(self, path: Path) -> Optional[sqlite3.Connection]:
        ctx = self._explicit_home()
        if ctx is not None and not Path(path).resolve().is_relative_to(ctx.home.resolve()):
            self._refuse('outside_home_store')
        conns = getattr(self._local, 'conns', None)
        if conns is None:
            conns = self._local.conns = {}
        key = str(path)
        try:
            ino = os.stat(path).st_ino
        except OSError:
            old = conns.pop(key, None)
            if old:
                old[0].close()
            return None
        hit = conns.get(key)
        if hit and hit[1] == ino:
            return hit[0]
        if hit:
            hit[0].close()
        try:
            # mode=rw + query_only, never a ro URI: SQLite cannot open a WAL database read-only while its -wal / -shm
            # are absent, and Hermes writers delete them on each last-close checkpoint (tests/test_sqlite_ro_guard.py).
            # mode=rw never creates a missing file; query_only refuses every write.
            c = sqlite3.connect(f'file:{path}?mode=rw', uri=True, timeout=2)
            c.execute('PRAGMA query_only=ON')
            c.row_factory = sqlite3.Row
        except sqlite3.Error:
            return None
        conns[key] = (c, ino)
        return c

    # feed readers
    def stamps(self):
        """One fingerprint per file that moves with every committed write: core's WAL stamp, else (not WAL, no -shm
        yet) the file's and its -wal's mtime and size, so a feed is never deaf to a board in another journal mode."""
        from hermes_cli.session_list_flight import data_stamp

        def stat(p):
            out = []
            for f in (p, Path(f'{p}-wal')):
                try:
                    st = os.stat(f)
                    out.append((st.st_ino, st.st_mtime_ns, st.st_size))
                except OSError:
                    out.append(None)
            return tuple(out)
        return tuple((str(p), data_stamp(p) or stat(p)) for p in self.files() + [self.state_db()]) + self.external_stamps()

    @staticmethod
    def snapshot_modules():
        """Explicit B1 acquisition capability; no production consumer calls it."""
        import importlib
        directory = Path(__file__).resolve().parent
        digest = hashlib.sha256(str(directory).encode() +
                                (directory / 'board_snapshot.py').read_bytes() +
                                (directory / 'board_capture.py').read_bytes() +
                                (directory / 'source_coordinator.py').read_bytes()).hexdigest()
        name = f'_panel_acquisition_{digest}'
        with _LOADER.lock:
            if name not in sys.modules:
                package = types.ModuleType(name)
                package.__path__ = [str(directory)]
                sys.modules[name] = package
            return importlib.import_module(f'{name}.board_capture')

    @staticmethod
    def snapshot_feed_helpers():
        return _load_feed()

    def capture_local(self, source, prior_part, demands, hints):
        if self._explicit_home() is None:
            self.require_scope('snapshot_capture')
        return self.snapshot_modules().capture_local(self, source, prior_part, demands, hints)

    def capture_external(self, demands, refs, prior_parts, *, hints, raw=None):
        if self._explicit_home() is None:
            self.require_scope('snapshot_capture')
        return self.snapshot_modules().capture_external(self, demands, refs, prior_parts, hints, raw=raw)

    def _links_inventory(self, ksa):
        """Reuse strict origin rows only while their pre-read source certificate holds.

        Core's fingerprint includes board change cursors and ledger/hub inputs.
        State is separate because compression aliases are deliberately omitted
        from that fingerprint. Inherit the strict core entry's ORIGINAL expiry:
        a fresh outer read must never renew an already-aged inner answer.
        Thread-local ownership prevents cross-lane publication races; scope in
        the key prevents lending a profile's inventory to another profile.
        """
        from copy import deepcopy
        fingerprint = getattr(ksa, '_links_fingerprint', None)
        scope = getattr(ksa, '_origin_scope', None)
        cache = getattr(ksa, '_links_cache', None)
        if not callable(fingerprint) or not callable(scope) or not isinstance(cache, dict):
            return ksa.remote_links_and_origin_rows(stale_ok=False)
        key = (scope(), fingerprint(), self._stat(self.state_db()))
        now = time.monotonic()
        hit = getattr(self._local, 'links_inventory', None)
        if hit is not None and hit[0] == key and now < hit[1]:
            return deepcopy(hit[2])
        rows = ksa.remote_links_and_origin_rows(stale_ok=False)
        # Core returns shallow list copies. Only borrow expiry when the published
        # entry still contains these exact row objects; a racing replacement or
        # an annotation-pinned older answer must not borrow a newer certificate.
        entry = ksa._links_cache.get(key[0])
        certified = (entry is not None and entry[1:3] == key[:2]
                     and all(len(a) == len(b) and all(x is y for x, y in zip(a, b))
                             for a, b in zip(rows, entry[3:5])))
        # Never absorb an input write racing the projection as answered.
        if certified and key == (scope(), fingerprint(), self._stat(self.state_db())):
            self._local.links_inventory = (key, min(entry[0], now + 15.0), deepcopy(rows))
        else:
            self._local.links_inventory = None
        return rows

    def external_capture(self, held, *, include_inventory=False):
        """Materialize canonical external inputs once, then partition by chat.

        Strict reads are essential here: stale-while-revalidate may return the old
        value and consume the only notification for a committed receipt.
        """
        if self._explicit_home() is not None:
            return self._explicit_external_capture(held, include_inventory=include_inventory)
        self.require_scope('external_inventory')
        if not held:
            return {}  # hidden panels do not admit a global ledger projection
        from hermes_cli import kanban_session_activity as ksa, kanban_db_sync as sync
        from hermes_cli import kanban_chat_links as links
        cards, history = self._links_inventory(ksa)
        rows = list(cards) + list(history)
        replica, gateway, profile = None, None, None
        if sync.sync_config().get('links_reader') == 'replica':
            from hermes_cli.profiles import get_active_profile_name
            gateway, profile = self.ksc.load_identity().gateway, get_active_profile_name()
            ids = list(dict.fromkeys(i for value in held.values() for i in value['lineage']))
            unreadable = []
            replica = links.replica_cards(
                ids, gateway,
                tuple(ksa.LISTED_STATUSES) + ('done', 'archived'),
                stale_ok=False, origin_cards=cards, unreadable_out=unreadable)
            rows.extend(replica)
            if unreadable:
                raise RuntimeError('session-panel external replica capture unavailable')
        # Held parents can belong to a different chat. Match their known owner
        # coordinates, not a bare ID shared by unrelated gateways/boards.
        refs = {s: self._external_refs(value) for s, value in held.items()}
        def refers(session, row):
            return any(all(not coordinate or not row.get(key) or row[key] == coordinate
                           for key, coordinate in scope.items())
                       for scope in refs[session].get(row.get('id'), ()))
        # Retain every field, including evidence timestamps: they affect lease
        # validity. No presentation classifier or guessed volatile-field filter.
        captured = {s: tuple(sorted(json.dumps(row, sort_keys=True, separators=(',', ':'))
                                for row in rows
                                if set(value['lineage']).intersection(
                                    (row.get('session'), row.get('origin_session'), row.get('resolved_session')))
                                or refers(s, row)))
                for s, value in held.items()}
        if include_inventory:
            return captured, {'origin': cards, 'history': history, 'replica': replica,
                              'gateway': gateway, 'profile': profile}
        return captured

    def _explicit_external_capture(self, held, *, include_inventory=False):
        if not held:
            return {}
        ident = self._home_identity()
        gateway, profile = ident.get('gateway'), ident.get('profile')
        cfg = self._home_sync_config()
        history = self._origin_rows()
        cards = [row for row in history if not row.get('released')]
        replica = None
        rows = list(cards) + list(history)
        if cfg.get('links_reader') == 'replica':
            if not gateway:
                self._refuse('external_inventory')
            from hermes_cli import kanban_chat_links as links
            from hermes_cli import kanban_session_activity as ksa
            ids = list(dict.fromkeys(i for value in held.values() for i in value['lineage']))
            unreadable = []
            shadows = [(slug, self._shadow_path(slug)) for slug, _ in self.boards()]
            replica = links.replica_cards(
                ids, gateway,
                tuple(ksa.LISTED_STATUSES) + ('done', 'archived'),
                replicas=shadows, stale_ok=False, origin_cards=cards,
                unreadable_out=unreadable, profile=profile, stale_after_s=_explicit_home_mod().stale_after(cfg))
            rows.extend(replica)
            if unreadable:
                raise RuntimeError('session-panel external replica capture unavailable')
        refs = {s: self._external_refs(value) for s, value in held.items()}

        def refers(session, row):
            return any(all(not coordinate or not row.get(key) or row[key] == coordinate
                           for key, coordinate in scope.items())
                       for scope in refs[session].get(row.get('id'), ()))

        captured = {s: tuple(sorted(json.dumps(row, sort_keys=True, separators=(',', ':'))
                                for row in rows
                                if set(value['lineage']).intersection(
                                    (row.get('session'), row.get('origin_session'), row.get('resolved_session')))
                                or refers(s, row)))
                for s, value in held.items()}
        if include_inventory:
            return captured, {'origin': cards, 'history': history, 'replica': replica,
                              'gateway': gateway, 'profile': profile}
        return captured

    def diagnostic_stale(self, held):
        """Compare extra source inputs with their own hydration snapshot.

        Demand acquisition is not a source change. Comparing capture-to-capture
        signatures would re-admit every chat after pre-hydration bootstrap.
        No reader opens until an already hydrated local identity demands it.
        """
        paths = None
        memo, stale = {}, set()
        for session, value in held.items():
            for tid, rec in value.get('bundle', {}).get('cards', {}).items():
                detail = rec.get('detail', {})
                if detail.get('source') != 'live' or detail.get('task', {}).get('id') != tid:
                    continue
                board = rec.get('board', 'default')
                refs = tuple(sorted({tid, *_diagnostic_refs(detail)}))
                identity = (board, tid, refs)
                if identity not in memo:
                    if paths is None:
                        paths = dict(self.boards())
                    c = self.conn(paths[board]) if board in paths else None
                    memo[identity] = self._diagnostic_signature(c, board, tid, refs) if c else None
                if memo[identity] is None or memo[identity] != detail.get('actionability_inputs'):
                    stale.add(session)
        return stale

    @staticmethod
    def _diagnostic_signature(c, board, tid, refs):
        """Bounded exact-ID reads for inputs outside core semantic triggers."""
        facts = []
        try:
            mode = bool(c.execute("SELECT 1 FROM kanban_sync_state WHERE mode!='off' LIMIT 1").fetchone())
        except sqlite3.Error:
            mode = 'unavailable'
        try:
            facts.append([tuple(row) for row in c.execute(
                'SELECT cmd FROM task_wake_checks WHERE task_id=?', (tid,))])
        except sqlite3.Error:
            facts.append('unavailable')
        for ref in refs:
            try:
                pending = c.execute("SELECT * FROM kanban_failure_commands WHERE task_id=? AND operation_kind='retain' "
                    "AND (delivery_state IS NULL OR delivery_state NOT IN ('confirmed','rejected')) "
                    'ORDER BY run_uid,request_uid LIMIT 2', (ref,))
                facts.append([ref, [{k: row[k] for k in row.keys() if k in (
                    'run_uid', 'request_uid', 'request_sha256', 'operation_kind',
                    'delivery_state', 'canonical_receipt_json')} for row in pending]])
            except sqlite3.Error:
                facts.append([ref, 'unavailable'])
        facts.append(mode)
        return json.dumps({'diagnostic_inputs': facts, 'board': board, 'id': tid},
                          sort_keys=True, separators=(',', ':'))

    def snapshot_select_external(self, inventory, ids, *, observed_at):
        """Select from the one strict inventory using the existing canonical helpers."""
        if self._explicit_home() is None:
            self.require_scope('external_inventory')
        from hermes_cli import kanban_session_activity as ksa
        from hermes_cli.kanban_origin_obligations import preserve_origin_obligations
        from hermes_cli.kanban_execution_observations import add_cards
        outputs = {}
        for terminal in (False, True):
            statuses = tuple(ksa.LISTED_STATUSES) + (('done', 'archived') if terminal else ())
            if inventory['replica'] is None:
                selected = inventory['origin']
            else:
                selected = preserve_origin_obligations(
                    [c for c in inventory['replica'] if c['session'] in ids], inventory['origin'],
                    gateway=inventory['gateway'], profile=inventory['profile'], ids=list(ids), statuses=statuses)
            values = add_cards(_project_remote_listed(selected, ids, statuses), now=observed_at,
                               **({'gateway': inventory.get('gateway'), 'profile': inventory.get('profile')}
                                  if self._explicit_home() is not None else {}))
            if terminal:
                values = _load_feed().with_terminal_history(values, inventory['history'], ids)
            outputs['done' if terminal else 'linked'] = _calm(values)
        return outputs

    @staticmethod
    def _external_refs(held):
        bundle = held.get('bundle') or {}
        rows = list(bundle.get('tasks', ())) + list(bundle.get('done', ()))
        for card_id, record in bundle.get('cards', {}).items():
            task = record.get('detail', {}).get('task', {})
            rows.append({**task, 'id': task.get('id') or card_id,
                         **{key: record[key] for key in ('gateway', 'profile', 'board')
                            if record.get(key)}})
        refs = {}
        for row in rows:
            if row.get('id') in held['refs']:
                refs.setdefault(row['id'], []).append(
                    {key: row.get(key) for key in ('gateway', 'profile', 'board')})
        # Old/restored bundles may lack hydrated scope. Preserve invalidation
        # until hydration supplies it; absence is not proof of another owner.
        for card_id in held['refs']:
            refs.setdefault(card_id, [{}])
        return refs

    @staticmethod
    def _pull_observation(row):
        # canonical hub projections have no source='replica' tag. Only these
        # known producers define synced_at as an observation, not task evidence.
        return row.get('source') == 'replica' or (
            row.get('remote') is True and row.get('via') in ('shepherd', 'origin'))

    @staticmethod
    def external_semantic(captured):
        """Replica pull time is observation metadata, not a changed card.

        Keep stale, executor evidence and every ledger field. The raw capture is
        retained separately so advance_external can carry the actual observation
        into the reducer; dropping its clock would prematurely expire evidence.
        """
        if captured is None:
            return None
        rows = [json.loads(row) for row in captured]
        for row in rows:
            if CoreReaders._pull_observation(row):
                row.pop('synced_at', None)
        return tuple(sorted(json.dumps(row, sort_keys=True, separators=(',', ':')) for row in rows))

    @staticmethod
    def advance_external(bundle, captured):
        """Pure observation carriage; never renew board reads or execution leases."""
        def identity(row):
            # Selection adds source='origin-obligation' to canonical rows. It
            # does not change the observation producer; board replicas do.
            producer = 'replica' if row.get('source') == 'replica' else row.get('via')
            binding = tuple(row.get(k) for k in (
                'origin_gateway', 'origin_profile', 'origin_session', 'binding_id')) if producer == 'origin' else ()
            return tuple(row.get(k) for k in ('gateway', 'profile', 'board', 'id', 'session')) + (producer,) + binding
        rows = [json.loads(row) for row in captured]
        clocks = {identity(row): row['synced_at'] for row in rows
                  if CoreReaders._pull_observation(row) and 'synced_at' in row}
        result = dict(bundle)
        for field in ('tasks', 'done'):
            result[field] = [{**row, 'synced_at': clocks[identity(row)]}
                             if CoreReaders._pull_observation(row) and identity(row) in clocks else row
                             for row in bundle[field]]
        return result

    def external_stamps(self):
        return tuple((str(p), self._stat(p)) for p in self.external_paths())

    def _stamps_of(self, paths):
        from hermes_cli.session_list_flight import data_stamp
        return tuple(data_stamp(p) or self._stat(p) for p in paths)

    def board_changes(self, cursor):
        """Per-source semantic changes, not an event-id high-water mark.

        Never commit a cursor or file stamp after failed capture. Each source is
        read in one owned query-only transaction and closed before invalidation.
        FULL (coverage loss, reset, replacement) conservatively invalidates all
        watched sessions, including sessions gaining their first membership.
        """
        from hermes_cli import kanban_task_change as changes
        from hermes_cli.kanban_session_activity import _counted_sub_clause
        previous = cursor or {}
        fresh = {}
        out = BoardChanges()
        paths = {str(p): slug for slug, p in self.boards()}
        paths.update({str(self._shadow_path(slug)): slug for slug, _ in self.boards()})
        present = {str(p) for p in self.files()}
        if set(previous) - present:
            out.full = True  # removal can retract rows; never retain a vanished source cursor
        for key in sorted(present):
            path = Path(key)
            stamp = self._stat(path)
            old = previous.get(key)
            if old is not None and old[0] == stamp:
                fresh[key] = old
                continue
            with contextlib.closing(sqlite3.connect(f'file:{path}?mode=rw', uri=True, timeout=0.5)) as c:
                c.execute('PRAGMA query_only=ON')
                c.row_factory = sqlite3.Row
                c.execute('BEGIN')
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                # This canonical parent-evidence table is not in core's task
                # revision trigger set. Capture it in the SAME transaction and
                # invalidate children even when no hydrated parent row exists.
                remote = {r[0]: tuple(r[1:]) for r in c.execute(
                    'SELECT parent_id,status,refreshed_at FROM kanban_sync_remote_parents')} if 'kanban_sync_remote_parents' in tables else {}
                old_remote = old[2] if old and len(old) > 2 else {}
                remote_ids = {pid for pid in set(remote) | set(old_remote) if remote.get(pid) != old_remote.get(pid)}
                if remote_ids:
                    affected = set(remote_ids)
                    ordered_parents = sorted(remote_ids)
                    for at in range(0, len(ordered_parents), 400):
                        part = ordered_parents[at:at + 400]
                        marks = ','.join('?' for _ in part)
                        affected.update(r[0] for r in c.execute(
                            f'SELECT child_id FROM task_links WHERE parent_id IN ({marks})', part))
                    out.sources.setdefault((key, paths.get(key, path.stem)), set()).update(affected)
                ids, next_cursor = changes.changed_since(c, old[1] if old else None)
                if ids is changes.FULL:
                    out.full = True
                elif isinstance(ids, frozenset) and ids:
                    out.sources.setdefault((key, paths.get(key, path.stem)), set()).update(ids)
                    # Current membership discovers brand-new subscribers/creators;
                    # old membership is represented by the held bundle's refs.
                    # Chunk for SQLite parameter limits, not a truncated event page.
                    ordered = sorted(ids)
                    for at in range(0, len(ordered), 400):
                        part = ordered[at:at + 400]
                        marks = ','.join('?' for _ in part)
                        out.sessions.update(r[0] for r in c.execute(
                            f'SELECT session_id FROM tasks WHERE id IN ({marks})', part) if r[0])
                        if 'kanban_notify_subs' in tables:
                            out.sessions.update(r[0] for r in c.execute(
                                "SELECT s.chat_id FROM kanban_notify_subs s JOIN tasks t ON t.id=s.task_id "
                                "WHERE s.platform='tui' AND "
                                f'{_counted_sub_clause(c)} AND s.task_id IN ({marks})', part) if r[0])
                        if 'task_chat_links' in tables:
                            # Invalidation is deliberately a superset of the
                            # canonical remote membership selector: never miss a
                            # new origin/shepherd with no held reference yet.
                            out.sessions.update(r[0] for r in c.execute(
                                f'SELECT chat_session FROM task_chat_links WHERE task_id IN ({marks})', part) if r[0])
                c.rollback()
            # Do not swallow a concurrent write by adopting an after-read stamp.
            # Retaining the before-read stamp guarantees another pass sees it.
            fresh[key] = (stamp, next_cursor, remote)
        return out, fresh

    def changed_cards(self, cursor):
        """({card id: its chat or None} with an event since `cursor`, new cursor). The first call only sets it.
        A file whose events went backwards (replaced) restarts its cursor."""
        cursor = dict(cursor or {})
        changed: dict[str, Optional[str]] = {}
        for path in self.files():
            c = self.conn(path)
            if c is None:
                continue
            key = str(path)
            try:
                top = c.execute('SELECT coalesce(max(id), 0) FROM task_events').fetchone()[0]
                if key not in cursor or top < cursor[key]:
                    cursor[key] = top
                    continue
                # Heartbeats change nothing a bundle shows (the full refresh renews run evidence every FULL_EVERY_S).
                rows = c.execute("SELECT e.id, e.task_id, e.kind, t.session_id FROM task_events e LEFT JOIN tasks t "
                                 "ON t.id = e.task_id WHERE e.id > ? ORDER BY e.id LIMIT 20000", (cursor[key],)).fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                if r['kind'] != 'heartbeat':
                    changed[str(r['task_id'])] = r['session_id'] or changed.get(str(r['task_id']))
            if rows:
                cursor[key] = rows[-1]['id']
        return changed, cursor

    def lineage(self, session: str) -> list[str]:
        scope = getattr(self._local, 'batch', None)
        if scope is not None and session in scope['lineages']:
            result = scope['lineages'][session]
            if isinstance(result, Exception):
                raise result
            return list(result)
        c = self.conn(self.state_db())
        out, cur = [], session
        while c is not None and cur and cur not in out and len(out) < 50:
            out.append(cur)
            try:
                row = c.execute('SELECT parent_session_id FROM sessions WHERE id = ?', (cur,)).fetchone()
            except sqlite3.Error:
                break
            cur = row['parent_session_id'] if row else None
        return out or [session]

    @contextmanager
    def batch(self, sessions):
        """Thread/pass-local hydration only. Nothing survives this context or crosses profiles/requests.

        Local membership is still core's creator/subscriber selector; remote inventory is cached BEFORE
        core's lineage-key dedup, so a card linked to two chats remains in both. Errors are not memoized.
        """
        previous = getattr(self._local, 'batch', None)
        if previous is not None:
            yield
            return
        scope = {'lineages': {}, 'local': {}, 'remote': {}, 'cards': {}}
        self._local.batch = scope
        try:
            for s in sessions:
                try:
                    scope['lineages'][s] = self.lineage(s)
                except Exception as e:
                    scope['lineages'][s] = e
            scope['ids'] = list(dict.fromkeys(i for v in scope['lineages'].values()
                                              if isinstance(v, list) for i in v[:50]))
            yield
        finally:
            self._local.batch = previous

    def _batch_scope(self):
        scope = getattr(self._local, 'batch', None)
        if scope is not None:
            # A pass is one bounded snapshot, not a global cache. Content writes are seen by the next tick;
            # unrelated state.db traffic must not discard shared board work. Replacements invalidate at once.
            stamp = []
            for path in self.files():
                try:
                    st = os.stat(path)
                    stamp.append((str(path), st.st_dev, st.st_ino))
                except FileNotFoundError:
                    stamp.append((str(path), None))
            if scope.get('stamp') != stamp:
                for name in ('local', 'remote', 'cards'):
                    scope[name].clear()
                scope.pop('history', None)
                scope.pop('local_failed', None)
                scope['stamp'] = stamp
        return scope

    def _session_tasks(self, ids, statuses):
        if self._explicit_home() is not None:
            return self._explicit_session_tasks(ids, statuses)
        self.require_scope('local_card_scope')
        from hermes_cli import kanban_session_activity as ksa
        scope = self._batch_scope()
        if (scope is None or len(scope['lineages']) < 2 or len(scope['ids']) > 400
                or not set(ids) <= set(scope['ids'])):
            return ksa.session_tasks(ids, statuses, stale_ok=True)
        from hermes_cli.kanban_session_outcome_tasks import select_listed_local_tasks
        from hermes_cli.kanban_session_activity_release import PENDING_RELEASE_STATUSES
        from hermes_cli.kanban_session_activity_card_details import annotate_remote_upstream
        from hermes_cli.kanban_execution_observations import add_cards
        out = []
        if scope.get('local_failed'):
            return ksa.session_tasks(ids, statuses, stale_ok=True)
        try:
            for path in ksa._board_db_paths():
                key = (str(path), statuses)
                if key not in scope['local']:
                    # One attempt, no changing union key in core's bounded last-good lineage memo.
                    rows = ksa._read_session_tasks(path, scope['ids'], statuses)
                    if any(row.get('_execution_error') for row in rows) and not self._primary_absence(ksa, path, rows):
                        raise RuntimeError('session-panel union execution evidence unavailable')
                    scope['local'][key] = {r['id']: r for r in rows}
                rows = scope['local'][key]
                c = self.conn(path)
                if c is None:
                    raise RuntimeError('session-panel membership board unavailable')
                members = select_listed_local_tasks(c, ids, statuses)
                # A writer may have changed membership after the union read; never omit a new card.
                if not members.keys() <= rows.keys():
                    return ksa.session_tasks(ids, statuses, stale_ok=True)
                board = ksa._board_slug(path)
                selected = []
                for tid, member in members.items():
                    row = copy.deepcopy(rows[tid])
                    row.update(via=member['via'])
                    selected.append(row)
                # Refresh exactly the memo core would have stored for this lineage, before evidence projection.
                projected = ksa._busy.session_tasks(path, ids, statuses, lambda *_: selected)
                for row in projected:
                    row.setdefault('key', f"{board}/{row['id']}")
                    out.append(row)
            remote = _project_remote_listed(self._remote_inventory(scope, ids, statuses), ids, statuses)
            return add_cards(out + remote)
        except Exception:
            # Preserve stable per-lineage busy memos and per-session failure isolation on batch read failure.
            scope['local_failed'] = True
            log.debug('session-panel shared read unavailable; retrying lineage', exc_info=True)
            return ksa.session_tasks(ids, statuses, stale_ok=True)

    def _primary_absence(self, ksa, path, rows) -> bool:
        """True when every `_execution_error` in a union read is a PRIMARY mirror's ordinary answer, not a failed
        evidence read. On a primary mirror core attests only a card whose current run is live there and marks every
        other card (parked, queued, done) unknown, on every read: a per-lineage read returns the same rows, so the
        union is as good. Asked again here for exactly the marked cards: evidence found now (the union's own read
        failed), a board in another mode, or any error keeps the per-lineage fallback."""
        try:
            from hermes_cli import kanban_primary_execution as kpe
            from hermes_cli.kanban_execution_observations import card_scope
            c = self.conn(path)
            if c is None:
                return False
            try:
                ksa._require_local_execution_authority(c)
                return False  # this gateway owns the board: a marked card there is a failed read
            except kpe.PrimaryMirror:
                pass
            marked = {r['id']: dict(r) for r in rows if r.get('_execution_error')}
            gateway, profile = card_scope(path)
            return not kpe.attach(c, marked, gateway=gateway, profile=profile, board=ksa._board_slug(path))
        except Exception:
            log.debug('session-panel primary-mirror check failed', exc_info=True)
            return False

    def _explicit_session_tasks(self, ids, statuses):
        from hermes_cli import kanban_session_activity as ksa
        from hermes_cli.kanban_execution_observations import add_cards
        ident = self._home_identity()
        scope = (ident.get('gateway'), ident.get('profile'))
        out = []
        for slug, path in self.boards():
            path = Path(path)
            if not path.is_file():
                continue
            rows = ksa._read_session_tasks(path, list(ids), statuses, scope=scope)
            for row in rows:
                row.setdefault('key', f"{slug}/{row['id']}")
                out.append(row)
        remote = self._explicit_remote_inventory(ids, statuses)
        return add_cards(out + remote, gateway=scope[0], profile=scope[1])

    def _explicit_remote_inventory(self, ids, statuses):
        cfg = self._home_sync_config()
        ident = self._home_identity()
        if cfg.get('links_reader') == 'replica':
            if not ident.get('gateway'):
                self._refuse('external_inventory')
            from hermes_cli import kanban_chat_links as links
            shadows = [(slug, self._shadow_path(slug)) for slug, _ in self.boards()]
            origin = [row for row in self._origin_rows() if not row.get('released')]
            return links.replica_cards(
                list(ids), ident['gateway'], statuses, replicas=shadows,
                stale_ok=True, origin_cards=origin, profile=ident.get('profile'),
                stale_after_s=_explicit_home_mod().stale_after(cfg))
        rows = self._origin_rows()
        wanted = set(ids)
        return [row for row in rows if not row.get('released')
                and wanted.intersection((row.get('session'), row.get('origin_session'), row.get('resolved_session')))]

    def _remote_inventory(self, scope, ids, statuses):
        """Share replica hydration, not ids-dependent origin-obligation dedup or failure decisions."""
        if self._explicit_home() is not None:
            return self._explicit_remote_inventory(ids, statuses)
        self.require_scope('external_inventory')
        from hermes_cli import kanban_session_activity as ksa, kanban_db_sync as sync
        from hermes_cli import kanban_chat_links as links
        from hermes_cli.kanban_origin_obligations import preserve_origin_obligations
        from hermes_cli.profiles import get_active_profile_name
        if sync.sync_config().get('links_reader') != 'replica':
            if statuses not in scope['remote']:
                scope['remote'][statuses] = ksa._remote_cards(scope['ids'], statuses, True)
            return scope['remote'][statuses]
        if statuses not in scope['remote']:
            # A ledger error must retain core's per-lineage empty/partial failure policy, not a union success.
            try:
                gateway = self.ksc.load_identity().gateway
                origin = ksa._remote_links(stale_ok=True)
            except Exception:
                return ksa._remote_cards(ids, statuses, True)
            selected = links.replica_cards(scope['ids'], gateway, statuses, stale_ok=True, origin_cards=origin)
            scope['remote'][statuses] = (selected, origin, gateway, get_active_profile_name())
        selected, origin, gateway, profile = scope['remote'][statuses]
        selected = [c for c in selected if c['session'] in ids]
        return preserve_origin_obligations(selected, origin, gateway=gateway, profile=profile,
                                           ids=ids, statuses=statuses)

    def panel_stamp(self, session):
        """Cheap per-session outcome/request/membership invalidation; no board hydration.

        The third value moves when the chat's row appears, disappears or changes parent: a lineage
        change (attach, detach, deleted ancestor) moves no outcome and no request of the held lineage."""
        c = self.conn(self.state_db())
        try:
            row = c.execute('SELECT outcome_json,parent_session_id FROM sessions WHERE id=?', (session,)).fetchone() if c else None
            outcome = row['outcome_json'] if row else None
            member = (row['parent_session_id'] or '') if row else None
        except sqlite3.Error:
            outcome = member = None
        c = self.conn(self.state_db().parent / 'user-steps' / 'actions.sqlite3')
        try:
            requests = tuple(tuple(r) for r in c.execute('SELECT id,revision,state,resolution FROM requests WHERE session=? ORDER BY id', (session,))) if c else ()
        except sqlite3.Error:
            requests = None
        return (outcome, requests, member)

    def panel_stamps(self, sessions):
        """`panel_stamp` for many chats in two reads per 400 (not two per chat): {session: (outcome, requests,
        membership)}, every asked chat present, each value exactly what `panel_stamp` returns for it."""
        ids = list(dict.fromkeys(str(s) for s in sessions))
        outcomes: dict = {}
        members: dict = {}
        requests: dict = {s: [] for s in ids}
        state = self.conn(self.state_db())
        actions = self.conn(self.state_db().parent / 'user-steps' / 'actions.sqlite3')
        for at in range(0, len(ids), 400):
            part = ids[at:at + 400]
            marks = ','.join('?' * len(part))
            try:
                if state:
                    for row in state.execute(f'SELECT id,outcome_json,parent_session_id FROM sessions WHERE id IN ({marks})', part):
                        outcomes[row['id']] = row['outcome_json']
                        members[row['id']] = row['parent_session_id'] or ''
            except sqlite3.Error:
                for s in part:
                    outcomes.pop(s, None)
                    members.pop(s, None)
            try:
                if actions:
                    for row in actions.execute('SELECT session,id,revision,state,resolution FROM requests '
                                               f'WHERE session IN ({marks}) ORDER BY id', part):
                        row = tuple(row)
                        requests[row[0]].append(row[1:])
            except sqlite3.Error:
                for s in part:
                    requests[s] = None
        return {s: (outcomes.get(s), None if requests[s] is None else tuple(requests[s]), members.get(s)) for s in ids}

    def board_stamps(self):
        """`stamps()` for the board files alone (no state.db)."""
        return self._stamps_of(self.files())

    def watch_paths(self) -> list[Path]:
        """Every file a bundle is computed from: a write to any of them is what makes a pass due."""
        return self.board_paths() + [self.state_db(), self.state_db().parent / 'user-steps' / 'actions.sqlite3'] + self.external_paths()

    def external_paths(self) -> list[Path]:
        """Origin receipts and hub replicas are inputs, not task-event boards."""
        if self._explicit_home() is not None:
            home = self._explicit_home().home
            return [home / 'kanban' / 'origin-ledger.db', home / 'origin-work.db']
        from hermes_constants import get_hermes_home
        from hermes_cli.kanban_db import kanban_home
        from hermes_cli.kanban_capability_registry import hub_db_path
        return [hub_db_path(), Path(get_hermes_home()) / 'kanban' / 'origin-ledger.db',
                kanban_home() / 'origin-work.db']

    def publication_paths(self):
        # remote_snapshot is a derived output of the canonical reader itself,
        # not a new input. Keep it on older-core watches, but with keyed recovery
        # its cache-only arrivals are covered by the existing 30s reconciliation.
        if self._explicit_home() is not None:
            return {self._explicit_home().home / 'origin-work.db'}
        from hermes_cli.kanban_db import kanban_home
        return {kanban_home() / 'origin-work.db'}

    def watch_directories(self):
        from hermes_cli import kanban_db, kanban_db_sync
        if self._explicit_home() is not None:
            root = self._explicit_home().home
            boards = root / 'kanban' / 'boards'
        else:
            root = kanban_db.kanban_home()
            boards = kanban_db.boards_root()
        # Directory-entry events discover new named/link-only boards. Their
        # writes are then watched by exact file name; unrelated logs don't wake us.
        roots = [root, boards, root / 'kanban' / kanban_db_sync.LINKS_DIR,
                 root / 'kanban' / kanban_db_sync.SHADOW_DIR]
        if boards.is_dir():
            roots.extend(p for p in boards.iterdir() if p.is_dir())
        return roots

    def panel_files_stamp(self):
        """Moves with every committed write to either file `panel_stamps` reads (stat only, nothing opened)."""
        from hermes_cli.session_list_flight import data_stamp
        return (data_stamp(self.state_db()) or self._stat(self.state_db()), self.decision_store_stamp())

    @staticmethod
    def _stat(base):
        out = []
        for f in (base, Path(f'{base}-wal')):
            try:
                st = os.stat(f)
                out.append((st.st_ino, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def decision_store_stamp(self):
        """Moves with every committed write to the user-steps store (requests, operations, deliveries)."""
        return self._stat(self.state_db().parent / 'user-steps' / 'actions.sqlite3')

    def user_step_requests(self, session, lineage):
        """Presentation never runs producer reconciliation or initializes stores."""
        return self._user_step_requests(session, lineage, reconcile=False)

    # The feed admits the producer per chat (inputs that moved), not for every chat on any source write.
    reconcile_scoped = True
    reconcile_min_interval_s = 3.0
    reconcile_safety_s = 30.0

    def reconciliation_stamp(self):
        """Source admission only: action-store writes cannot trigger their producer."""
        return self._stamps_of(self.board_paths() + [self.state_db()] + self.external_paths())

    reconcile_budgeted = True  # reconcile_sources honours a deadline and names the chats it did not reach

    def reconcile_sources(self, sessions, deadline=None):
        """Run the canonical writer only for source-admitted sessions, before reads.

        This producer is deliberately separate from both presentation lanes. It
        still uses canonical per-owner reconciliation; it is not a batched source
        capture API. With a monotonic ``deadline`` it stops starting chats once it
        has passed (at least one always runs) and lists the rest in
        ``reconcile_unreached``: the feed carries them to its next pass.
        """
        if self._explicit_home() is not None:
            ident = self._home_identity()
            if not ident.get('install_id'):
                self._refuse('user_steps')
            # Presentation never reconciles. The canonical producer lane still requires a
            # home-local Mirror; until that adapter exists, do not call process-home Mirror.
            self.reconcile_unreached = list(sessions)
            self.reconcile_timings = []
            return set(self.reconcile_unreached)
        self.require_scope('user_steps')
        api = self._user_step_api()
        deferred = set()
        timings = []
        sessions = list(sessions)
        self.reconcile_unreached = []
        for i, session in enumerate(sessions):
            if deadline is not None and i and time.monotonic() >= deadline:
                self.reconcile_unreached = sessions[i:]
                break
            # One chat's unavailable source must not fail every other chat's pass (this phase runs
            # before all hydration). The feed retries what is returned here; it is never certified.
            began = time.monotonic()
            began_ns, began_cpu = time.monotonic_ns(), time.thread_time_ns()
            preflight_ns, preflight_cpu = began_ns, began_cpu
            stage_timing = None
            try:
                home, lineage = self.state_db().parent, self.lineage(session)
                preflight_ns, preflight_cpu = time.monotonic_ns(), time.thread_time_ns()
                left = api.panel_reconcile(home, session, lineage)
                stage_timing = getattr(left, 'panel_timing', None)
            except Exception as exc:
                stage_timing = getattr(exc, 'panel_timing', None)
                left = [f'{type(exc).__name__}: {exc}']
            if stage_timing is not None:
                total_ns, total_cpu = time.monotonic_ns() - began_ns, time.thread_time_ns() - began_cpu
                stage_timing = dict(stage_timing, reader_wall_ns=total_ns, reader_cpu_ns=total_cpu,
                                    preflight_wall_ns=preflight_ns - began_ns,
                                    preflight_cpu_ns=preflight_cpu - began_cpu,
                                    reader_residual_wall_ns=total_ns - (preflight_ns - began_ns) - stage_timing['wall_ns'],
                                    reader_residual_cpu_ns=total_cpu - (preflight_cpu - began_cpu) - stage_timing['cpu_ns'])
            timings.append({'session': session, 'reconcile_s': round(time.monotonic() - began, 4),
                            'at': time.time(), 'unchanged': bool(getattr(left, 'unchanged', False)),
                            'deferred': bool(left), 'stage_timing': stage_timing})
            if left:
                deferred.add(session)
                log.debug('session-panel source reconciliation deferred: %s: %s', session, left)
        self.reconcile_timings = timings
        return deferred

    def reconcile_user_steps(self, session, lineage):
        return self._user_step_requests(session, lineage, reconcile=True)

    def decision_inputs(self, session, lineage):
        """Read authoritative owner-scoped decisions, with no unrelated board reconciliation."""
        if self._explicit_home() is None:
            self.require_scope('user_steps')
        c = self.conn(self.state_db())
        if c is None:
            raise RuntimeError('session outcome unavailable')
        row = c.execute('SELECT outcome_json FROM sessions WHERE id=?', (session,)).fetchone()
        if row is None:
            # user-steps cannot establish ownership without the watched session's
            # own stored row and correctly rejects that request with 404. Do not
            # call it for a known-deleted recent chat or infer an ancestor owner.
            return {'outcome_json': {}, 'requests': []}
        try:
            outcome = json.loads(row['outcome_json'] or '{}')
        except (TypeError, ValueError):
            outcome = {}
        return {'outcome_json': outcome if isinstance(outcome, dict) else {},
                'requests': self._user_step_requests(session, lineage, reconcile=False)}

    @staticmethod
    def _user_step_api():
        path = Path(__file__).resolve().parents[2] / 'user-steps' / 'dashboard' / 'plugin_api.py'
        key = '_session_panel_user_steps_api_' + str(path) + ':' + str(path.stat().st_mtime_ns)
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, path)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod = sys.modules.setdefault(key, mod)
        return mod

    def _user_step_requests(self, session, lineage, *, reconcile):
        if self._explicit_home() is not None:
            ident = self._home_identity()
            if not ident.get('install_id'):
                self._refuse('user_steps')
            mod = self._user_step_api()
            return mod.panel_requests(
                self.state_db().parent, session, lineage, reconcile=reconcile,
                install_id=ident['install_id'], profile=ident.get('profile'), bound=True)
        self.require_scope('user_steps')
        mod = self._user_step_api()
        if reconcile:
            return mod.panel_requests(self.state_db().parent, session, lineage)
        scope = getattr(self._local, 'batch', None)
        if scope is not None and scope['lineages'].get(session) == lineage:
            if 'request_capture' not in scope:
                groups = {s: ids for s, ids in scope['lineages'].items() if isinstance(ids, list)}
                try:
                    scope['request_capture'] = mod.panel_requests_many(self.state_db().parent, groups)
                except Exception as exc:
                    # Share failure as failure for this pass, never as empty rows;
                    # the next source-admitted pass gets a fresh capture attempt.
                    scope['request_capture'] = exc
            captured = scope['request_capture']
            value = captured if isinstance(captured, Exception) else captured[session]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        return mod.panel_requests(self.state_db().parent, session, lineage, reconcile=False)

    def panel_inputs(self, session, lineage):
        if self._explicit_home() is None:
            self.require_scope('panel_runtime_inputs')
        path = Path(__file__).resolve().parents[1] / 'inputs.py'
        spec = importlib.util.spec_from_file_location('_session_panel_inputs', path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.read(self, session, lineage)

    def linked(self, ids):
        if self._explicit_home() is None:
            self.require_scope('local_card_scope')
        from hermes_cli import kanban_session_activity as ksa
        return _calm(self._session_tasks(list(ids)[:50], tuple(ksa.LISTED_STATUSES)))

    def done(self, ids):
        from hermes_cli import kanban_session_activity as ksa
        ids = list(ids)[:50]
        tasks = _calm(self._session_tasks(ids, tuple(ksa.LISTED_STATUSES) + ('done', 'archived')))
        tasks = self._with_run_stats(tasks)
        if self._explicit_home() is not None:
            history = self._origin_rows()
            return _load_feed().with_terminal_history(tasks, history, ids)
        # The terminal origin-history reader resolves the ambient origin ledger.
        self.require_scope('origin_history')
        reader = getattr(ksa, 'remote_links_and_origin_rows', None)
        if reader is None:
            return tasks
        try:
            scope = getattr(self._local, 'batch', None)
            if scope is not None and 'history' in scope:
                history = scope['history']
            else:
                try:
                    _, history = reader(stale_ok=True)
                except TypeError:
                    _, history = reader()
                if scope is not None:
                    scope['history'] = history
        except Exception:
            log.debug('session-panel feed: origin history unavailable', exc_info=True)
            return tasks
        return _load_feed().with_terminal_history(tasks, history, ids)

    def _with_run_stats(self, tasks):
        """Done rows on this gateway's own boards get `run_stats` (duration, runs, dollars); other rows unchanged."""
        try:
            paths = dict(self.boards())
            feed = _load_feed()
            by_board = {}
            for row in tasks:
                if row.get('status') in ('done', 'archived') and row.get('source') != 'origin-history':
                    by_board.setdefault(str(row.get('board') or 'default'), []).append(row)
            copies = {}
            for board, rows in by_board.items():
                path = paths.get(board)
                c = self.conn(Path(path)) if path is not None and Path(path).exists() else None
                if c is not None:
                    # Copies: the listed rows can be core's memoized objects.
                    for row in feed.with_done_run_stats([dict(r) for r in rows], c):
                        copies[id(next(r for r in rows if r.get('id') == row.get('id')))] = row
            tasks = [copies.get(id(row), row) for row in tasks]
        except Exception:
            log.debug('session-panel feed: done run stats unavailable', exc_info=True)
        return tasks

    def card(self, card_id: str, board: str):
        scope = self._batch_scope()
        if scope is None:
            return self._card(card_id, board)
        key = (card_id, board)
        if key not in scope['cards']:
            value = self._card(card_id, board)
            if value is None:
                return None  # unavailable/missing is not a pass-wide negative cache
            scope['cards'][key] = value
        return copy.deepcopy(scope['cards'][key])

    def _card(self, card_id: str, board: str):
        """A slim `/api/plugins/kanban/tasks/<id>` answer: `{detail, board}`, or None when no board has it."""
        paths = dict(self.boards())
        for slug in dict.fromkeys([board or 'default', 'default']):
            for path, replica in ((paths.get(slug), False), (self._shadow_path(slug), True)):
                if path is None or not Path(path).exists():
                    continue
                c = self.conn(Path(path))
                if c is None:
                    continue
                try:
                    detail = self._detail(c, card_id, slug, replica)
                except sqlite3.Error:
                    detail = None
                if detail is not None:
                    return {'detail': detail, 'board': slug}
        return None

    def _detail(self, c: sqlite3.Connection, card_id: str, board: str, replica: bool,
                *, snapshot_at=None, parent_connection=...):
        from hermes_cli import kanban_db
        task = kanban_db.get_task(c, card_id)
        if task is None:
            return None
        d = dataclasses.asdict(task)
        explicit = self._explicit_home() is not None
        if explicit and parent_connection is ...:
            parent_connection = None  # never discover a process-home replica for contained parents
        try:
            from hermes_cli.kanban_session_wait_rows import _local_wait_info
            d['wait'] = (_local_wait_info(c, [card_id]) if snapshot_at is None and not explicit else
                         _local_wait_info(c, [card_id], now=snapshot_at,
                                          replica=parent_connection)).get(card_id, {})
        except (ImportError, sqlite3.Error):
            d['wait'] = {}
        # Heartbeats move these every minute without changing anything the chain shows: left out, so a heartbeat
        # alone never re-pushes a bundle (the card rows in `tasks` keep their own activity fields).
        d.pop('last_heartbeat_at', None)
        try:
            from hermes_cli import kanban_wake_check as kwc
            d['wake_check'] = kwc.get_state(c, card_id)
        except Exception:
            d['wake_check'] = None
        try:  # core's GET /tasks/<id> adds it (plugins/kanban `_ready_since`): the ready card's "waiting N min"
            cols = {r[1] for r in c.execute('PRAGMA table_info(tasks)')}
            row = c.execute('SELECT ready_since FROM tasks WHERE id = ?', (card_id,)).fetchone() if 'ready_since' in cols else None
            d['ready_since'] = row[0] if row else None
        except sqlite3.Error:
            d['ready_since'] = None
        parents = [r[0] for r in c.execute('SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id', (card_id,))]
        link_tasks = []
        for pid in parents:
            p = kanban_db.get_task(c, pid)
            if p is not None:
                link_tasks.append({'id': p.id, 'title': p.title, 'status': p.status})
        events = [dataclasses.asdict(e) for e in _newest_events(c, card_id)]
        latest = kanban_db.latest_run(c, card_id)
        runs = [dataclasses.asdict(latest)] if latest is not None else []
        for r in runs:
            r.pop('last_heartbeat_at', None)
        # Count of this card's runs that ended in a failure (the panel shows "N failures" beside the run time).
        # Unlike consecutive_failures, an unblock does not reset it. None = the run table could not be read.
        try:
            marks = ','.join('?' for _ in FAILED_RUN_OUTCOMES)
            d['failed_runs'] = int(c.execute(f'SELECT COUNT(*) FROM task_runs WHERE task_id = ? AND outcome IN ({marks})',
                                             (card_id, *FAILED_RUN_OUTCOMES)).fetchone()[0])
        except sqlite3.Error:
            d['failed_runs'] = None
        out = {'task': d, 'events': events, 'runs': runs, 'links': {'parents': parents, 'children': []},
               'link_tasks': link_tasks, 'source': 'replica' if replica else 'live', 'slim': True}
        if replica:
            if self._explicit_home() is None:
                self.require_scope('replica_card_owner')
            try:
                from hermes_cli import kanban_foreign_card as kfc
                owner = kfc.created_on(c, card_id)
                out['owner'] = None if owner is kfc._UNSET else owner
            except Exception:
                out['owner'] = None
        elif explicit:
            # Queue diagnostics are not a captured-home reader yet. Report that gap, never call its ambient resolver.
            out['queue_unavailable'] = {'state': 'unavailable', 'reason': 'explicit_home_adapter_unavailable'}
        elif not _mitigation('HERMES_PANEL_SKIP_QUEUE_POSITION'):
            try:
                from hermes_cli import kanban_queue_position as kqp
                extra = kqp.detail_fields(c, task, board)
                # Stamped with the read time (observed_at, age): the chain reads none of it, and it would make every
                # recompute differ. The card rows in `tasks` carry the evidence the panel judges.
                extra.pop('execution_observations', None)
                if isinstance(extra.get('queue'), dict):
                    # Local queue snapshots carry a fresh read timestamp even when their position is unchanged.
                    extra['queue'] = {k: v for k, v in extra['queue'].items() if k != 'observed_at'}
                out.update(extra)
            except Exception:
                pass
        owned_snapshot = not c.in_transaction
        try:
            if owned_snapshot:
                c.execute('BEGIN')
            from hermes_cli.kanban_actionability_sources import describe
            diagnostic = describe(c, card_id, now=0)
            if isinstance(diagnostic, dict):
                out['actionability'] = {**diagnostic, 'evidence_state': 'unknown'} if replica else diagnostic
        except Exception:
            # Optional old-core/malformed evidence must not break hydration.
            pass
        finally:
            if not replica:
                try:
                    refs = tuple(sorted({card_id, *_diagnostic_refs(out)}))
                    out['actionability_inputs'] = self._diagnostic_signature(c, board, card_id, refs)
                except Exception:
                    out.pop('actionability', None)
            if owned_snapshot and c.in_transaction:
                c.rollback()
        return out


def _diagnostic_refs(detail):
    """Only source-named immediate parents and administrative task owners, not a graph scan."""
    refs = list(detail.get('links', {}).get('parents', ()))
    diagnostic = detail.get('actionability')
    if isinstance(diagnostic, dict):
        owners = [diagnostic.get('owner')]
        dependencies = diagnostic.get('dependencies')
        owners += [dep.get('administrative_owner') for dep in dependencies if isinstance(dep, dict)] if isinstance(dependencies, (list, tuple)) else []
        for owner in owners:
            if isinstance(owner, dict) and owner.get('kind') in ('task', 'unknown', 'unavailable'):
                named = owner.get('refs')
                if isinstance(named, (list, tuple)):
                    refs.extend(named)
    return {ref for ref in refs if isinstance(ref, str) and ref}


def _newest_events(c: sqlite3.Connection, card_id: str) -> list:
    """The card's newest `EVENTS_KEPT` non-heartbeat events, oldest first (`list_events` order). [daveey]

    SQLite picks them through `idx_events_task(task_id, created_at)`. Reading every event and filtering in Python cost
    the hub backend ~75 thread-seconds a minute (2026-11-22): cards carry up to 9,302 events, most of them
    heartbeats, and each row paid a GIL round trip in a ~190-thread process."""
    from hermes_cli import kanban_db
    rows = c.execute(
        "SELECT * FROM task_events WHERE task_id = ? AND kind != 'heartbeat' "
        "ORDER BY created_at DESC, id DESC LIMIT ?", (card_id, EVENTS_KEPT)).fetchall()
    return [kanban_db.Event.from_row(r) for r in reversed(rows)]


def _project_remote_listed(cards, ids, statuses):
    """Existing per-lineage remote selection, shared with the unactivated snapshot adapter."""
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_session_activity_release import PENDING_RELEASE_STATUSES
    from hermes_cli.kanban_session_activity_card_details import annotate_remote_upstream
    wanted, seen, remote = set(ids), set(), []
    for card in cards:
        if (card['session'] in wanted or card.get('origin_session') in wanted) and (
                card.get('status') in statuses or (card.get('status') in PENDING_RELEASE_STATUSES
                                                  and card.get('unfinished'))) and card['key'] not in seen:
            seen.add(card['key'])
            wait = ksa._remote_wait(card)
            row = {**copy.deepcopy(card), 'session_id': card['session']}
            if wait is not None:
                row['wait'] = wait
            remote.append(row)
    annotate_remote_upstream(remote)
    return remote


def _calm(rows):
    """Card rows without `last_heartbeat_at`: a heartbeat moves it every minute, the panel never reads it, and it
    would re-push the chat's whole bundle each time."""
    return [{k: v for k, v in r.items() if k != 'last_heartbeat_at'} if isinstance(r, dict) else r for r in rows or []]


def json_safe(v):
    """`v` as plain JSON: non-finite floats become null, unknown types their str(), keys strings."""
    if isinstance(v, float):
        return v if v == v and v not in (float('inf'), float('-inf')) else None
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [json_safe(x) for x in v]
    return str(v)


PUSH_MAX_BYTES = 512 * 1024  # a bigger batch is announced by name only: clients fetch it with POST /changes


def push_payload(payload: dict, max_bytes: int = PUSH_MAX_BYTES) -> dict:
    """The frame body for one batch: JSON-safe (a client's JSON.parse would drop a frame with NaN), and within
    `max_bytes`, else `bundles` is replaced by `sessions` (names only)."""
    body = json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, **payload})
    if len(json.dumps(body)) > max_bytes:
        body['sessions'] = sorted(body.pop('bundles', {}) or {})
        body['bundles'] = {}
    return body


def _emit(payload: dict) -> None:
    from hermes_cli.plugin_events import broadcast_plugin_event
    broadcast_plugin_event(PLUGIN_ID, 'changed', push_payload(payload))


def _emit_with_receipt(payload: dict) -> tuple[bytes, int, int]:
    """Exact post-transform callback custody, labelled server emission only.

    This is not an authenticated client receipt or proof of Desktop presentation.
    The generic callback return, without these exact bytes/clocks, cannot settle
    an acquisition. Invalidation-only emission still requires scoped catch-up.
    """
    from hermes_cli.plugin_events import broadcast_plugin_event
    body = push_payload(payload)
    encoded = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    started = time.monotonic_ns()
    broadcast_plugin_event(PLUGIN_ID, 'changed', body)
    return encoded, started, time.monotonic_ns()


setattr(_emit, 'with_receipt', _emit_with_receipt)


def _launch_profile() -> str:
    """The profile this process serves when no request scopes it: read on a fresh thread (no request context)."""
    out = ['default']

    def read():
        try:
            from hermes_cli.profiles import get_active_profile_name
            out[0] = get_active_profile_name() or 'default'
        except Exception:
            pass
    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(5)
    return out[0]


def _request_profile() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        return get_active_profile_name() or 'default'
    except Exception:
        return 'default'


_GLOBAL_FLAGS_WITH_VALUE = {'-p', '--profile'}  # hermes flags before the subcommand that take the next argument


def _feed_role(argv: Optional[list[str]] = None) -> str:
    """Which backend of this host this process is, stable across its restarts: the hermes subcommand (`serve`,
    `dashboard`, ...) and, for a Desktop's ssh-launched backend, that Desktop's slot (the directory of its session
    token file; the token itself changes per launch). Two Desktops on one hub are two roles."""
    args = list(sys.argv[1:] if argv is None else argv)
    sub, i = 'proc', 0
    while i < len(args):
        a = args[i]
        if a in _GLOBAL_FLAGS_WITH_VALUE:
            i += 2
            continue
        if a and not a.startswith('-'):
            sub = a
            break
        i += 1
    slot = ''
    for i, a in enumerate(args):
        if a == '--ssh-session-token-file' and i + 1 < len(args):
            slot = Path(args[i + 1]).parent.name
        elif a.startswith('--ssh-session-token-file='):
            slot = Path(a.split('=', 1)[1]).parent.name
    return re.sub(r'[^A-Za-z0-9_.-]', '_', f'{sub}-{slot}' if slot else sub)[:80]


FEED_ROLE = _feed_role()
# Stable across restarts (not the pid): clients match pushes by it, so a restarted backend's pushes count at once.
FEED_ID = f'{socket.gethostname()}:{FEED_ROLE}'
LAUNCH_PROFILE = _launch_profile()
SNAPSHOT_EVERY_S = 30.0        # a changed feed is saved at most this often (and on stop / exit)
RESTORE_MAX_AGE_S = 15 * 60.0  # an older snapshot starts a new revision space instead
SNAPSHOT_PRUNE_S = 7 * 86400.0  # snapshots of backends not seen for this long are deleted
WARN_EVERY_S = 600.0            # a repeated snapshot warning is logged at most this often
EXIT_SAVE_TIMEOUT_S = 5.0       # the exit save waits at most this long for a push in flight
_CLAIMS = _LOADER.__dict__.setdefault('snapshot_claims', {})  # snapshot path -> this process's claim generation
_WARNED = _LOADER.__dict__.setdefault('snapshot_warned', {})  # warning key -> when last logged


def _warn(key: str, msg: str, *args, exc_info: bool = False) -> None:
    now = time.monotonic()
    if now - _WARNED.get(key, -WARN_EVERY_S) >= WARN_EVERY_S:
        _WARNED[key] = now
        log.warning(msg, *args, exc_info=exc_info)


def timing_path() -> Path:
    """Tick duration and last publish time, readable from a shell without the Desktop session token."""
    return _snapshot_path().with_name('timing-' + _snapshot_path().name)


def _explicit_readers(f):
    """The feed's readers when they carry an explicit home context, else None (legacy ambient paths unchanged)."""
    readers = getattr(f, 'readers', None)
    return readers if getattr(readers, 'home_context', None) is not None else None


def write_timing(f, path: Optional[Path] = None) -> bool:
    """Atomically write the feed's wall-clock timing (`Feed.timing`) as JSON. Best effort: never raises."""
    try:
        explicit = _explicit_readers(f)
        path = path or (explicit.timing_path() if explicit is not None else timing_path())
        with f.lock:
            body = {'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'pid': os.getpid(), 'written_at': time.time(),
                    'rev': f.rev, 'watched': len(f.interest), **dict(getattr(f, 'timing', {}))}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(json.dumps(json_safe(body)), encoding='utf-8')
        os.replace(tmp, path)
        return True
    except Exception:
        log.debug('session-panel timing write failed', exc_info=True)
        return False


def _snapshot_path() -> Path:
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except ImportError:  # no core on the path (plugin-only test runs)
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', f'{FEED_ROLE}-{LAUNCH_PROFILE}')
    return home / 'session-panel' / f'feed-{safe}.json'


@contextmanager
def _locked(path: Path, timeout: float = 5.0):
    """An exclusive lock on `path`'s sibling .lock file, across processes (no-op where fcntl is missing)."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{path} is locked by another process')
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)  # closing releases the flock


def _read_header(path: Path) -> Optional[dict]:
    """A snapshot file is two lines: a small JSON header (who wrote it, its claim generation, which revision) and
    the JSON snapshot."""
    try:
        with path.open('r', encoding='utf-8') as fh:
            head = json.loads(fh.readline())
        return head if isinstance(head, dict) else None
    except Exception:
        return None


def _prune(directory: Path, keep: Path) -> None:
    now = time.time()
    for p in directory.glob('feed-*.json'):
        with contextlib.suppress(OSError):
            if p != keep and now - p.stat().st_mtime > SNAPSHOT_PRUNE_S:
                p.unlink()  # its .lock stays: a process still saving to it keeps its mutual exclusion
    for p in directory.glob('.feed-*.tmp'):
        with contextlib.suppress(OSError):
            if now - p.stat().st_mtime > 86400:
                p.unlink()


def save_snapshot(f, path: Optional[Path] = None, *, claim: bool = False, timeout: Optional[float] = None) -> bool:
    """Write `f`'s snapshot atomically (temp file, owner-only, then replace), under a cross-process lock.
    Ownership is a claim generation in the file, never a clock: a process claims the file when its feed starts
    (`claim`: the generation goes up by one) and from then on saves only while the file still carries its claim.
    So the process a restart replaces, still running for a while, never overwrites its successor's snapshot.
    False when the file has a newer claim, the feed was busy past `timeout`, or the write failed (logged)."""
    tmp = None
    try:
        explicit = _explicit_readers(f)
        path = path or (explicit.snapshot_path() if explicit is not None else _snapshot_path())
        snap = f.snapshot(timeout=timeout)
        if snap is None:
            return False
        snap = json_safe(snap)
        with _locked(path):
            held = _read_header(path) or {}
            # The generation also lives in the .lock file (never pruned): a deleted snapshot does not reset it, so
            # an older process cannot re-take the file from a newer one after it vanished.
            gen_file = path.with_name(path.name + '.lock')
            try:
                lock_gen = int(gen_file.read_text(encoding='utf-8').strip() or 0)
            except (OSError, ValueError):
                lock_gen = 0
            held_gen = max(int(held.get('gen') or 0), lock_gen)
            mine = _CLAIMS.get(str(path))
            if claim or mine is None:
                mine = held_gen + 1  # recorded once it is on disk
            elif held_gen > mine:  # generations only go up, under this lock: a newer process claimed the file
                _warn(f'claimed:{path}', 'session-panel feed: snapshot not saved; a newer process (claim %s, pid %s) '
                      'carries this feed on', held_gen, held.get('owner_pid'))
                return False
            head = {'version': snap['version'], 'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'epoch': snap['epoch'],
                    'rev': snap['rev'], 'saved_at': snap['saved_at'], 'gen': mine, 'owner_pid': os.getpid()}
            tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)  # chat and card content: owner only
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(json.dumps(head, separators=(',', ':')) + '\n' + json.dumps(snap, separators=(',', ':')))
            os.replace(tmp, path)
            tmp = None
            if mine > lock_gen:
                with contextlib.suppress(OSError):
                    gen_file.write_text(str(mine), encoding='utf-8')
            # A claim counts once written. A file that vanished (or never got our claim) is simply written again.
            _CLAIMS[str(path)] = max(mine, _CLAIMS.get(str(path), 0))
        if claim:
            _prune(path.parent, path)
        return True
    except Exception:
        _warn(f'save:{path}', 'session-panel feed: snapshot not saved', exc_info=True)
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
        return False


def load_snapshot(path: Optional[Path] = None, *, now: Optional[float] = None, readers=None) -> Optional[dict]:
    """This backend's last snapshot when it can be carried on: same feed id, profile and epoch in header and body,
    at most RESTORE_MAX_AGE_S old. None otherwise (missing, stale, unreadable, another backend's).
    Explicit-home `readers` select their own home+generation namespace; omitted keeps the ambient path."""
    try:
        explicit = readers if getattr(readers, 'home_context', None) is not None else None
        path = path or (explicit.snapshot_path() if explicit is not None else _snapshot_path())
        head_line, _, body = path.read_text(encoding='utf-8').partition('\n')
        head, snap = json.loads(head_line), json.loads(body)
    except FileNotFoundError:
        return None
    except Exception:
        _warn('load', 'session-panel feed: snapshot unreadable; starting a new revision space', exc_info=True)
        return None
    now = time.time() if now is None else now
    if not (isinstance(head, dict) and isinstance(snap, dict)) or head.get('feed') != FEED_ID \
            or head.get('profile') != LAUNCH_PROFILE or head.get('epoch') != snap.get('epoch'):
        return None
    try:
        if now - float(head.get('saved_at')) > RESTORE_MAX_AGE_S:
            return None
    except (TypeError, ValueError):
        return None
    return snap

def feed():
    """The process's one feed, started on first use (its thread ticks every TICK_S until stopped)."""
    if _external_projection() is not None:
        raise RuntimeError('external projection excludes legacy feed startup')
    while True:
        core = _load_feed()
        with core.PROCESS_LOCK:
            if sys.modules.get(_FEED_KEY) is not core:
                continue  # a reload swapped the module (under this same lock) after we loaded it: use the new one
            # Startup/reload callers cannot replace a predecessor still owned by
            # a cutover. Failed joins retain this fence until a later join succeeds.
            if (getattr(_LOADER, 'facts_transition_pending', False)
                    or getattr(_LOADER, 'facts_unloading', False)):
                return core.PROCESS_FEED
            if core.PROCESS_FEED is None or core.PROCESS_FEED.stopped.is_set():
                readers = None if getattr(_LOADER, 'facts_mode', 'legacy') == 'facts' else CoreReaders()
                prev, new = core.PROCESS_FEED, core.Feed(readers, emit=_emit)
                if prev is not None:  # a plugin reload stopped it: carry its revisions on, so clients see no reset
                    try:
                        new.resume(prev)
                    except Exception:  # never leave the process without a running feed: start clean (clients reset)
                        log.warning('session-panel feed: resume failed; starting a new revision space', exc_info=True)
                        new = core.Feed(readers, emit=_emit)
                else:  # this process's first feed: carry on the one its previous process saved
                    snap = load_snapshot()
                    if snap is not None:
                        try:
                            new.restore(snap)
                            log.info('session-panel feed: restored epoch %s at rev %s (%d bundles)',
                                     new.epoch, new.rev, len(new.bundles))
                        except Exception:
                            log.warning('session-panel feed: snapshot not restored; starting a new revision space',
                                        exc_info=True)
                            new = core.Feed(readers, emit=_emit)
                    save_snapshot(new, claim=True)  # claim it now: the process this one replaces no longer saves
                    _save_at_exit()
                if getattr(_LOADER, 'facts_mode', 'legacy') == 'facts':
                    adapter = core.PanelSubscription(new, _LOADER.facts_v1, _LOADER.facts_scope,
                                                     _LOADER.facts_v1.key, demand=_LOADER.facts_handle.demand)
                    new._fact_subscription = adapter
                    new._feed_thread = threading.Thread(target=_run_facts, args=(new, adapter), name='session-panel-facts', daemon=True)
                else:
                    new._feed_thread = threading.Thread(target=_run, args=(new,), name='session-panel-feed', daemon=True)
                readiness = getattr(_LOADER, 'facts_registration', None)
                if readiness is not None:
                    new._facts_readiness = readiness.wake
                    readiness.wake.set()
                core.PROCESS_FEED = new
                new._feed_thread.start()
            if getattr(core.PROCESS_FEED, '_facts_mode', False):
                return core.PROCESS_FEED
            # An older API's saved RESTART may already have resumed a current Feed
            # with only its board thread. Ensure this lane for existing feeds too.
            lane = getattr(core.PROCESS_FEED, '_decision_thread', None)
            if lane is None or not lane.is_alive():
                lane = threading.Thread(target=_run_decisions, args=(core.PROCESS_FEED,),
                                        name='session-panel-decisions', daemon=True)
                core.PROCESS_FEED._decision_thread = lane
                lane.start()
            lane = getattr(core.PROCESS_FEED, '_board_thread', None)
            if lane is None or not lane.is_alive():
                lane = threading.Thread(target=_run_board, args=(core.PROCESS_FEED, core.BoardLane),
                                        name='session-panel-board', daemon=True)
                core.PROCESS_FEED._board_thread = lane
                lane.start()
            # A saved older entry may already own a live capture thread. Do not
            # duplicate it or reset its cursor; repair that lane's capacity service.
            with core.PROCESS_FEED.lock:
                # Registration can still be pending in an older capture thread.
                # Publish the requirement under the same lock used by registration.
                core.PROCESS_FEED._board_service_required = True
                board = getattr(core.PROCESS_FEED, '_board_lane', None)
                if board is not None:
                    board.start_service()
            return core.PROCESS_FEED


class _FactsMount:
    """Exact installation custody for the authorized dashboard mount registry."""
    def __init__(self, installation):
        self.installation = installation

    def close(self):
        lock = _LOADER.__dict__.setdefault('facts_binding_lock', threading.Lock())
        with lock:
            if getattr(_LOADER, 'facts_mount', None) is not self:
                return  # replacement already drained this installation
            _stop_facts()  # failure retains this handle for a retry
            _LOADER.facts_mount = None


def bind_facts(ctx):
    """Published dashboard lifecycle hook; ctx is authorized by the core mount."""
    lock = _LOADER.__dict__.setdefault('facts_binding_lock', threading.Lock())
    with lock:
        installation = _install_facts(ctx.facts.v1, ctx.facts.scope, ctx.facts.handle)
        mounted = _FactsMount(installation)
        _LOADER.facts_mount = mounted
        return mounted


def install_facts(v1, scope, handle):
    # Binding custody spans close, publication and start. Do not share the mode
    # transition lock: close/report_ready may synchronously invoke that callback.
    lock = _LOADER.__dict__.setdefault('facts_binding_lock', threading.Lock())
    with lock:
        return _install_facts(v1, scope, handle)


def _install_facts(v1, scope, handle):
    """Bind the authorized, host-injected API; only its global latch may switch.

    Called at plugin registration/startup, never from a request. Old cores do not
    call this entry point and continue to use the installed legacy lanes.
    """
    core = _load_feed()
    with core.PROCESS_LOCK:
        if getattr(_LOADER, 'facts_unloading', False):
            raise RuntimeError('panel unload in progress; registration refused')
        _LOADER.facts_binding_token = None
    previous = getattr(_LOADER, 'facts_registration', None)
    if previous is not None:
        previous.close()
        _LOADER.facts_registration = None
    _LOADER.facts_mount = None
    token = object()
    with core.PROCESS_LOCK:
        if getattr(_LOADER, 'facts_unloading', False):
            raise RuntimeError('panel unload in progress; registration refused')
        _LOADER.facts_binding_token = token
        _LOADER.facts_v1, _LOADER.facts_scope = v1, scope
        _LOADER.facts_handle = handle
        _LOADER.facts_ready = None
    registration = None
    try:
        registration = v1.bind_consumer('panel', scope,
                                       lambda mode, generation: _facts_mode_changed(mode, generation, token=token))
        readiness = core.PanelReadiness(v1, scope,
                                       lambda: _warm_facts(v1, scope, handle), start=False)
    except Exception:
        with core.PROCESS_LOCK:
            _LOADER.facts_binding_token = None
        if registration is not None:
            registration.close()
        raise
    readiness.registration = registration
    with core.PROCESS_LOCK:
        accepted = (getattr(_LOADER, 'facts_binding_token', None) is token
                    and not getattr(_LOADER, 'facts_unloading', False))
        if accepted:
            _LOADER.facts_registration = readiness
    if not accepted:
        readiness.close()
        raise RuntimeError('panel registration superseded by unload or replacement')
    if core.PROCESS_FEED is not None:
        core.PROCESS_FEED._facts_readiness = readiness.wake
    try:
        readiness.start()
    except Exception:
        with core.PROCESS_LOCK:
            _LOADER.facts_binding_token = None
            _LOADER.facts_registration = None
        readiness.close()
        raise
    return readiness


def stop_facts():
    lock = _LOADER.__dict__.setdefault('facts_binding_lock', threading.Lock())
    with lock:
        return _stop_facts()


def _stop_facts():
    """Unload without releasing predecessor custody before every lane exits."""
    core = _load_feed()
    with core.PROCESS_LOCK:
        _LOADER.facts_unloading = True
        _LOADER.facts_binding_token = None
        _LOADER.facts_transition_pending = True
    # Closing a binding can call back into the lifecycle, so never hold its
    # transition lock while closing. The pending fence already blocks startup.
    previous = getattr(_LOADER, 'facts_registration', None)
    if previous is not None:
        previous.close()
        _LOADER.facts_registration = None
    _LOADER.__dict__.setdefault('facts_transition_lock', threading.Lock())
    with _LOADER.facts_transition_lock:
        f = core.PROCESS_FEED
        if f is not None and not _quiesce(f):
            raise RuntimeError('panel reader did not quiesce; unload refused')
        adapter = getattr(f, '_fact_subscription', None)
        if adapter is not None:
            adapter.close()
        with core.PROCESS_LOCK:
            _LOADER.facts_mode = 'legacy'
            _LOADER.facts_generation = -1
            for name in ('facts_v1', 'facts_scope', 'facts_handle'):
                _LOADER.__dict__.pop(name, None)
            _LOADER.facts_transition_pending = False


def _warm_facts(v1=None, scope=None, handle=None):
    """Background readiness only: no consumer claims readiness for cold panels."""
    if getattr(_LOADER, 'facts_transition_pending', False):
        return False, None
    if v1 is None:
        v1 = getattr(_LOADER, 'facts_v1', None)
        scope = getattr(_LOADER, 'facts_scope', None)
        handle = getattr(_LOADER, 'facts_handle', None)
    if v1 is None:
        return False, None
    f = getattr(sys.modules.get(_FEED_KEY), 'PROCESS_FEED', None)
    if f is None:
        return False, None  # readiness awaits the real publisher
    with f.lock:
        f._drain()
        sessions = tuple(f.interest)
    snapshot = v1.snapshot(scope)
    ready = True
    now, deadlines = f.clock(), []
    for session in sessions:
        view = snapshot.view('panel_bundle', {'session': session})
        fact = snapshot.get(v1.key(scope, 'ChatWaitingFact', session))
        if fact is not None and fact.valid_until is not None and fact.valid_until > now:
            deadlines.append(fact.valid_until)
        if (not isinstance(view, v1.Ready) or fact is None or fact.health != 'fresh'
                or (fact.valid_until is not None and fact.valid_until <= now)):
            ready = False
            handle.demand('panel_bundle', {'session': session})
    return ready, (max(0.0, min(deadlines) - now) if deadlines else None)


def _quiesce(f, timeout=25.0):
    """Join every old reader before subscribing. A stuck reader holds cutover."""
    f.stopped.set()
    end = time.monotonic() + timeout
    threads = [getattr(f, name, None) for name in
               ('_feed_thread', '_decision_thread', '_board_thread', '_watch_thread')]
    board = getattr(f, '_board_lane', None)
    if board is not None:
        with f.lock:
            threads.extend(list(board.active.values()))
            threads.append(board.service_worker)
        board.service_stop.set()
        board.service_wake.set()
    for thread in threads:
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, end - time.monotonic()))
    return not any(t is not None and t.is_alive() for t in threads)


def _facts_mode_changed(mode, generation, *, token=None):
    """Off-request latch callback; repeated generations never duplicate lanes."""
    if mode not in ('legacy', 'facts'):
        raise ValueError('unsupported facts mode')
    core = _load_feed()
    _LOADER.__dict__.setdefault('facts_transition_lock', threading.Lock())
    with _LOADER.facts_transition_lock:
        with core.PROCESS_LOCK:
            if token is not None and token is not getattr(_LOADER, 'facts_binding_token', None):
                return  # delayed callback from a closed binding
            if getattr(_LOADER, 'facts_unloading', False):
                return  # stop_facts owns the final drain; close must not restart
            if generation < getattr(_LOADER, 'facts_generation', -1):
                return
            current_mode = getattr(_LOADER, 'facts_mode', 'legacy')
            previous = core.PROCESS_FEED
            if mode == current_mode and (previous is None or not previous.stopped.is_set()):
                _LOADER.facts_generation = generation
                return
            _LOADER.facts_transition_pending = True
        # Do not hold the feed's process lock while joining its workers. Never
        # clear custody or restart on timeout: a reader may still hold a DB pass.
        if previous is not None and not _quiesce(previous):
            raise RuntimeError('panel reader did not quiesce; mode transition refused')
        if previous is not None:
            adapter = getattr(previous, '_fact_subscription', None)
            if adapter is not None:
                adapter.close()
        with core.PROCESS_LOCK:
            _LOADER.facts_mode = mode
            _LOADER.facts_generation = generation
            _LOADER.facts_transition_pending = False
        feed()


def _resume_facts_reload():
    """Reload uses the same quiescence/custody gate as a mode transition."""
    try:
        _facts_mode_changed(getattr(_LOADER, 'facts_mode', 'legacy'),
                            getattr(_LOADER, 'facts_generation', 0))
    except Exception:
        log.warning('session-panel reload held until predecessor quiesces', exc_info=True)
        _LOADER.facts_registration.wake.set()


def _run_facts(f, adapter):
    saved = None
    saved_at = 0.0
    try:
        while not f.stopped.is_set():
            try:
                adapter.step()
                with f.lock:
                    state = (f.rev, frozenset(f.interest))
                if state != saved and time.monotonic() - saved_at >= SNAPSHOT_EVERY_S:
                    saved_at = time.monotonic()
                    if save_snapshot(f):
                        saved = state
            except Exception:
                log.warning('session-panel facts publication failed', exc_info=True)
                with f.lock:
                    sessions = tuple(f.interest)
                adapter._unavailable(sessions, 'fact_subscription_failed')
                adapter._status()
                with adapter.lock:
                    adapter.reset_needed = True
                f.stopped.wait(0.2)
            # Only transport heartbeat / interest expiry; no source retry or
            # reducer scan. Failed source recovery belongs to the shared owner.
            deadlines = [at for at in adapter.deadlines.values() if at is not None]
            delay = min(f.heartbeat_s, max(0.0, min(deadlines) - f.clock())) if deadlines else f.heartbeat_s
            f._wake.wait(delay)
    finally:
        adapter.close()
        if getattr(f, '_successor', None) is None:
            save_snapshot(f)


def stop() -> None:
    """Stop the feed thread (plugin unload / disable)."""
    f = _load_feed().PROCESS_FEED
    if f is not None:
        f.stopped.set()


MIN_PAUSE_S = 0.2
MAX_PAUSE_S = 5.0


def _pause(elapsed: float, tick_s: Optional[float] = None) -> float:
    """How long a loop rests after a pass that took `elapsed` seconds. A pass inside the tick keeps the tick
    period. A slower one rests as long as it ran (at most MAX_PAUSE_S): the old fixed 0.2 s floor turned every
    overrunning pass into a near-continuous loop that took the interpreter from the request threads."""
    tick_s = TICK_S if tick_s is None else tick_s
    return max(MIN_PAUSE_S, tick_s - elapsed, min(elapsed, MAX_PAUSE_S))


DEBOUNCE_S = 0.15        # a write burst is one pass
MIN_GAP_S = 0.5          # under continuous writes, passes start at least this far apart (a waiting reader skips it)
WATCH_REFRESH_S = 20.0   # the watcher re-lists the files it watches (a new board) and notices a stop; no database


class FileWatch:
    """Kernel notification of writes to a set of files: Linux inotify on their directories, kqueue elsewhere.
    Nothing here polls: `wait` sleeps in select until the kernel reports a write (or `timeout`). `ok` is False
    where neither exists; source freshness then remains unavailable, without a DB polling fallback."""
    _IN = 0x2 | 0x40 | 0x80 | 0x100 | 0x200  # MODIFY | MOVED_FROM | MOVED_TO | CREATE | DELETE
    _Q_OVERFLOW, _IGNORED = 0x4000, 0x8000   # the kernel dropped events | a watch ended (its directory is gone)

    def __init__(self):
        self.ok, self._fd, self._kq, self._libc = False, -1, None, None
        self.stale = False  # a watch ended under us: the owner re-lists (set_paths) before it sleeps again
        self._dirs: dict[str, int] = {}      # inotify: directory -> watch descriptor
        self._names: dict[int, set] = {}     # inotify: watch descriptor -> file names wanted in it
        self._fds: dict[str, int] = {}       # kqueue: path -> descriptor
        self._wanted: list[str] = []
        try:
            if sys.platform.startswith('linux'):
                import ctypes
                self._libc = ctypes.CDLL(None, use_errno=True)
                self._fd = self._libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
                self.ok = self._fd >= 0
            else:
                import select
                if hasattr(select, 'kqueue'):
                    self._kq = select.kqueue()
                    self.ok = True
        except Exception:
            self.ok = False

    @staticmethod
    def _files(paths) -> list[str]:
        out = []
        for p in paths:
            out += [str(p), f'{p}-wal']
        return list(dict.fromkeys(out))

    def set_paths(self, paths, *, directories=()) -> None:
        """Watch source files, and entry changes in inventory directories.

        Missing parents are watched through the nearest existing ancestor. The
        next path component's creation immediately rearms coverage; no 20-second
        discovery gap is allowed for a newly created profile or source store.
        """
        files = self._files(paths)
        self._inventory_dirs = {str(p) for p in directories}
        if self._kq is not None:
            self._wanted = files
            self._arm()
            return
        want: dict[str, set] = {}
        rearm: dict[str, set] = {}
        for f in files:
            parent, name = Path(f).parent, Path(f).name
            while not parent.is_dir() and parent != parent.parent:
                name, parent = parent.name, parent.parent
                rearm.setdefault(str(parent), set()).add(name)
            want.setdefault(str(parent), set()).add(name)
        for directory in self._inventory_dirs:
            parent = Path(directory)
            if parent.is_dir():
                want.setdefault(str(parent), set()).add('*')
            else:
                name = parent.name
                parent = parent.parent
                while not parent.is_dir() and parent != parent.parent:
                    name, parent = parent.name, parent.parent
                want.setdefault(str(parent), set()).add(name)
                rearm.setdefault(str(parent), set()).add(name)
        self._rearm = rearm
        for d in [d for d in self._dirs if d not in want]:
            wd = self._dirs.pop(d)
            self._names.pop(wd, None)
            self._libc.inotify_rm_watch(self._fd, wd)
        self.stale = False
        import ctypes
        for d, names in want.items():
            # Asked every time: the kernel answers with the watch it already has for this directory, or a new one
            # when the directory was removed and made again (the old watch died with it).
            wd = self._libc.inotify_add_watch(self._fd, os.fsencode(d), self._IN)
            old = self._dirs.get(d)
            if wd < 0:
                err = ctypes.get_errno()
                if old is not None:
                    self._dirs.pop(d)
                    self._names.pop(old, None)
                if err in (errno.ENOENT, errno.ENOTDIR):
                    continue  # no such directory yet: taken up by a later set_paths
                # Refused (ENOSPC: fs.inotify.max_user_watches; EACCES...): a feed that believed it was watching
                # would never hear a write. The owner logs this and goes back to the timed loop.
                raise OSError(err, f'inotify_add_watch refused: {os.strerror(err)}', d)
            if old is not None and old != wd:
                self._names.pop(old, None)
            self._dirs[d] = wd
            self._names[wd] = names

    def _arm(self) -> None:
        import select
        flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
        dirs = list(dict.fromkeys([os.path.dirname(f) for f in self._wanted] +
                                  list(getattr(self, '_inventory_dirs', ()))))
        for directory in list(dirs):
            parent = Path(directory)
            while not parent.is_dir() and parent != parent.parent:
                parent = parent.parent
            if str(parent) not in dirs:
                dirs.append(str(parent))
        self._directory_fds = set(dirs)
        for p in [p for p in self._fds if p not in self._wanted and p not in dirs]:
            os.close(self._fds.pop(p))
        for p in self._wanted + dirs:  # a directory reports a file created or removed in it (a new -wal)
            if p in self._fds:
                continue
            try:
                fd = os.open(p, getattr(os, 'O_EVTONLY', os.O_RDONLY))
            except OSError:
                continue
            self._fds[p] = fd
            self._kq.control([select.kevent(fd, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, flags)], 0, 0)

    def wait(self, timeout: float) -> bool:
        """Sleep until a watched file is written; False after `timeout` seconds without one."""
        import select
        import struct
        until = time.monotonic() + timeout
        self.changed_paths = set()
        self.source_changed = False
        while True:
            left = max(0.0, until - time.monotonic())
            if self._kq is not None:
                events = self._kq.control(None, 8, left)
                if not events:
                    return False
                by_fd = {fd: p for p, fd in self._fds.items()}
                if any(by_fd.get(e.ident) in self._directory_fds for e in events):
                    self.stale = True
                gone = [by_fd[e.ident] for e in events if e.ident in by_fd
                        and e.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME)]
                for p in gone:
                    os.close(self._fds.pop(p))
                self.changed_paths = None  # directory/overflow ambiguity stays legacy
                self.source_changed = True
                self._arm()  # a replaced or newly created file is watched from here on
                return True
            if not select.select([self._fd], [], [], left)[0]:
                return False
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                continue
            hit, at = False, 0
            while at + 16 <= len(data):
                wd, mask, _cookie, size = struct.unpack_from('iIII', data, at)
                name = data[at + 16:at + 16 + size].split(b'\0', 1)[0]
                at += 16 + size
                if mask & self._Q_OVERFLOW:
                    self.source_changed = True
                    self.changed_paths = None
                    hit = True  # events were dropped: any of ours may be among them
                elif mask & self._IGNORED:
                    if wd in self._names:  # a watched directory went away (not our own rm_watch): its files with it
                        self._names.pop(wd)
                        self._dirs = {d: w for d, w in self._dirs.items() if w != wd}
                        self.stale = hit = self.source_changed = True
                        self.changed_paths = None
                else:
                    decoded = os.fsdecode(name)
                    names = self._names.get(wd, ())
                    entry = mask & (0x40 | 0x80 | 0x100 | 0x200)
                    inventory = '*' in names and entry
                    self.source_changed |= decoded in names
                    parent = next((d for d, w in self._dirs.items() if w == wd), None)
                    if inventory or decoded in getattr(self, '_rearm', {}).get(parent, ()):
                        self.stale = True
                    if self.changed_paths is not None and (decoded in names or inventory):
                        if parent is None:
                            self.changed_paths = None
                        else:
                            self.changed_paths.add(Path(parent) / decoded)
                    hit = hit or decoded in names or bool(inventory)
            if hit:
                return True  # anything else in those directories (logs, locks) is not ours

    def close(self) -> None:
        for fd in [self._fd, *self._fds.values()]:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if self._kq is not None:
            self._kq.close()
        self._fd, self._fds, self.ok = -1, {}, False


def _keyed_hints():
    if _mitigation('HERMES_FACTS_PANEL'):
        try:
            spec = importlib.util.spec_from_file_location('_panel_keyed_hints', Path(__file__).with_name('keyed_hints.py'))
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.KeyedHints()
        except ImportError:
            pass  # older core retains every legacy lane
    return None


def _watch(f, watch: FileWatch, paths) -> None:
    """The feed's one watcher thread: a write to a source file makes a pass due on both lanes."""
    try:
        listed = None
        hints = _keyed_hints()
        listed_paths = None

        def rearm():
            nonlocal listed_paths
            readers = f.readers
            invalidate = getattr(readers, 'invalidate_inventory', None)
            if callable(invalidate):
                invalidate()
            publications = getattr(readers, 'publication_paths', lambda: ())() if hints is not None else ()
            current = tuple(p for p in paths() if p not in publications)
            changed = listed_paths is not None and set(current) != set(listed_paths)
            listed_paths = current
            roots = getattr(readers, 'watch_directories', None)
            if callable(roots):
                watch.set_paths(current, directories=roots())
            else:
                watch.set_paths(current)
            return changed

        while not f.stopped.is_set():
            # Periodic maintenance is filesystem-only; it never admits a DB pass.
            # Inventory events rearm immediately, before waking either producer.
            if listed is None or watch.stale or time.monotonic() - listed >= WATCH_REFRESH_S:
                rearm()
                listed = time.monotonic()
            deadline = listed + WATCH_REFRESH_S
            if hints is not None:
                deadline = min(deadline, hints.next_reconcile)
                if hints.reader.source_dirty:
                    deadline = time.monotonic()
            hit = watch.wait(max(0.0, deadline - time.monotonic()))
            recovery = hints is not None and hints.due()
            if recovery:
                with f.lock:
                    f.queue_keyed(set(f.interest))
            if (hit or (hints is not None and hints.reader.source_dirty)) and not f.stopped.is_set():
                if watch.stale:
                    inventory_changed = rearm()
                    listed = time.monotonic()
                    # Directory inventory hints only rediscover sources. Our own
                    # SQLite sidecars/temp files are not application changes.
                    # Exact source writes, lost coverage and new/removed sources
                    # still own a trailing pass (never clear an existing wake).
                    if not getattr(watch, 'source_changed', True) and not inventory_changed:
                        continue
                changed = getattr(watch, 'changed_paths', None)
                hub_files = {Path(str(hints.path) + suffix) for suffix in ('', '-wal', '-shm', '-journal')} if hints else set()
                if hints is not None and ((changed and changed <= hub_files) or (not hit and hints.reader.source_dirty)):
                    try:
                        with f.lock:
                            held = {s: {'lineage': set(b['lineage']), 'refs': set(b['refs'])}
                                    for s, b in f.bundles.items() if s in f.interest}
                        f.queue_keyed(hints.sessions(held, LAUNCH_PROFILE))
                        continue
                    except Exception:
                        hints.reader.source_dirty = False  # retry via next hint/clock, never a tight loop
                        log.warning('panel keyed hint failed; legacy admission retained', exc_info=True)
                with f.lock:
                    f._legacy_wake = True
                    f._wake.set()
                f._decide.set()
                board = getattr(f, '_board', None)
                if board is not None:
                    board.set()
                f.stopped.wait(DEBOUNCE_S)  # one wake per burst; what is written meanwhile wakes the next round
    except Exception:
        log.warning('session-panel feed: file watch failed; source freshness unavailable (no polling)', exc_info=True)
        f._watching = False
    finally:
        watch.close()


def _event_driven(f) -> bool:
    """Start (once per feed) the file watch that replaces the timed loops; False when this feed, its readers or
    this platform cannot be event-driven (the timed loop stays)."""
    with _LOADER.lock:
        state = getattr(f, '_watching', None)
        if state is not None:
            return state
        paths = getattr(f.readers, 'watch_paths', None)
        watch = FileWatch() if callable(paths) and all(hasattr(f, n) for n in ('_wake', '_cold', '_decide', 'beat')) else None
        f._watching = bool(watch is not None and watch.ok)
        if f._watching:
            f._watch_thread = threading.Thread(target=_watch, args=(f, watch, paths), name='session-panel-watch', daemon=True)
            f._watch_thread.start()
        return f._watching


def _mitigation(name: str) -> bool:
    """Reversible urgent mitigation; unset means enabled."""
    return os.environ.get(name, '1').lower() not in ('0', 'false', 'off', 'no')


def _coalesce(f, last_start: float | None) -> None:
    # Called only after demand/events (or an existing retry), never on an idle timer.
    # Do not clear the wake here: arrivals during the run remain a trailing pass.
    if last_start is not None:
        f.stopped.wait(max(0.0, 3.0 - (time.monotonic() - last_start)))


# A full pass recomputes the watched chats whose inputs moved (about 2 s each on a busy host), and a write to any
# source file makes the next one due. Source files are written once or twice a second even when idle, so the feed
# thread ran back to back (41% of a core in the MacBook gateway, 2026-11-23): the 3 s floor above is measured from
# a pass's start and does nothing once a pass outlasts it. After a full pass that took T the next one now rests for
# DUTY_REST_RATIO * T, but never so long that a held bundle would pass its trust window. A bundle's age when the
# next pass replaces it is the pass that read it, the rest, and the next pass: T + rest + T. So the rest is bounded
# by full_every_s - DUTY_TRUST_MARGIN_S - 2T, and a pass of 25 s or more earns none at the default 60 s window.
# This holds for passes of similar length: the margin covers the 3 s floor grid and a next pass up to about 7 s
# longer; a much longer one can show a chat untrusted once, after which its own length earns no rest. Not held: keyed refreshes, a cold reader waiting for its first bundle, a
# pending retry (`_retry` or a deferred reconcile) while its budgeted retry clock is armed (`_recovery_due`), and
# the board and decision lanes. A chat that fails every pass spends its retries and then rests like the others:
# `timing.duty_deferrals` not rising under continuous writes shows a regression of that.
# HERMES_PANEL_DUTY=0 turns the rest off.
DUTY_REST_RATIO = 2.0
DUTY_TRUST_MARGIN_S = 10.0


def _duty_owed(f) -> float:
    """Seconds the next full pass still has to rest after the last one (0 when none is owed)."""
    if not _mitigation('HERMES_PANEL_DUTY'):
        return 0.0
    ended, took = f.__dict__.get('_full_pass', (None, 0.0))
    if ended is None:
        return 0.0
    window = float(getattr(f, 'full_every_s', 60.0))
    rest = min(DUTY_REST_RATIO * took, max(0.0, window - DUTY_TRUST_MARGIN_S - 2 * took))
    return max(0.0, rest - (time.monotonic() - ended))


_RETRY_CLOCKS = ('_admission_retry_at', '_hydrate_retry_at', '_reconcile_retry_at')


def _recovery_due(f) -> bool:
    """True while failed work still has a budgeted retry armed (`Feed._spend` / `Feed._backoff`): that retry
    skips the duty rest. A chat left in `_retry` or `_reconcile_deferred` after its retries ran out has no
    clock armed (`_spend` returns None) and is re-admitted by the next source write like any other chat, so it
    no longer skips the rest. Before, the non-empty set alone did: on the hub four chats whose remote
    reconciliation defers on every pass kept the rest off for the whole process (`duty_deferrals` stayed at 6
    over 100 back-to-back passes of 6-21 s, 2026-11-24)."""
    if not (getattr(f, '_retry', None) or getattr(f, '_reconcile_deferred', None)):
        return False
    return any(getattr(f, name, None) is not None for name in _RETRY_CLOCKS)


def _settle(f, last_start: float) -> None:
    """Before a pass: coalesce demand, without allowing cold readers to bypass the floor."""
    if _mitigation('HERMES_PANEL_COALESCE'):
        _coalesce(f, last_start or None)
    else:
        f._cold.wait(max(DEBOUNCE_S, MIN_GAP_S - (time.monotonic() - last_start)))


def _run_decisions(f) -> None:
    """A blocked board sweep must not delay a recorded decision's status line."""
    # Own current-generation readers: the existing Feed may have been constructed
    # through an older API whose readers do not implement the cheap decision path.
    readers = CoreReaders()
    try:
        first = True
        last = None
        while not f.stopped.is_set():
            started = time.monotonic()
            _event_driven(f)
            if not first:
                # Startup, explicit registration and source events only. A
                # refused watcher must never turn this lane into a poller.
                # A failed read's bounded retry (Feed._backoff) is the one timed wake.
                retry_at = getattr(f, '_decision_retry_at', None)
                if retry_at is None:
                    f._decide.wait()
                else:
                    f._decide.wait(max(0.0, retry_at - f.clock()))
                    f._decision_retry_at = None  # consumed: the pass re-arms it while a read still fails
                if _mitigation('HERMES_PANEL_COALESCE'):
                    _coalesce(f, last)
                else:
                    f.stopped.wait(max(DEBOUNCE_S, MIN_GAP_S - (time.monotonic() - started)))
            if f.stopped.is_set():
                break
            f._decide.clear()
            first = False
            started = last = time.monotonic()
            try:
                f.refresh_decisions(readers=readers)
            except Exception:
                log.warning('session-panel decision refresh failed', exc_info=True)
            readers.release()  # no connection outlives one pass
    finally:
        readers.release()


def _run_board(f, lane_type) -> None:
    """Source admission stays responsive while reconciliation or another chat blocks."""
    lane = lane_type(f, CoreReaders)
    lane.start_service()
    first = True
    last = None
    try:
        while not f.stopped.is_set():
            if not first:
                at = lane.retry_at()
                f._board.wait(None if at is None else max(0, at - f.clock()))
                if _mitigation('HERMES_PANEL_COALESCE'):
                    _coalesce(f, last)
                else:
                    f.stopped.wait(DEBOUNCE_S)
            if f.stopped.is_set():
                break
            first = False
            last = time.monotonic()
            lane.step()
            write_timing(f)
    finally:
        lane.close()


def _run(f) -> None:
    saved = (None, None)  # (rev, watched chats) last written
    saved_at = 0.0
    try:
        last = 0.0
        _event_driven(f)
        f._cold.set()  # one startup pass, including when notifications are unavailable
        f._wake.set()
        while not f.stopped.is_set():
            # A timeout admits memory-only ageing, never database reads.
            idle = getattr(f, 'idle_wait', None)
            if not f._wake.wait(idle() if callable(idle) else getattr(f, 'heartbeat_s', 20.0)):
                try:
                    f.beat()
                except Exception:
                    log.warning('session-panel feed heartbeat failed', exc_info=True)
                continue
            if f.stopped.is_set():
                break
            _settle(f, last)
            if f.stopped.is_set():
                break
            started = last = time.monotonic()
            try:
                # Maintenance must not depend on an idle timeout: a steady stream
                # of keyed hints must still renew interest and publish heartbeats.
                f.beat()
                with f.lock:
                    pending = f.__dict__.setdefault('_keyed_pending', set())
                    keyed = set(sorted(pending)[:4])
                    pending.difference_update(keyed)
                    keyed_only = bool(keyed) and not (
                        getattr(f, '_legacy_wake', False) or f._cold.is_set() or f._retry)
                    f._legacy_wake = False
                    if keyed_only:
                        f._wake.clear()  # arrivals after this boundary retain a trailing wake
                if keyed:
                    f.refresh_keyed(keyed)
                if not keyed_only:
                    # getattr: the pacing tests' feed doubles carry only what their case needs.
                    urgent = getattr(f._cold, 'is_set', lambda: True)() or _recovery_due(f)
                    if not urgent and _duty_owed(f) > 0:
                        # The full pass stays due: the wake is still set (so the loop returns after the floor)
                        # and a keyed batch arriving meanwhile must not clear it as a keyed-only iteration.
                        with f.lock:
                            f._legacy_wake = True
                        timing = f.__dict__.setdefault('timing', {})
                        timing['duty_deferrals'] = timing.get('duty_deferrals', 0) + 1
                    else:
                        tick_started = time.monotonic()
                        f.__dict__['_full_pass'] = (tick_started, 0.0)  # a pass that raises earns no rest
                        f.tick()
                        ended = time.monotonic()
                        f.__dict__['_full_pass'] = (ended, ended - tick_started)
            except Exception:
                log.warning('session-panel feed tick failed', exc_info=True)
            with f.lock:
                if f.__dict__.get('_keyed_pending'):
                    f._wake.set()  # bounded batch yields; the remainder owns a trailing pass
            f.readers.release()  # no connection outlives one pass
            readiness = getattr(f, '_facts_readiness', None)
            if readiness is not None:
                readiness.set()
            write_timing(f)  # after every loop iteration, a deferred pass included (at most one per MIN_GAP_S): the 1 s freshness target is read from it
            with f.lock:
                state = (f.rev, frozenset(f.interest))
            if state != saved and time.monotonic() - saved_at >= SNAPSHOT_EVERY_S:
                saved_at = time.monotonic()
                if save_snapshot(f):
                    saved = state
    finally:
        # A plugin reload's successor owns the revision space now (and saves it); a plain stop saves the last state.
        if getattr(f, '_successor', None) is None:
            save_snapshot(f)
        f.readers.release()  # retained connections belong to this thread, including plugin unload


def _save_at_exit() -> None:
    """Once per process: at interpreter exit (a restart for an update) save the running feed's last state."""
    if _LOADER.__dict__.setdefault('atexit_registered', False):
        return
    _LOADER.atexit_registered = True
    import atexit
    atexit.register(_save_running)
    _LOADER.atexit_hook = _save_running  # what was registered (tests check it)


def _save_running() -> bool:
    """The exit hook: save the running feed's last state (never hanging the exit on a stuck push)."""
    f = getattr(sys.modules.get(_FEED_KEY), 'PROCESS_FEED', None)
    return f is not None and not f.stopped.is_set() and save_snapshot(f, timeout=EXIT_SAVE_TIMEOUT_S)


def ensure_snapshot_service(feed, api_generation, reader_factory):
    """Register current readers for an already explicitly opted-in owner only."""
    ensure = getattr(feed, 'ensure_snapshot_service', None)
    # Registration can precede feed.py's natural reload. The published factory
    # is consumed by its successor's resume; an older class cannot install it.
    return ensure(api_generation, reader_factory) if callable(ensure) else None


def register_snapshot_readers(reader_factory):
    """Publish capability before/after a genuine saved RESTART, without traffic.

    Publish under the loader lock, then acquire the process lock in the same
    order as the loader. Never run reader construction under either lock. A
    resumed feed also consumes this registry itself, closing the other ordering.
    """
    with _LOADER.lock:
        setattr(_LOADER, 'snapshot_reader_factory', reader_factory)
        core = sys.modules.get(_FEED_KEY)
        if core is not None:
            setattr(core, 'SNAPSHOT_READER_FACTORY', reader_factory)
        generation = _LOADER.generation
    if core is None:
        return None
    with core.PROCESS_LOCK:
        current = core.PROCESS_FEED
    if current is not None:
        return ensure_snapshot_service(current, generation, reader_factory)
    return None


# Legacy reload callbacks are excluded from the producer-only import.
_PRODUCER_ONLY = __name__ == '_session_panel_producer_api_v1'
if not _PRODUCER_ONLY and _external_projection() is None:
    # Publish current readers before a saved predecessor entry can resume a
    # current Feed. Its legacy factory is not the registration authority.
    register_snapshot_readers(CoreReaders)
    _load_feed().RESTART = feed
    _load_feed().INSTALL_FACTS = install_facts
    _load_feed().STOP_FACTS = stop_facts
    _binding = sys.modules.get('_session_panel_facts_binding')
    if _binding is not None:
        install_facts(_binding.v1, _binding.scope, _binding.handle)


class ChangesBody(BaseModel):
    subscriber: Optional[str] = Field(default=None, max_length=128)
    visible_session: Optional[str] = Field(default=None, max_length=256)
    sessions: list[str] = Field(default_factory=list, max_length=200)
    since: int = 0
    epoch: Optional[str] = None


router = APIRouter()
_WIRE_PREFIX = json.dumps({'feed': FEED_ID, 'profile': LAUNCH_PROFILE},
                          ensure_ascii=False, separators=(',', ':')).encode()[:-1] + b','


def start_publication() -> bool:
    """Start (or resume) the process's feed on its own thread; True when a start was launched. Disk restore and the
    feed lifecycle locks never run on an interactive request. One start in flight per process."""
    if _external_projection() is not None:
        return False
    running = _LOADER.__dict__.get('starting')
    if running is not None and running.is_alive():
        return False
    t = threading.Thread(target=feed, name='session-panel-start', daemon=True)
    _LOADER.starting = t
    t.start()
    return True


def _serves_desktop(name: str = __name__, role: str = FEED_ROLE) -> bool:
    """True when core's web server imported this file as the plugin's API in a backend that serves Desktops
    (`hermes serve` / `hermes dashboard`). Core's app runs a lifespan, so a router startup hook never fires, and
    plugin routes may be mounted long after startup: the import is the one moment every such process passes."""
    return name.startswith('hermes_dashboard_plugin_') and role.split('-', 1)[0] in ('serve', 'dashboard')


if _serves_desktop():
    # The producer runs before the first click: a restarted backend restores its saved bundles and recomputes them
    # while Desktops reconnect, instead of starting on (and making wait) the first request.
    start_publication()


@router.post('/changes')
async def changes(body: ChangesBody, request: Request):
    profile = _request_profile()
    if profile != LAUNCH_PROFILE:
        return {'feed': FEED_ID, 'profile': profile, 'profile_unsupported': True}
    external = _external_projection()
    if external is not None:
        from hermes_cli.serve_producer_runtime import projection_response
        command = body.model_dump()
        command['_wire_identity'] = {'feed': FEED_ID, 'profile': LAUNCH_PROFILE}
        return projection_response(external, command)
    f = getattr(sys.modules.get(_FEED_KEY), 'PROCESS_FEED', None)
    if f is None or not hasattr(f, 'changes_bytes'):
        # Not running in this process yet (a host process that did not start it at import): start it off this
        # request and say so. Never compute here. A stopped feed (plugin unload) keeps answering with what it
        # holds; register() resumes it.
        start_publication()
        return Response(b'{"detail":"Session panel starting"}', status_code=503,
                        media_type='application/json', headers={'Retry-After': '1'})
    current = f._current() if getattr(f, '_successor', None) is not None else f
    if getattr(current, 'receipt_log', None) is not None:
        # Private inclusion telemetry uses core's actual route-local admission,
        # not a client body flag/header assertion. No token/principal is logged.
        from hermes_cli.plugin_host_http_auth import admitted_for_sensitive_route
        from fastapi import HTTPException
        from uuid import uuid4
        request_id = uuid4().hex
        if not admitted_for_sensitive_route(request):
            current.receipt_log.record('catchup_error', request_id=request_id, authenticated=False,
                                       error='core_http_admission_refused')
            raise HTTPException(401, 'Unauthorized')
        wire, custody = current.changes_packet(body.sessions, since=body.since, epoch=body.epoch,
                            subscriber=body.subscriber, visible_session=body.visible_session)
        return current.receipt_module.completed_response(_WIRE_PREFIX + wire[1:], log=current.receipt_log,
            stored=custody, authenticated=True, request_id=request_id)
    wire = f.changes_bytes(body.sessions, since=body.since, epoch=body.epoch,
                           subscriber=body.subscriber, visible_session=body.visible_session)
    return Response(_WIRE_PREFIX + wire[1:], media_type='application/json')


@router.get('/status')
def status():
    f = getattr(sys.modules.get(_FEED_KEY), 'PROCESS_FEED', None)
    if f is None:
        return {'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'running': False}
    if getattr(f, '_facts_mode', False):
        stopped = b'true' if f.stopped.is_set() else b'false'
        running = b'false' if f.stopped.is_set() else b'true'
        wire = f._fact_subscription.status_wire
        return Response(_WIRE_PREFIX + b'"running":' + running + b',' + wire[1:-1]
                        + b',"stopped":' + stopped + b'}', media_type='application/json')
    return json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'running': not f.stopped.is_set(), **f.status()})


class DecisionRefreshBody(BaseModel):
    session: str = Field(min_length=1, max_length=512)


@router.post('/decision-refresh')
async def decision_refresh(body: DecisionRefreshBody):
    # Server reads canonical state; the caller cannot submit a model or receipt
    # as presentation authority. Never join the background sweep or feed lane.
    from fastapi import HTTPException
    if _request_profile() != LAUNCH_PROFILE:
        raise HTTPException(409, 'Panel profile unavailable')
    f = getattr(sys.modules.get(_FEED_KEY), 'PROCESS_FEED', None)
    if f is None or f.stopped.is_set():
        raise HTTPException(503, 'Session panel starting')
    if getattr(f, '_facts_mode', False):
        held = f._fact_subscription.decision_wires.get(body.session)
        if held is None or (held[1] is not None and held[1] <= f.clock()):
            raise HTTPException(503, 'Session panel decision pending', headers={'Retry-After': '1'})
        return Response(_WIRE_PREFIX + held[0][1:], media_type='application/json')
    def read():
        readers = CoreReaders()
        try:
            result = f.refresh_decision(body.session, readers=readers)
            b = result['bundle']
            # Decision repaint must not enqueue megabytes of historical raw card
            # details on the same Desktop tunnel as the next interactive click.
            result['bundle'] = f.decision_projection(b)
            return json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, **result})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            readers.release()
    return await CoreReaders._user_step_api()._on_decision(read)
