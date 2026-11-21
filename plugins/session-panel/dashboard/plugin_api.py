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

import dataclasses
import importlib.util
import json
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

PLUGIN_ID = 'session-panel'
TICK_S = float(os.environ.get('HERMES_SESSION_PANEL_FEED_TICK_S', '1'))
EVENTS_KEPT = 40   # newest events per card record (heartbeats left out): the park reader needs the newest park and what followed
log = logging.getLogger(PLUGIN_ID)


def _load_feed():
    """feed.py, loaded once per process: it holds the process's one feed (PROCESS_FEED). hermes-sync restarts the
    dashboard for any change under dashboard/, so a new feed.py takes effect with a fresh process."""
    key = '_session_panel_feed_core'
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parent / 'feed.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


class CoreReaders:
    """Feed reads against this gateway's own boards, board replicas and state.db, read-only. Each thread has its own
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
            c = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)
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
                rows = c.execute('SELECT e.id, e.task_id, t.session_id FROM task_events e LEFT JOIN tasks t '
                                 'ON t.id = e.task_id WHERE e.id > ? ORDER BY e.id LIMIT 20000', (cursor[key],)).fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                changed[str(r['task_id'])] = r['session_id'] or changed.get(str(r['task_id']))
            if rows:
                cursor[key] = rows[-1]['id']
        return changed, cursor

    def lineage(self, session: str) -> list[str]:
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

    def linked(self, ids):
        from hermes_cli import kanban_session_activity as ksa
        return ksa.session_tasks(list(ids)[:50], stale_ok=True)

    def done(self, ids):
        from hermes_cli import kanban_session_activity as ksa
        ids = list(ids)[:50]
        tasks = ksa.session_tasks(ids, tuple(ksa.LISTED_STATUSES) + ('done', 'archived'), stale_ok=True)
        reader = getattr(ksa, 'remote_links_and_origin_rows', None)
        if reader is None:
            return tasks
        try:
            try:
                _, history = reader(stale_ok=True)
            except TypeError:
                _, history = reader()
        except Exception:
            log.debug('session-panel feed: origin history unavailable', exc_info=True)
            return tasks
        return _load_feed().with_terminal_history(tasks, history, ids)

    def card(self, card_id: str, board: str):
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


FEED_ID = f'{socket.gethostname()}:{os.getpid()}'
LAUNCH_PROFILE = _launch_profile()


def feed():
    """The process's one feed, started on first use (its thread ticks every TICK_S until stopped)."""
    core = _load_feed()
    with core.PROCESS_LOCK:
        if core.PROCESS_FEED is None or core.PROCESS_FEED.stopped.is_set():
            core.PROCESS_FEED = core.Feed(CoreReaders(), emit=_emit)
            threading.Thread(target=_run, args=(core.PROCESS_FEED,), name='session-panel-feed', daemon=True).start()
        return core.PROCESS_FEED


def stop() -> None:
    """Stop the feed thread (plugin unload / disable)."""
    f = _load_feed().PROCESS_FEED
    if f is not None:
        f.stopped.set()


def _run(f) -> None:
    while not f.stopped.is_set():
        started = time.monotonic()
        try:
            f.tick()
        except Exception:
            log.warning('session-panel feed tick failed', exc_info=True)
        f.stopped.wait(max(0.2, TICK_S - (time.monotonic() - started)))


class ChangesBody(BaseModel):
    sessions: list[str] = Field(default_factory=list, max_length=200)
    since: int = 0
    epoch: Optional[str] = None


router = APIRouter()


@router.post('/changes')
async def changes(body: ChangesBody):
    profile = _request_profile()
    if profile != LAUNCH_PROFILE:
        return {'feed': FEED_ID, 'profile': profile, 'profile_unsupported': True}
    f = feed()
    out = await run_in_threadpool(f.changes, body.sessions, since=body.since, epoch=body.epoch)
    return json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, **out})


@router.get('/status')
def status():
    f = _load_feed().PROCESS_FEED
    if f is None:
        return {'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'running': False}
    return json_safe({'feed': FEED_ID, 'profile': LAUNCH_PROFILE, 'running': not f.stopped.is_set(), **f.status()})
