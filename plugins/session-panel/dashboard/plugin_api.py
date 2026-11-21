"""Session-panel feed API, mounted at /api/plugins/session-panel (see feed.py and ../README.md).

POST /changes {sessions, since, epoch} -> {feed, profile, epoch, rev, reset, bundles}: watch these chats, catch up.
Pushes: `plugin.session-panel.changed` {feed, profile, epoch, prev, rev, bundles | heartbeat} on the gateway stream.
GET /status: the feed's revision, watched chats, failures and last recompute time.

The feed thread starts on the first request, so only the process that serves the Desktop (`hermes serve`, where
plugin routers run and `broadcast_plugin_event` reaches the windows) ever runs it. It serves the process's own
(launch) profile only: a request scoped to another profile is answered `profile_unsupported`, and the Desktop keeps
its own reads for that profile.
"""
from __future__ import annotations

import contextlib
import dataclasses
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
EVENTS_KEPT = 40   # newest events per card record (heartbeats left out): the park reader needs the newest park and what followed
log = logging.getLogger(PLUGIN_ID)


_FEED_KEY = '_session_panel_feed_core'
_FEED_PATH = Path(__file__).resolve().parent / 'feed.py'
# Process-wide (not per plugin_api generation: a hot reload re-imports this file while the old generation's threads
# may still call their own _load_feed): one lock and a generation counter for loading feed.py.
_LOADER = sys.modules.setdefault('_session_panel_feed_loader', types.ModuleType('_session_panel_feed_loader'))
_LOADER.__dict__.setdefault('lock', threading.Lock())
_LOADER.__dict__.setdefault('generation', 0)


def _feed_stamp() -> tuple:
    try:
        st = os.stat(_FEED_PATH)
        return (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return ()


def _load_feed():
    """feed.py as a module of this process: it holds the process's one feed (PROCESS_FEED), so it outlives plugin
    reloads (plugin_api.py is re-imported; this module is not). When feed.py's bytes change on disk the new code is
    loaded and the running feed handed over: it is stopped and resumed under the new code at once (same epoch and
    revisions). [daveey 2026-11-21] a hot mods-reload re-imported plugin_api.py but kept the old feed.py, so a feed
    fix did not take effect until the backend process restarted."""
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
        digest = hashlib.sha256(source).hexdigest()
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
                running.stopped.set()
                restart = True
    if restart:  # resume it now, not at the next request: pushes and heartbeats continue
        threading.Thread(target=feed, name='session-panel-feed-resume', daemon=True).start()
    return mod


class CoreReaders:
    """Feed reads against this gateway's own boards, board replicas and state.db, read-only (query_only). Each thread has its own
    connections (the feed thread and catch-up request threads read concurrently); a connection is reopened when its
    file was replaced (another inode) and dropped when the file is gone."""

    def __init__(self, state_db: Optional[Path] = None):
        from hermes_cli import kanban_sync_client as ksc
        self.ksc = ksc
        self._local = threading.local()
        self._boards: list[tuple[str, Path]] = []
        self._boards_at = 0.0
        self._boards_lock = threading.Lock()
        if state_db is None:
            from hermes_constants import get_hermes_home
            state_db = Path(get_hermes_home()) / 'state.db'
        self._state_db = Path(state_db)

    # files
    def boards(self) -> list[tuple[str, Path]]:
        with self._boards_lock:
            if time.monotonic() - self._boards_at > 60 or not self._boards:
                self._boards = list(self.ksc.board_paths())
                self._boards_at = time.monotonic()
            return list(self._boards)

    def files(self) -> list[Path]:
        out = [p for _, p in self.boards()] + [self.ksc.shadow_path(s) for s, _ in self.boards()]
        return [Path(p) for p in out if Path(p).exists()]

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
        return tuple(data_stamp(p) or stat(p) for p in self.files() + [self.state_db()])

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
                    if any(row.get('_execution_error') for row in rows):
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
            wanted, seen, remote = set(ids), set(), []
            # Same projection as append_remote_listed_cards, applied before key dedup for EACH lineage.
            for card in self._remote_inventory(scope, ids, statuses):
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
            return add_cards(out + remote)
        except Exception:
            # Preserve stable per-lineage busy memos and per-session failure isolation on batch read failure.
            scope['local_failed'] = True
            log.debug('session-panel shared read unavailable; retrying lineage', exc_info=True)
            return ksa.session_tasks(ids, statuses, stale_ok=True)

    def _remote_inventory(self, scope, ids, statuses):
        """Share replica hydration, not ids-dependent origin-obligation dedup or failure decisions."""
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
        """Cheap per-session outcome/request invalidation; no board hydration."""
        c = self.conn(self.state_db())
        try:
            row = c.execute('SELECT outcome_json FROM sessions WHERE id=?', (session,)).fetchone() if c else None
            outcome = row['outcome_json'] if row else None
        except sqlite3.Error:
            outcome = None
        c = self.conn(self.state_db().parent / 'user-steps' / 'actions.sqlite3')
        try:
            requests = tuple(tuple(r) for r in c.execute('SELECT id,revision,state,resolution FROM requests WHERE session=? ORDER BY id', (session,))) if c else ()
        except sqlite3.Error:
            requests = None
        return (outcome, requests)

    def panel_inputs(self, session, lineage):
        path = Path(__file__).resolve().parents[1] / 'inputs.py'
        spec = importlib.util.spec_from_file_location('_session_panel_inputs', path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.read(self, session, lineage)

    def linked(self, ids):
        from hermes_cli import kanban_session_activity as ksa
        return _calm(self._session_tasks(list(ids)[:50], tuple(ksa.LISTED_STATUSES)))

    def done(self, ids):
        from hermes_cli import kanban_session_activity as ksa
        ids = list(ids)[:50]
        tasks = _calm(self._session_tasks(ids, tuple(ksa.LISTED_STATUSES) + ('done', 'archived')))
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
            for path, replica in ((paths.get(slug), False), (self.ksc.shadow_path(slug), True)):
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

    def _detail(self, c: sqlite3.Connection, card_id: str, board: str, replica: bool):
        from hermes_cli import kanban_db
        task = kanban_db.get_task(c, card_id)
        if task is None:
            return None
        d = dataclasses.asdict(task)
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
        events = [dataclasses.asdict(e) for e in kanban_db.list_events(c, card_id) if e.kind != 'heartbeat'][-EVENTS_KEPT:]
        runs = [dataclasses.asdict(r) for r in kanban_db.list_runs(c, card_id)][-1:]
        for r in runs:
            r.pop('last_heartbeat_at', None)
        out = {'task': d, 'events': events, 'runs': runs, 'links': {'parents': parents, 'children': []},
               'link_tasks': link_tasks, 'source': 'replica' if replica else 'live', 'slim': True}
        if replica:
            try:
                from hermes_cli import kanban_foreign_card as kfc
                owner = kfc.created_on(c, card_id)
                out['owner'] = None if owner is kfc._UNSET else owner
            except Exception:
                out['owner'] = None
        else:
            try:
                from hermes_cli import kanban_queue_position as kqp
                extra = kqp.detail_fields(c, task, board)
                # Stamped with the read time (observed_at, age): the chain reads none of it, and it would make every
                # recompute differ. The card rows in `tasks` carry the evidence the panel judges.
                extra.pop('execution_observations', None)
                out.update(extra)
            except Exception:
                pass
        return out


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
        path = path or _snapshot_path()
        snap = f.snapshot(timeout=timeout)
        if snap is None:
            return False
        snap = json_safe(snap)
        with _locked(path):
            held = _read_header(path) or {}
            held_gen = int(held.get('gen') or 0)
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


def load_snapshot(path: Optional[Path] = None, *, now: Optional[float] = None) -> Optional[dict]:
    """This backend's last snapshot when it can be carried on: same feed id, profile and epoch in header and body,
    at most RESTORE_MAX_AGE_S old. None otherwise (missing, stale, unreadable, another backend's)."""
    try:
        path = path or _snapshot_path()
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
    while True:
        core = _load_feed()
        with core.PROCESS_LOCK:
            if sys.modules.get(_FEED_KEY) is not core:
                continue  # a reload swapped the module (under this same lock) after we loaded it: use the new one
            if core.PROCESS_FEED is None or core.PROCESS_FEED.stopped.is_set():
                prev, new = core.PROCESS_FEED, core.Feed(CoreReaders(), emit=_emit)
                if prev is not None:  # a plugin reload stopped it: carry its revisions on, so clients see no reset
                    try:
                        new.resume(prev)
                    except Exception:  # never leave the process without a running feed: start clean (clients reset)
                        log.warning('session-panel feed: resume failed; starting a new revision space', exc_info=True)
                        new = core.Feed(CoreReaders(), emit=_emit)
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
                            new = core.Feed(CoreReaders(), emit=_emit)
                    save_snapshot(new, claim=True)  # claim it now: the process this one replaces no longer saves
                    _save_at_exit()
                core.PROCESS_FEED = new
                threading.Thread(target=_run, args=(core.PROCESS_FEED,), name='session-panel-feed', daemon=True).start()
            return core.PROCESS_FEED


_load_feed().RESTART = feed  # plugin register() after a reload resumes the feed now, not at the next request


def stop() -> None:
    """Stop the feed thread (plugin unload / disable)."""
    f = _load_feed().PROCESS_FEED
    if f is not None:
        f.stopped.set()


def _run(f) -> None:
    saved = (None, None)  # (rev, watched chats) last written
    saved_at = 0.0
    try:
        while not f.stopped.is_set():
            started = time.monotonic()
            try:
                f.tick()
            except Exception:
                log.warning('session-panel feed tick failed', exc_info=True)
            with f.lock:
                state = (f.rev, frozenset(f.interest))
            if state != saved and time.monotonic() - saved_at >= SNAPSHOT_EVERY_S:
                saved_at = time.monotonic()
                if save_snapshot(f):
                    saved = state
            f.stopped.wait(max(0.2, TICK_S - (time.monotonic() - started)))
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


class ChangesBody(BaseModel):
    sessions: list[str] = Field(default_factory=list, max_length=200)
    since: int = 0
    epoch: Optional[str] = None


router = APIRouter()


@router.post('/changes')
async def changes(body: ChangesBody, request: Request):
    profile = _request_profile()
    if profile != LAUNCH_PROFILE:
        return {'feed': FEED_ID, 'profile': profile, 'profile_unsupported': True}
    # A resume waits for the stopped feed's in-flight push: never on the loop, and on the feed lane like the read.
    f = await _on_lane('feed', feed, request=request)
    out = await _on_lane('feed', f.changes, body.sessions, since=body.since, epoch=body.epoch, request=request)
    return json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, **out})


@router.get('/status')
def status():
    f = _load_feed().PROCESS_FEED
    if f is None:
        return {'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'running': False}
    return json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'running': not f.stopped.is_set(), **f.status()})
