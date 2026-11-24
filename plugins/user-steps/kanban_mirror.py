"""Server-owned projection of blocked card asks into the canonical decision queue.

A source reference is provenance, never write authority. Only an immutable binding
written by this producer can authorize the receiver, and the receiver rechecks the
exact blocked ask and block generation under the board write transaction.
"""

import contextvars
import hashlib
import importlib.util
import inspect
import itertools
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MISMATCH = "Request no longer matches its frozen server binding"

# Change-keyed reconcile (design t_01a1d563324d7e8e card L). Desktop polls ``/requests`` per open chat and each
# poll reconciled every board, every bound card (older owners' too) and the compression tip from scratch: ~0.18 s
# of CPU and ~100 connection opens per poll on the hub with no blocked ask at all. Now:
#   * every board / state.db / action-store read goes through a kept read-only connection (``_kept``), and
#   * a pass first reads the key of its inputs (``Mirror._key``): the board rows it reads (blocked asks, bound
#     cards, their lifecycle-event id and local subscribers), the action store's commit counter, the
#     relevant sessions (including custom-source local subscribers) and the user-session table shape, the
#     owner, the board set and the fenced-core gate. An unchanged key skips the pass.
# A pass runs in full anyway when its key cannot be read, after a pass that deferred or rejected anything, when a
# resolver is injected (no key covers it), and at least every FULL_EVERY_S. That ceiling is the P1 fallback: a
# change the key does not see (a compression parent ended after its child was created) is picked up that late.
FULL_EVERY_S = 60.0
_EVENT_KINDS = ("created", "blocked", "block_loop_detected", "unblocked", "archived", "completed")
_MEMO: dict = {}  # owner key -> (inputs key, monotonic time of the full pass that produced it)
_MEMO_LOCK = threading.Lock()
_MEMO_MAX = 256
_REMOTE_FRESH: dict = {}  # owner key -> monotonic start of its last clean strict pass (``Mirror.reconcile``)
_REMOTE_CLEARED: dict = {}  # owner key -> monotonic time a failed strict pass last cleared its fresh mark
_REMOTE_FRESH_LOCK = threading.Lock()
_REMOTE_FRESH_MAX = 512
# Panel producers retry unavailable remote membership independently of the local
# mirror. Source churn must not reset an unreachable owner's retry deadline.
_PANEL_REMOTE = {}  # (store path, owner) -> (input key, deadline, failures, error)
_PANEL_REMOTE_LOCK = threading.Lock()
PANEL_REMOTE_RETRY_S = 5.0
PANEL_REMOTE_RETRY_MAX_S = 300.0


def _mark_remote_fresh(key, started):
    with _REMOTE_FRESH_LOCK:
        # A pass that started before a later failed strict pass cleared the mark cannot re-mark the owner.
        if started < _REMOTE_CLEARED.get(key, float('-inf')):
            return
        if started >= _REMOTE_FRESH.get(key, float('-inf')):
            _REMOTE_FRESH.pop(key, None)
            _REMOTE_FRESH[key] = started
        while len(_REMOTE_FRESH) > _REMOTE_FRESH_MAX:
            del _REMOTE_FRESH[next(iter(_REMOTE_FRESH))]


def _clear_remote_fresh(key):
    with _REMOTE_FRESH_LOCK:
        _REMOTE_FRESH.pop(key, None)
        _REMOTE_CLEARED.pop(key, None)
        _REMOTE_CLEARED[key] = time.monotonic()
        while len(_REMOTE_CLEARED) > _REMOTE_FRESH_MAX:
            del _REMOTE_CLEARED[next(iter(_REMOTE_CLEARED))]


# Remote passes a Desktop poll found stale run off the request thread, on at most BACKGROUND_WORKERS daemon
# threads, one queued or running pass per owner. Hub 2026-11-23: every poll of a chat without a fresh feed pass
# ran the strict linked-card projection inline (3-21 s, queued on kanban_session_activity._links_lock), so each
# poll held a web worker thread and the backend answered other Desktop calls with "Timed out connecting to Hermes
# backend". A remote ask now reaches the queue when its owner's background pass finishes (queue wait included).
BACKGROUND_WORKERS = 2
_BACKGROUND_LOCK = threading.Condition()
_BACKGROUND_PENDING: dict = {}  # owner key -> (mirror, owner, the poll's context, passes wanted), waiting
_BACKGROUND_RUNNING: set = set()  # owner keys a worker is reconciling now
_BACKGROUND_WORKERS_ALIVE = 0
# [daveey] Owners whose local pass already ran in this process. A poll for one of them answers from the action
# store (the last good result) and queues its local pass here instead of running it on the request thread: the
# local pass re-reads every card binding (222 on the hub, 2026-11-23) through one kept connection per board, and
# concurrent polls queued on it for 10-86 s ("Action queue unavailable" at Desktop's 10 s budget). A cold owner
# still runs it inline, so a newly opened chat never shows an empty queue for want of a first pass.
_LOCAL_SEEN: dict = {}  # owner key -> None, insertion-ordered for eviction
_LOCAL_SEEN_MAX = 512


def _mark_local_seen(key):
    with _MEMO_LOCK:
        _LOCAL_SEEN.pop(key, None)
        _LOCAL_SEEN[key] = None
        while len(_LOCAL_SEEN) > _LOCAL_SEEN_MAX:
            del _LOCAL_SEEN[next(iter(_LOCAL_SEEN))]


def _local_key(mirror, owner):
    """Warm is per action store and owner: another store (a test's, a profile's) has not held this owner's result."""
    store = getattr(mirror, 'store', None)
    return encoded([str(getattr(store, 'path', None)), owner])


def _local_seen(key):
    with _MEMO_LOCK:
        return key in _LOCAL_SEEN


def _schedule_remote(mirror, owner):
    """Queue one background remote pass for *owner*; False when it was already queued or running."""
    return _schedule(mirror, owner, ('remote',))


def _schedule(mirror, owner, passes):
    """Queue one background pass for *owner* doing *passes* ('remote', 'local'); False when one was already
    queued (its passes are widened to include these) or running (the next poll queues again).

    Every call, a duplicate included, starts a worker while passes are queued and a slot is free, so a queued
    pass is never left without one (a worker exiting, dying or failing to start is replaced by the next poll)."""
    global _BACKGROUND_WORKERS_ALIVE
    key = encoded(owner)
    with _BACKGROUND_LOCK:
        pending = _BACKGROUND_PENDING.get(key)
        if pending is not None:
            _BACKGROUND_PENDING[key] = pending[:3] + (pending[3] | frozenset(passes),)
        queued = pending is None and key not in _BACKGROUND_RUNNING
        if queued:
            # Threads do not inherit contextvars: the pass must see the poll's profile (multiplexed HERMES_HOME
            # overrides), exactly as the outbox worker runs under its captured context.
            _BACKGROUND_PENDING[key] = (mirror, owner, contextvars.copy_context(), frozenset(passes))
        start = bool(_BACKGROUND_PENDING) and _BACKGROUND_WORKERS_ALIVE < BACKGROUND_WORKERS
        if start:
            _BACKGROUND_WORKERS_ALIVE += 1
    if start:
        try:
            threading.Thread(target=_drain_remote, name='user-steps-card-pass', daemon=True).start()
        except BaseException as exc:  # e.g. "can't start new thread": give the slot back; the next poll starts one
            with _BACKGROUND_LOCK:
                _BACKGROUND_WORKERS_ALIVE -= 1
                _BACKGROUND_LOCK.notify_all()
            if not isinstance(exc, Exception):
                raise
            logging.getLogger('user-steps').warning('Background card pass not started', exc_info=True)
    return queued


def _background_idle(timeout):
    """Wait until no background card pass (remote or local) is queued or running (tests); True when idle."""
    with _BACKGROUND_LOCK:
        return _BACKGROUND_LOCK.wait_for(
            lambda: not _BACKGROUND_WORKERS_ALIVE and not _BACKGROUND_PENDING and not _BACKGROUND_RUNNING, timeout)


def _drain_remote():
    global _BACKGROUND_WORKERS_ALIVE
    log = logging.getLogger('user-steps')
    released = False
    try:
        while True:
            with _BACKGROUND_LOCK:
                if not _BACKGROUND_PENDING:
                    # Leave in the same critical section that saw the queue empty: a pass queued after this
                    # point sees the freed slot and starts a worker.
                    _BACKGROUND_WORKERS_ALIVE -= 1
                    released = True
                    _BACKGROUND_LOCK.notify_all()
                    return
                key, (mirror, owner, ctx, passes) = next(iter(_BACKGROUND_PENDING.items()))
                del _BACKGROUND_PENDING[key]
                _BACKGROUND_RUNNING.add(key)
            try:
                if 'remote' in passes:
                    try:
                        if not ctx.run(mirror._reconcile_remote, owner):
                            log.debug('Background remote card pass deferred; owner stays stale')
                    except Exception:  # a failed pass stays stale: the next poll queues another
                        log.warning('Background remote card pass failed', exc_info=True)
                if 'local' in passes:
                    try:
                        ctx.run(mirror._reconcile_local, owner, False)
                    except Exception:  # the store keeps the last good requests; the next poll queues another
                        log.warning('Background local card pass failed', exc_info=True)
            finally:
                with _BACKGROUND_LOCK:
                    _BACKGROUND_RUNNING.discard(key)
                    _BACKGROUND_LOCK.notify_all()
    finally:
        if not released:  # died on a BaseException: give the slot back (the next poll starts a replacement)
            with _BACKGROUND_LOCK:
                _BACKGROUND_WORKERS_ALIVE -= 1
                _BACKGROUND_LOCK.notify_all()


def _remote_fresh(key, within_s):
    with _REMOTE_FRESH_LOCK:
        started = _REMOTE_FRESH.get(key)
    return started is not None and 0 <= time.monotonic() - started <= within_s


_POOL: dict = {}
_POOL_LOCK = threading.Lock()
_POOL_MAX = 64
_RETIRED = False
_GEN = itertools.count(1)
_SCHEMA_READY: set = set()  # (store path, dev, ino) whose kanban_projections table exists


def _open_ro(path):
    """``core._ro_connect`` for a connection kept across threads (each use holds the entry's lock)."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        c.execute("select 1 from sqlite_master limit 1")
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=10, check_same_thread=False)
        c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


class _Kept:
    __slots__ = ("conn", "ident", "gen", "lock", "owner", "memo", "retired")

    def __init__(self):
        self.conn: sqlite3.Connection | None = None
        self.ident = None
        self.gen = 0  # a fresh connection restarts PRAGMA data_version: keys carry the generation
        self.lock = threading.Lock()
        self.owner = None
        self.retired = False
        self.memo: dict = {}  # per-connection derived values, valid for one (gen, data_version)


def _close_entry(entry):
    """Caller holds entry.lock. Clear ownership even if close raises."""
    conn, entry.conn = entry.conn, None
    entry.memo.clear()
    if conn is not None:
        conn.close()


def close_pool():
    """Retire this module's readers without interrupting an active SQLite read.

    Never wait for a borrower while holding the pool lock: a borrower may nest
    another read. Busy entries close on return; queued opens cannot be retained.
    Old module references remain usable, but only with one-shot readers.
    """
    global _RETIRED
    with _POOL_LOCK:
        _RETIRED = True
        entries = list(_POOL.values())
        _POOL.clear()
        for entry in entries:
            entry.retired = True
            if entry.lock.acquire(blocking=False):
                try:
                    _close_entry(entry)
                finally:
                    entry.lock.release()


def _evict():
    for key in list(_POOL):
        stale = _POOL[key]
        if stale.lock.acquire(blocking=False):
            try:
                del _POOL[key]
                stale.retired = True
                _close_entry(stale)
            finally:
                stale.lock.release()


@contextmanager
def _kept(path, *, entry_out=None):
    """A kept read-only connection to ``path``, reopened when the file is replaced (dev, ino) or after an
    error. Autocommit reads only; a nested use on the same thread gets a one-shot connection instead."""
    st = os.stat(path)  # raises like an open of a missing file would
    ident = (st.st_dev, st.st_ino)
    with _POOL_LOCK:
        entry = _POOL.get(str(path))
        if entry is None:
            if len(_POOL) >= _POOL_MAX:
                _evict()
            entry = _Kept()
            entry.retired = _RETIRED
            if not _RETIRED:
                _POOL[str(path)] = entry
    if entry.owner == threading.get_ident():
        c = _open_ro(path)
        try:
            yield c
        finally:
            c.close()
        return
    try:
        with entry.lock:
            entry.owner = threading.get_ident()
            try:
                if entry.conn is None or entry.ident != ident:
                    if entry.conn is not None:
                        entry.conn.close()
                        entry.conn = None
                    entry.conn, entry.ident, entry.gen, entry.memo = _open_ro(path), ident, next(_GEN), {}
                c = entry.conn
                if entry_out is not None:
                    entry_out.append(entry)
                try:
                    yield c
                except sqlite3.Error:
                    entry.conn = None  # never reuse a connection that failed mid-read
                    c.close()
                    raise
                finally:
                    if entry.conn is not None and c.in_transaction:
                        c.rollback()
            finally:
                entry.owner = None
                with _POOL_LOCK:
                    if entry.retired:
                        _close_entry(entry)
    finally:
        # close_pool() may retire the entry after the check above but before entry.lock is
        # released; its non-blocking acquire then fails and it skips the close. It sets
        # ``retired`` before that acquire, so recheck after release: either we get the lock and
        # close, or its holder (close_pool or a queued borrower) closes on its own way out.
        if entry.retired and entry.conn is not None and entry.lock.acquire(blocking=False):
            try:
                _close_entry(entry)
            finally:
                entry.lock.release()


def _version(c):
    return c.execute("PRAGMA data_version").fetchone()[0]


class _NoAccounting:
    """Stand-in for core's ``loop_accounting.Pass`` outside a Hermes runtime (or on an older core)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def skip(self):
        pass

    def change(self, changed=True):
        pass

    def fallback(self, fell_back=True):
        pass

    def rows(self, n):
        pass


def _accounted_pass(name):
    """One P1 loop-accounting pass (core ``hermes_cli.loop_accounting``), or a no-op without it."""
    try:
        from hermes_cli.loop_accounting import pass_
    except Exception:  # outside a Hermes runtime, or a core without P1
        return _NoAccounting()
    return pass_(name)


def _digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Mirror:
    exclusive_dispatch = True  # retirement and every card-delivery attempt share operation custody

    def __init__(
        self,
        core,
        store,
        *,
        boards=None,
        backend=None,
        session_exists=None,
        tip=None,
        session_present=None,
    ):
        self.core, self.store, self.backend = core, store, backend
        path = Path(__file__).with_name('remote_cards.py')
        spec = importlib.util.spec_from_file_location('_user_steps_remote_cards', path)
        remote = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(remote)
        self.remote = remote.Remote(self)
        board_source = core.kanban_boards if boards is None else boards
        self.boards = dict(board_source() if callable(board_source) else board_source)
        self.session_exists = session_exists or self._user_session
        self.session_present = session_present or session_exists or self._stored_session
        self.tip = tip or self._tip
        if getattr(store, 'read_only', False):
            return  # only the reconciliation producer owns schema initialization
        try:
            st = os.stat(store.path)
            ready = (str(store.path), st.st_dev, st.st_ino)
        except (OSError, AttributeError, TypeError):
            ready = None
        if ready is not None and ready in _SCHEMA_READY:
            return  # a Mirror is built per poll: one write transaction per store file and process is enough
        with store._tx() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS kanban_projections ("
                "namespace TEXT NOT NULL, request_id TEXT NOT NULL, session TEXT NOT NULL, "
                "board TEXT NOT NULL, task TEXT NOT NULL, snapshot TEXT NOT NULL, request TEXT NOT NULL, "
                "PRIMARY KEY(namespace,request_id))"
            )
            c.execute('CREATE TABLE IF NOT EXISTS kanban_projection_display ('
                      'namespace TEXT NOT NULL, request_id TEXT NOT NULL, body TEXT NOT NULL, '
                      'PRIMARY KEY(namespace,request_id))')
        if ready is not None:
            _SCHEMA_READY.add(ready)

    def _session_row(self, sid):
        # Read failure is not evidence that a card lost its owner.
        with _kept(self.core._state_db()) as c:
            return c.execute(
                "SELECT id,parent_session_id,end_reason,source FROM sessions WHERE id=?",
                (sid,),
            ).fetchone()

    def _stored_session(self, sid):
        return bool(self._session_row(sid))

    def _user_session(self, sid):
        row = self._session_row(sid)
        return bool(row and row[3] in ("desktop", "cli", "tui", "web"))

    def _tip(self, sid):
        # Core alone owns continuation classification (branches/resets/delegates
        # are NOT continuations). Never use the plugin's legacy substring walk.
        try:
            from hermes_state_compression import (
                _CHAIN_STEP_SQL,
                _walk_compression_chain,
            )
        except ImportError as exc:
            raise OSError("Core continuation resolver unavailable") from exc
        with _kept(self.core._state_db()) as c:
            chain = _walk_compression_chain(
                sid, lambda s: c.execute(_CHAIN_STEP_SQL, (s,)).fetchone()
            )
            return chain[-1]

    def _api(self):
        if self.backend is not None:
            return self.backend
        from hermes_cli import kanban_db
        from hermes_cli.kanban_db_connect import connect
        from types import SimpleNamespace

        try:  # core's one-step release of a spend/scope stop; absent on an older core
            from hermes_cli import kanban_release
        except ImportError:
            kanban_release = None
        return SimpleNamespace(
            connect=connect,
            write_txn=kanban_db.write_txn,
            add_comment=kanban_db.add_comment,
            unblock_task=kanban_db.unblock_task,
            archive_task=kanban_db.archive_task,
            release=kanban_release,
        )

    def _path(self, board):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", board) or board not in self.boards:
            raise self.core.ActionError("Unknown projection board", 409)
        return Path(self.boards[board])

    def _snapshot(self, c, task, owner, *, presentation=True):
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        if row.get("status") not in ("blocked", "triage") or str(
            row.get("idempotency_key") or ""
        ).startswith("user-steps:decision-card:"):
            return None
        try:
            hr = json.loads(row.get("human_request") or "null")
        except (ValueError, TypeError):
            return None
        if (
            not isinstance(hr, dict)
            or not hr.get("action")
            or hr.get("interaction")
            not in ("reply", "approval", "user_terminal", "physical", "external")
        ):
            return None
        # Prefer the creating user session; otherwise the earliest local user subscriber.
        # A touched/watching session never gets another copy of the same card ask.
        sid = row.get("session_id")
        if not sid or not self.session_exists(sid):
            try:
                subs = c.execute(
                    "SELECT chat_id FROM kanban_notify_subs WHERE task_id=? "
                    "AND platform IN ('desktop','cli','tui','web') "
                    "AND (notifier_profile=? OR notifier_profile IS NULL) "
                    "ORDER BY created_at,chat_id",
                    (task, owner["profile"]),
                ).fetchall()
            except sqlite3.OperationalError:
                subs = []
            # The local user-surface subscription is provenance even for legacy
            # custom-source chats. Require its stored session, not a source alias.
            sid = next((r[0] for r in subs if self.session_present(r[0])), None)
        if not sid or (presentation and self.tip(sid) != owner["storedSessionId"]):
            return None
        # A new identical block after an unblock is a NEW question, not an old answer replay.
        generation = c.execute(
            "SELECT MAX(id) FROM task_events WHERE task_id=? AND kind IN "
            "('created','blocked','block_loop_detected','unblocked','archived','completed')",
            (task,),
        ).fetchone()[0]
        snap = dict(
            task=task,
            session=sid,
            generation=generation,
            ask=hr,
            title=row.get("title") or hr["action"],
        )
        cited = self._citations(c, task, hr, owner)
        if cited:
            snap['cited_set'] = cited
        return snap

    def _citations(self, c, task, hr, owner):
        if hr.get('choices'):
            return []
        event = c.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='blocked' ORDER BY id DESC LIMIT 1",
                          (task,)).fetchone()
        # Legacy event payloads are not necessarily JSON objects. A corrupt
        # reason must not prevent unrelated cards from projecting.
        try:
            payload = json.loads(event[0] or '{}') if event else {}
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        text = '\n'.join(str(hr.get(k) or '') for k in ('action', 'scope', 'evidence'))
        text += '\n' + str(payload.get('reason') or '')
        ids = dict.fromkeys(re.findall(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', text))
        found = []
        with self.store._read() as reader:
            for rid in ids:
                row = reader.execute('SELECT session FROM requests WHERE namespace=? AND id=?',
                                     (self.store._ns(owner), rid)).fetchone()
                if row is None or row[0] == owner['storedSessionId']:
                    continue
                # Projections are already delivered by their own card. Never
                # create a mirror of a mirror, even from another session.
                if reader.execute('SELECT 1 FROM kanban_projections WHERE namespace=? AND request_id=?',
                                  (self.store._ns(owner), rid)).fetchone():
                    continue
                request = self.store._get(reader, dict(owner, storedSessionId=row[0]), rid)
                if request['state'] == 'open' and request['kind'] == 'choice' and request['choices']:
                    found.append(request)
        return found

    @staticmethod
    def _source_snapshot(snap):
        return {k: v for k, v in snap.items() if k not in ('cited_id', 'cited_revision')}

    def _matches_snapshot(self, current, frozen):
        return encoded(current) == encoded(self._source_snapshot(json.loads(frozen)))

    def _read_snapshot(self, board, task, owner, *, presentation=True):
        with _kept(self._path(board)) as c:
            return self._snapshot(c, task, owner, presentation=presentation)

    def _request(self, owner, board, snap, incarnation=0):
        if snap.get('cited_id'):
            cited = next(r for r in snap['cited_set'] if r['requestId'] == snap['cited_id'])
            identity = encoded([owner, board, self._source_snapshot(snap),
                                snap['cited_id'], snap['cited_revision']]
                               + ([incarnation] if incarnation else []))
            rid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'hermes:kanban-ask:' + identity))
            ref = dict(gatewayId=owner['gatewayId'], profile=owner['profile'],
                       type='kanban', board=board, id=snap['task'])
            choices = [dict(id='q-' + ch['id'], label=ch['label'], effect='answer',
                            **{k: ch[k] for k in ('explain', 'recommended') if k in ch})
                       for ch in cited['choices']]
            return self.core.validate_action(dict(
                schemaVersion=2, owner=owner, requestId=rid, revision=1, kind='choice',
                title=cited['title'], scope=dict(markdown=cited['scope']['markdown'],
                    complete=True, sourceRefs=[ref]), limits=[], exclusions=[],
                outcomes=dict(answer='Record the selected value on the source card.'),
                choices=choices, aliases=[], state='open'))
        # UUID5 identity includes the exact ask, lifecycle and owner, not a fuzzy title.
        # A later incarnation re-projects the same ask after an earlier projection was
        # withdrawn/retired; incarnation 0 keeps the original identity byte for byte.
        identity = encoded(
            [owner, board, snap['remote']['target'] if snap.get('remote') else snap]
            + ([incarnation] if incarnation else [])
        )
        rid = str(uuid.uuid5(uuid.NAMESPACE_URL, "hermes:kanban-ask:" + identity))
        hr = snap["ask"]
        options = hr.get("choices") or [
            dict(label="Unblock", value="unblock"),
            dict(label="Archive", value="archive"),
        ]
        if not isinstance(options, list) or not options:
            raise self.core.ActionError("Invalid card choices")
        choices = []
        for i, opt in enumerate(options):
            if (
                not isinstance(opt, dict)
                or not isinstance(opt.get("label"), str)
                or not isinstance(opt.get("value"), str)
            ):
                raise self.core.ActionError("Card choices require label and value")
            choices.append(dict(id=f"card-{i}", label=opt["label"], effect="answer"))
        ref = dict(
            gatewayId=snap.get('remote', {}).get('target', {}).get('ownerGateway', owner['gatewayId']),
            profile=snap.get('remote', {}).get('target', {}).get('ownerProfile', owner['profile']),
            type="kanban",
            board=board,
            id=snap["task"],
        )
        markdown = "\n\n".join(
            str(hr[k])
            for k in ("action", "why_human", "scope", "evidence")
            if hr.get(k)
        )
        markdown += (
            "\n\nSelecting an option records its value on this card and resumes it. "
        )
        markdown += "Archive abandons the card." if not hr.get("choices") else ""
        markdown += " No commands run from this panel."
        if hr["interaction"] == "user_terminal":
            markdown += " Unblock resumes without a terminal result; it does not authenticate or finish the command."
        return self.core.validate_action(
            dict(
                schemaVersion=2,
                owner=owner,
                requestId=rid,
                revision=1,
                kind="choice",
                title=snap["title"],
                scope=dict(markdown=markdown, complete=True, sourceRefs=[ref]),
                limits=[],
                exclusions=[],
                outcomes=dict(answer="Record the selected value on the source card."),
                choices=choices,
                aliases=[],
                state="open",
            )
        )

    def views(self, owners, *, include_withdrawn=False):
        """Capture all owner presentation inputs once; project only after closing SQL.

        Optional mirror tables may be absent in a store with no card producer yet.
        This is a read seam, not reconciliation or decision authority.
        """
        keys = list(dict.fromkeys((self.store._ns(o), o['storedSessionId']) for o in owners))
        if not keys:
            return []
        scope = ('WITH scope(namespace,session) AS '
                 "(SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?)) ")
        params = (json.dumps(keys),)
        with self.store._read() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            rows = c.execute(scope +
                'SELECT r.namespace,r.session,r.id,r.state,r.resolution,v.body,l.created_at,l.withdrawal '
                'FROM scope s JOIN requests r ON r.namespace=s.namespace AND r.session=s.session '
                'LEFT JOIN revisions v ON r.namespace=v.namespace AND r.id=v.id AND r.revision=v.revision '
                'LEFT JOIN lifecycle l ON r.namespace=l.namespace AND r.id=l.id ORDER BY r.rowid', params).fetchall()
            bindings = c.execute(scope +
                'SELECT p.* FROM scope s JOIN kanban_projections p '
                'ON p.namespace=s.namespace AND p.session=s.session', params).fetchall() if 'kanban_projections' in tables else []
            displays = c.execute(scope +
                'SELECT d.* FROM scope s JOIN requests r ON r.namespace=s.namespace AND r.session=s.session '
                'JOIN kanban_projection_display d ON d.namespace=r.namespace AND d.request_id=r.id',
                params).fetchall() if 'kanban_projection_display' in tables else []
            operations = c.execute(scope +
                'SELECT o.namespace,o.session,o.id,o.body FROM scope s JOIN operations o '
                'ON o.namespace=s.namespace AND o.session=s.session', params).fetchall()
        bound = {(r['namespace'], r['session'], r['request_id']): dict(r) for r in bindings}
        shown = {(r['namespace'], r['request_id']): r for r in displays}
        ops = {(r['namespace'], r['session'], r['id']): r['body'] for r in operations}
        by_owner = {key: [] for key in keys}
        for row in rows:
            if not include_withdrawn and row['state'] == 'withdrawn':
                continue
            key = (row['namespace'], row['session'], row['id'])
            request = self.store._project_view(row)
            frozen = json.loads(row['body'])
            frozen['state'] = row['state']
            oid = (request.get('resolution') or {}).get('operationId')
            captured = dict(binding=bound.get(key), frozen=frozen,
                            display_available='kanban_projection_display' in tables,
                            display=shown.get((row['namespace'], row['id'])),
                            operation=ops.get((row['namespace'], row['session'], oid)))
            by_owner[key[:2]].append(self.present(request, captured=captured))
        return [r for key in keys for r in by_owner[key]]

    def present(self, request, *, captured=None):
        """A corrupt optional binding must never poison healthy requests."""
        try:
            return self._present(request, captured=captured)
        except (ValueError, TypeError, KeyError, StopIteration, sqlite3.Error, self.core.ActionError) as exc:
            logging.getLogger('user-steps').warning('Card presentation unavailable: %s', exc)
            return request

    def _present(self, request, *, captured=None):
        """Display-only metadata; never rewrite a frozen request or mint a new tile."""
        binding = self.binding(request['owner'], request['requestId']) if captured is None else captured['binding']
        if not binding:
            return request
        frozen = self.store.get(request['owner'], request['requestId']) if captured is None else dict(captured['frozen'])
        frozen.pop('resolution', None)
        frozen['state'] = json.loads(binding['request'])['state']
        if encoded(frozen) != binding['request']:
            return request
        snap = json.loads(binding['snapshot'])
        if snap.get('remote'):
            if captured is None:
                with self.store._read() as c:
                    display = c.execute('SELECT body FROM kanban_projection_display WHERE namespace=? AND request_id=?',
                                        (self.store._ns(request['owner']), request['requestId'])).fetchone()
            else:
                if not captured['display_available']:
                    raise sqlite3.OperationalError('Card presentation display table unavailable')
                display = captured['display']
            if display is not None:
                shown = json.loads(display['body'])
                if shown['remote']['target'] == snap['remote']['target']:
                    snap = shown
                    request = dict(request, title=shown['title'])
        hr = snap['ask']
        if snap.get('cited_id'):
            cited = next(r for r in snap['cited_set'] if r['requestId'] == snap['cited_id'])
            question = re.sub(r'\b(?:t_[a-zA-Z0-9_]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\b',
                              'this question', cited['title'])
            recommended = next(('q-' + ch['id'] for ch in cited['choices'] if ch.get('recommended')), None)
            return dict(request, presentation=dict(kind='kanban-question', question=question,
                        details=cited['scope']['markdown'], recommendedChoiceId=recommended,
                        other=True, sourceRef=request['scope']['sourceRefs'][0]))
        question = re.split(r'\bUnblock\s*=', hr['action'], maxsplit=1, flags=re.I)[0].strip()
        question = question or snap['title']
        question = re.sub(r'\bt_[a-zA-Z0-9_]+\b', 'this card', question)
        options = hr.get('choices') or []
        recommended = next((f'card-{i}' for i, opt in enumerate(options)
                            if opt.get('recommended') is True), None)
        if recommended is None:
            # kanban_create's choice schema carries only label/value, so a label is how asks mark their pick.
            marked = [i for i, opt in enumerate(options)
                      if re.search(r'\(recommended\)\s*$', str(opt.get('label') or ''), re.I)]
            if len(marked) == 1:
                recommended = f'card-{marked[0]}'
        details = '\n\n'.join(str(hr[k]) for k in ('action', 'why_human', 'scope', 'evidence') if hr.get(k))
        note = None
        if snap.get('remote'):
            if captured is None:
                note = self.remote.delivery_note(request['owner'], request)
            elif (request.get('resolution') or {}).get('operationId'):
                if captured['operation'] is None:
                    raise self.core.ActionError('Unknown operation in this session', 404)
                operation = json.loads(captured['operation'])
                if (operation.get('requestId'), operation.get('revision')) != (request['requestId'], request['revision']):
                    raise self.core.ActionError('Operation does not match presented request', 409)
                note = self.remote.project_delivery_note(operation, binding)
        return dict(request, **(dict(delivery=note) if note else {}), presentation=dict(kind='kanban-question', question=question,
                    details=details, recommendedChoiceId=recommended, other=True,
                    sourceRef=dict(gatewayId=snap.get('remote', {}).get('target', {}).get('ownerGateway', request['owner']['gatewayId']),
                                   profile=snap.get('remote', {}).get('target', {}).get('ownerProfile', request['owner']['profile']), type='kanban',
                                   board=binding['board'], id=binding['task'])))

    def decide_other(self, owner, decision):
        """An informational answer to an exact card ask, not a new permission.

        The supplemental choice is server-owned, not added to the immutable v2
        choices or numbered-list snapshot. Existing tiles keep their UUID/revision.
        """
        def supplemental(c, request, payload):
            if payload['choiceId'] != 'answer-text':
                return None
            row = c.execute('SELECT request FROM kanban_projections WHERE namespace=? AND request_id=? AND session=?',
                            (self.store._ns(owner), request['requestId'], owner['storedSessionId'])).fetchone()
            if not row or encoded(request) != row['request']:
                raise self.core.ActionError('Exact server projection binding required', 409)
            return dict(id='answer-text', label='Other (type your answer)', effect='answer')
        return self.store._decide(owner, decision, supplemental_choice=supplemental,
                                  provenance=dict(type='kanban_other', version=1))

    def binding(self, owner, rid):
        with self.store._read() as c:
            row = c.execute(
                "SELECT * FROM kanban_projections WHERE namespace=? AND request_id=? AND session=?",
                (self.store._ns(owner), rid, owner["storedSessionId"]),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _ask_scope(snap):
        """Actionable source identity, independent of projection/presentation churn.

        Evidence, rationale, title, lifecycle generation and compression owner do
        not create new human work. Different action/scope/choices remain distinct.
        Remote owner identity prevents collisions with a same-named local card.
        """
        target = snap.get('remote', {}).get('target', {})
        ask = snap['ask']
        cited = next((r for r in snap.get('cited_set', [])
                      if r['requestId'] == snap.get('cited_id')), None)
        return encoded([target.get('ownerGateway'), target.get('ownerProfile'),
                        {k: ask.get(k) for k in ('action', 'scope', 'interaction', 'choices')},
                        ({k: cited.get(k) for k in ('kind', 'scope', 'choices', 'outcomes')}
                         if cited else None)])

    def _withdrawn_scope(self, c, owner, board, snap):
        rows = c.execute(
            "SELECT p.snapshot,w.withdrawal FROM kanban_projections p "
            "JOIN (SELECT namespace,id,withdrawal FROM withdrawal_history "
            "UNION SELECT namespace,id,withdrawal FROM lifecycle WHERE withdrawal IS NOT NULL) w "
            "ON w.namespace=p.namespace AND w.id=p.request_id "
            "WHERE p.namespace=? AND p.board=? AND p.task=?",
            (self.store._ns(owner), board, snap['task']))
        scope = self._ask_scope(snap)
        for row in rows:
            withdrawal = json.loads(row['withdrawal'] or '{}')
            # Only our mechanical relocation/revision retirement permits another
            # incarnation. Explicit withdrawal is durable across future closeouts.
            if (withdrawal.get('by') == 'system' and withdrawal.get('reason') ==
                    'Source card left this block or its ask changed'):
                continue
            if self._ask_scope(json.loads(row['snapshot'])) == scope:
                return True
        return False

    def _current_request(self, owner, board, snap):
        """The request to publish for this exact ask, or None when none may be shown.

        Identity is deterministic; only mechanical relocation/revision retirement
        may mint another incarnation. Explicit withdrawal retires the source scope.
        A recorded answer or an unproven mismatch fails closed (None).
        """
        with self.store._read() as c:
            if self._withdrawn_scope(c, owner, board, snap):
                return None
        n = 0
        while True:
            request = self._request(owner, board, snap, n)
            rid = request["requestId"]
            binding = self.binding(owner, rid)
            if binding is None:
                return request  # unbound id: _publish proves it or rejects the conflict
            stored = self.store.get(owner, rid)
            resolution = stored.pop("resolution", None)
            state = stored["state"]
            if state in ("open", "needs_context"):
                stored["state"] = json.loads(binding["request"]).get("state")
                if encoded(stored) == binding["request"]:
                    return request
                if resolution:
                    return None  # reconcile retires the operation; never deliver it
                self._withdraw(owner, self.store.get(owner, rid))
                if self.store.get(owner, rid)["state"] in ("open", "needs_context"):
                    return None
            elif state == "cancelled":
                op = (
                    self.store.operation(owner, resolution["operationId"])["operation"]
                    if resolution
                    else None
                )
                remote_retired = (snap.get('remote') and op
                                  and (op.get('retired') or {}).get('reason') == 'Remote card answer retired')
                if not remote_retired and (not op or (op.get("retired") or {}).get("reason") != MISMATCH):
                    return None
            elif state == 'resolved' and snap.get('remote') and resolution:
                op = self.store.operation(owner, resolution['operationId'])['operation']
                if not (op['dispatchState'] == 'delivered'
                        and op.get('provenance') == dict(type='kanban_other', version=1)):
                    return None
            elif state != "withdrawn":
                return None
            n += 1

    def _fenced_core(self):
        # A core without fenced transitions would accept a click and then fail
        # delivery. Publish no new tiles there; existing bindings still reconcile.
        try:
            api = self._api()
        except ImportError:
            return False
        for name in ("unblock_task", "archive_task"):
            try:
                params = inspect.signature(getattr(api, name)).parameters
            except (AttributeError, TypeError, ValueError):
                return False
            if any(p.kind is p.VAR_KEYWORD for p in params.values()):
                continue
            if not {"precondition", "comment", "event_extra"} <= set(params):
                return False
        return True

    def reconcile_poll(self, owner, fresh_s):
        """The Desktop request poll: ``reconcile`` without the remote pass while a strict pass for this owner
        completed cleanly and started within ``fresh_s`` seconds; the local pass still runs. [daveey] The session
        panel's feed runs that strict pass for every watched chat whenever its sources move, and the remote pass
        waits on the linked-card projection (3-7 s per poll on the hub, 2026-11-23). An owner no feed covers, or
        whose last strict pass failed, is reconciled in full here as before."""
        self.core.validate_owner(owner)
        if fresh_s <= 0:
            self.reconcile(owner)
            return
        key = encoded(owner)
        passes = set()
        if not _remote_fresh(key, fresh_s):
            # Stale: the remote pass runs on a background worker, never on this request thread; a remote ask it
            # mirrors shows on the first poll after that pass finishes.
            passes.add('remote')
        warm = _local_seen(_local_key(self, owner))
        if warm:
            passes.add('local')  # answer from the store (last good); a local change shows on a later poll
        if passes:  # queued before any inline pass: a cold pass that raises must not strand the remote pass
            _schedule(self, owner, passes)
        if not warm:
            self._reconcile_local(owner, False)  # cold owner: its first pass answers this poll
            _mark_local_seen(_local_key(self, owner))

    def _reconcile_remote(self, owner):
        """One remote pass; True (and *owner* marked fresh for polls, as a clean strict pass does) when clean.

        False when the projection deferred (an unknown snapshot, e.g. an unreachable owner gateway): routine and
        possibly long-lived, so not an error; the owner stays stale. Real failures raise."""
        key = encoded(owner)
        started = time.monotonic()
        if self.remote.reconcile(owner) is False:
            return False
        _mark_remote_fresh(key, started)
        return True

    def _panel_stamp(self, owner):
        """Semantic remote inputs, never a global DB/WAL or commit revision.

        The remote pass reads this owner's requests and namespace-wide historical
        bindings when checking withdrawn scopes. Keep their immutable revisions,
        current lifecycle and operations in one closed ActionStore snapshot. The
        local pass retains its own namespace-wide dependency handling separately.
        Capture before work: a write during reconciliation must be revisited.
        """
        membership = self.remote.membership_capture(owner)
        if membership is None:
            return None
        try:
            ns = self.store._ns(owner)
            with self.store._read() as c:
                bindings = [tuple(r) for r in c.execute(
                    'SELECT * FROM kanban_projections WHERE namespace=? ORDER BY request_id', (ns,))]
                # Unbound requests in this chat also matter: a deterministic
                # remote request ID may conflict with one. Other chats' unbound
                # requests and notification timestamps are not inputs to this pass.
                ids = [r[0] for r in c.execute(
                    'SELECT id FROM requests WHERE namespace=? AND session=? '
                    'UNION SELECT request_id FROM kanban_projections WHERE namespace=? ORDER BY 1',
                    (ns, owner['storedSessionId'], ns))]
                parts = [encoded(owner), membership, bindings]
                scope = json.dumps(ids)
                for table, column, order in (
                        ('requests', 'id', 'id'),
                        ('revisions', 'id', 'id,revision'),
                        ('withdrawal_history', 'id', 'id,revision'),
                        ('operations', 'request', 'id')):
                    parts.append([tuple(r) for r in c.execute(
                        f'SELECT * FROM {table} WHERE namespace=? AND {column} '
                        f'IN (SELECT value FROM json_each(?)) ORDER BY {order}', (ns, scope))])
                parts.append([tuple(r) for r in c.execute(
                    'SELECT id,withdrawal FROM lifecycle WHERE namespace=? '
                    'AND id IN (SELECT value FROM json_each(?)) ORDER BY id', (ns, scope))])
            return _digest(parts)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return None

    def reconcile_panel(self, owner):
        """Strict producer with change-keyed remote reads and bounded failure retry.

        Local changes are never hidden by remote backoff. A skipped failed or
        in-flight remote pass remains deferred; it cannot certify an empty queue.
        Keys are captured BEFORE reconciliation so a concurrent write is owed.
        """
        self.core.validate_owner(owner)
        stamp = self._panel_stamp(owner)
        key = _local_key(self, owner)
        now = time.monotonic()
        with _PANEL_REMOTE_LOCK:
            hit = _PANEL_REMOTE.get(key)
            if hit and now < hit[1] and (hit[3] is not None or (stamp is not None and hit[0] == stamp)):
                run, error = False, hit[3]
            else:
                run, error = True, None
                _PANEL_REMOTE[key] = (stamp, float('inf'), 0, 'Remote card reconciliation in progress')
        self.panel_remote_unchanged = not run and error is None
        if run:
            failures = hit[2] if hit else 0
            try:
                if self.remote.reconcile(owner) is False:
                    error = 'Remote card reconciliation deferred'
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'[:300]
            except BaseException:
                with _PANEL_REMOTE_LOCK:
                    _PANEL_REMOTE.pop(key, None)
                raise
            failures = min(failures + 1, 16) if error else 0
            delay = min(PANEL_REMOTE_RETRY_MAX_S, PANEL_REMOTE_RETRY_S * 2 ** (failures - 1)) if error else FULL_EVERY_S
            with _PANEL_REMOTE_LOCK:
                _PANEL_REMOTE.pop(key, None)
                deadline = time.monotonic() + delay if error else now + FULL_EVERY_S
                _PANEL_REMOTE[key] = (stamp, deadline, failures, error)
                # Never evict in-flight custody and allow a competing pass.
                for old, value in list(_PANEL_REMOTE.items()):
                    if len(_PANEL_REMOTE) <= _REMOTE_FRESH_MAX:
                        break
                    if value[1] != float('inf'):
                        del _PANEL_REMOTE[old]
        try:
            self._reconcile_local(owner, True)
        except BaseException:
            _clear_remote_fresh(encoded(owner))
            raise
        _mark_local_seen(_local_key(self, owner))
        if error:
            if run:
                _clear_remote_fresh(encoded(owner))
            raise RuntimeError(error)
        # A memo hit does not renew the canonical remote observation clock.
        if run:
            _mark_remote_fresh(encoded(owner), now)

    def reconcile(self, owner, *, strict=False):
        self.core.validate_owner(owner)
        key = encoded(owner)
        started = time.monotonic()
        deferred = None
        try:
            if self.remote.reconcile(owner) is False:
                deferred = RuntimeError('Remote card reconciliation deferred')
        except Exception as exc:  # optional remote projection must not suppress local cards
            deferred = exc
            logging.getLogger('user-steps').warning('Remote card reconciliation unavailable: %s', exc)
        # Local cards are mirrored whether or not the remote projection answered. A strict caller
        # learns of the deferred remote work afterwards, never instead of the local pass.
        try:
            self._reconcile_local(owner, strict)
            _mark_local_seen(_local_key(self, owner))
            if strict and deferred is not None:
                raise deferred
        except BaseException:
            if strict:  # the last strict pass failed: polls reconcile in full until a clean one
                _clear_remote_fresh(key)
            raise
        if strict:  # both halves clean (a strict local pass raises when it is not)
            _mark_remote_fresh(key, started)

    def _reconcile_local(self, owner, strict):
        with _accounted_pass("user_steps.mirror_reconcile") as acct:
            fenced = self._fenced_core()
            key = self._key(owner, fenced)
            okey = encoded(owner)
            now = time.monotonic()
            with _MEMO_LOCK:
                memo = _MEMO.get(okey)
            if key is not None and memo is not None and memo[0] == key and now - memo[1] < FULL_EVERY_S:
                acct.skip()
                return
            acct.change(memo is None or memo[0] != key)
            acct.fallback(key is None or (memo is not None and memo[0] == key))  # unkeyable, or the ceiling
            clean = self._reconcile(owner, fenced)
            if strict and not clean:
                raise RuntimeError('Card reconciliation deferred')
            with _MEMO_LOCK:
                if key is None or not clean:
                    _MEMO.pop(okey, None)
                    return
                if okey not in _MEMO and len(_MEMO) >= _MEMO_MAX:
                    del _MEMO[min(_MEMO, key=lambda k: _MEMO[k][1])]
                _MEMO[okey] = (key, now)

    def _key(self, owner, fenced):
        """Digest of everything a pass reads for ``owner``, or None when a pass must run (see FULL_EVERY_S)."""
        if (self.session_exists != self._user_session or self.session_present != self._stored_session
                or self.tip != self._tip):
            return None  # an injected resolver reads inputs no key covers
        try:
            ns = self.store._ns(owner)
            parts = [encoded(owner), sorted((b, str(p)) for b, p in self.boards.items()), fenced]
            held = []
            with _kept(self.store.path, entry_out=held) as c:
                if not held:
                    return None  # nested use on this thread: no kept connection, no commit counter
                entry, version = held[0], _version(c)
                memo_key = ("bound", entry.gen, version, ns)
                bound = entry.memo.get(memo_key)
                if bound is None:
                    bound = [tuple(r) for r in c.execute(
                        "SELECT DISTINCT board, task FROM kanban_projections WHERE namespace=? ORDER BY 1, 2", (ns,))]
                    entry.memo = {k: v for k, v in entry.memo.items() if k[1:3] == (entry.gen, version)}
                    entry.memo[memo_key] = bound
                parts.append(("store", entry.gen, version))
            session_ids = set()
            subscriber_ids = set()
            for board, path in sorted(self.boards.items()):
                ids = [t for b, t in bound if b == board]
                held = []
                with _kept(path, entry_out=held) as c:
                    if not held:
                        return None
                    entry, version = held[0], _version(c)
                    memo_key = ("board", entry.gen, version, tuple(ids))
                    digest = entry.memo.get(memo_key)
                    if digest is None:
                        digest = self._board_digest(c, ids)
                        entry.memo = {k: v for k, v in entry.memo.items() if k[1:3] == (entry.gen, version)}
                        entry.memo[memo_key] = digest
                parts.append((board, digest[0]))
                session_ids.update(digest[1])
                subscriber_ids.update(digest[2])
            with _kept(self.core._state_db()) as c:
                # Include subscriber sources so custom-source compression descendants
                # invalidate on insert/delete. Creator sources must NOT expand this
                # shape: a kanban creator would key unrelated worker-session churn.
                parts.append(tuple(c.execute(
                    "SELECT MAX(rowid), COUNT(*) FROM sessions "
                    "WHERE source IN ('desktop','cli','tui','web') OR source IN "
                    "(SELECT source FROM sessions WHERE id IN (SELECT value FROM json_each(?)))",
                    (json.dumps(sorted(subscriber_ids)),)).fetchone()))
                parts.append([tuple(r) for r in c.execute(
                    "SELECT id,parent_session_id,end_reason,source FROM sessions "
                    "WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id",
                    (json.dumps(sorted(session_ids)),))])
            return _digest(parts)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _board_digest(c, ids):
        """The board rows ``_snapshot``/``_receipt`` read: blocked asks and bound cards, each with its newest
        lifecycle event id, and their local subscribers."""
        have = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        cols = ", ".join(f"t.{x}" for x in ("id", "status", "idempotency_key", "human_request", "session_id", "title")
                         if x in have)
        cols += ", (SELECT e.payload FROM task_events e WHERE e.task_id=t.id AND e.kind='blocked' ORDER BY e.id DESC LIMIT 1)"
        latest = ("(SELECT MAX(e.id) FROM task_events e WHERE e.task_id=t.id AND e.kind IN "
                  f"({','.join('?' * len(_EVENT_KINDS))}))")
        rows = [tuple(r) for r in c.execute(
            f"SELECT {cols}, {latest} FROM tasks t WHERE t.status IN ('blocked','triage') AND t.human_request IS NOT NULL "
            f"UNION SELECT {cols}, {latest} FROM tasks t WHERE t.id IN (SELECT value FROM json_each(?)) ORDER BY 1",
            (*_EVENT_KINDS, *_EVENT_KINDS, json.dumps(ids)))]
        try:
            subs = [tuple(r) for r in c.execute(
                "SELECT task_id, chat_id, platform, notifier_profile, created_at FROM kanban_notify_subs "
                "WHERE task_id IN (SELECT value FROM json_each(?)) ORDER BY 1, 2, 3, 4, 5",
                (json.dumps([r[0] for r in rows]),))]
        except sqlite3.OperationalError:
            subs = None  # _snapshot reads no subscribers then either
        positions = [x for x in ("id", "status", "idempotency_key", "human_request", "session_id", "title")
                     if x in have]
        session_ids = {r[positions.index("session_id")] for r in rows
                       if "session_id" in positions and r[positions.index("session_id")]}
        subscriber_ids = {r[1] for r in (subs or []) if r[1]}
        session_ids.update(subscriber_ids)
        return _digest([rows, subs]), tuple(sorted(session_ids)), tuple(sorted(subscriber_ids))

    def _reconcile(self, owner, fenced):
        """One full pass. False when anything was deferred (the next pass must not be skipped)."""
        clean = True
        if not fenced:
            logging.getLogger("user-steps").warning(
                "Card projection paused: core lacks fenced unblock/archive (deploy core first)"
            )
            boards = {}
        else:
            boards = self.boards
        for board, path in boards.items():
            try:
                with _kept(path) as c:
                    rows = c.execute(
                        "SELECT id FROM tasks WHERE status IN ('blocked','triage') AND human_request IS NOT NULL"
                    ).fetchall()
                    snapshots = [self._snapshot(c, r[0], owner) for r in rows]
            except (sqlite3.Error, OSError):
                clean = False
                continue  # unavailable is NOT evidence to withdraw
            for snap in filter(None, snapshots):
                try:
                    questions = [dict(snap, cited_id=r['requestId'], cited_revision=r['revision'])
                                 for r in snap.get('cited_set', [])] or [snap]
                    for question in questions:
                        if self._relocate(owner, board, question):
                            request = self._current_request(owner, board, question)
                            if request is not None:
                                self._publish(owner, board, question, request)
                except self.core.ActionError as exc:
                    clean = False
                    # A malformed legacy ask or conflicting identity must not hide
                    # unrelated cards. Never adopt a different request as authority.
                    logging.getLogger("user-steps").warning(
                        "Card projection rejected board=%s task=%s: %s",
                        board,
                        snap["task"],
                        exc,
                    )
        with self.store._read() as c:
            bindings = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM kanban_projections WHERE namespace=? AND session=?",
                    (self.store._ns(owner), owner["storedSessionId"]),
                )
            ]
        for binding in bindings:
            try:
                self._reconcile_binding(owner, binding)
            except (ValueError, sqlite3.Error, OSError) as exc:
                clean = False
                logging.getLogger("user-steps").warning(
                    "Card binding reconciliation deferred request=%s: %s",
                    binding["request_id"],
                    exc,
                )
        # Older owners may never reconcile again. Even an unchanged source ask
        # needs its request checked against the frozen binding before work can linger.
        with self.store._read() as c:
            older = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM kanban_projections WHERE namespace=? AND session!=?",
                    (self.store._ns(owner), owner["storedSessionId"]),
                )
            ]
        for binding in older:
            previous = dict(owner, storedSessionId=binding["session"])
            try:
                self._reconcile_binding(previous, binding)
            except (ValueError, sqlite3.Error, OSError) as exc:
                clean = False
                logging.getLogger("user-steps").warning(
                    "Older card binding reconciliation deferred request=%s: %s",
                    binding["request_id"],
                    exc,
                )
        return clean

    def _reconcile_binding(self, owner, binding):
        if json.loads(binding['snapshot']).get('remote'):
            return  # remote owner, not a same-named local board/card
        if binding["board"] not in self.boards:
            return  # temporarily absent board is not departure evidence
        current = self._read_snapshot(
            binding["board"], binding["task"], owner, presentation=False
        )
        request = self.store.get(owner, binding["request_id"])
        tip = self.tip(current["session"]) if current is not None else None
        if request["state"] not in ("open", "needs_context"):
            return
        with self.store._read() as c:
            withdrawn = self._withdrawn_scope(c, owner, binding['board'], json.loads(binding['snapshot']))
        if withdrawn and not request.get('resolution'):
            self._withdraw(owner, request)
            return
        public = {k: v for k, v in request.items() if k != "resolution"}
        public["state"] = json.loads(binding["request"]).get("state")
        mismatch = encoded(public) != binding["request"]
        if self._matches_snapshot(current, binding["snapshot"]) and not mismatch:
            if not request.get("resolution") and tip != owner["storedSessionId"]:
                self._withdraw(owner, request)
            return
        if request.get("resolution"):
            oid = request["resolution"]["operationId"]
            op = self.store.operation(owner, oid)["operation"]
            if op["dispatchState"] in ("delivered", "cancelled"):
                return
            # Preserve a board commit whose outbox acknowledgment is still pending.
            with _kept(self._path(binding["board"])) as c:
                delivered = self._receipt(c, binding, op)
            if not delivered:
                try:
                    self.store.retire_operation(
                        owner,
                        oid,
                        by="system",
                        reason=MISMATCH
                        if mismatch
                        else "Source card ask changed before delivery",
                        evidence="Exact blocked-card snapshot no longer matches"
                        if not mismatch
                        else "Request no longer matches its frozen server binding",
                    )
                except self.core.ActionError as exc:
                    if exc.status != 409 or self.store.operation(owner, oid)[
                        "operation"
                    ]["dispatchState"] not in ("delivered", "cancelled"):
                        raise
            return
        self._withdraw(owner, request)

    def _relocate(self, owner, board, snap):
        # Presentation may rotate on compression, but a recorded answer remains
        # bound to its original owner and blocks a second question for this ask.
        with self.store._read() as c:
            old = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM kanban_projections WHERE namespace=? AND board=? AND task=? AND snapshot=? AND session!=?",
                    (
                        self.store._ns(owner),
                        board,
                        snap["task"],
                        encoded(snap),
                        owner["storedSessionId"],
                    ),
                )
            ]
        for binding in old:
            previous = dict(owner, storedSessionId=binding["session"])
            request = self.store.get(previous, binding["request_id"])
            if not request.get("resolution") and request["state"] in (
                "open",
                "needs_context",
            ):
                self._withdraw(previous, request)
                request = self.store.get(previous, binding["request_id"])
            if request.get("resolution"):
                op = self.store.operation(
                    previous, request["resolution"]["operationId"]
                )["operation"]
                if op["dispatchState"] != "cancelled":
                    return False
        return True

    def _publish(self, owner, board, snap, request):
        values = (
            self.store._ns(owner),
            request["requestId"],
            owner["storedSessionId"],
            board,
            snap["task"],
            encoded(snap),
            encoded(request),
        )

        def bind(c, frozen):
            if self._withdrawn_scope(c, owner, board, snap):
                raise self.core.ActionError('Projection has a withdrawn source ask', 410)
            c.execute("INSERT INTO kanban_projections VALUES (?,?,?,?,?,?,?)", values)

        try:
            self.store.create(owner, request, bind_transaction=bind)
            return
        except self.core.ActionError as exc:
            if exc.status != 409:
                raise
        # A concurrent producer already committed both rows. Never adopt an
        # unbound preexisting request, even if its public body was guessed exactly.
        with self.store._tx() as c:
            existing = self.store._get(c, owner, request["requestId"])
            existing.pop("resolution", None)
            existing["state"] = request["state"]
            bound = c.execute(
                "SELECT * FROM kanban_projections WHERE namespace=? AND request_id=?",
                values[:2],
            ).fetchone()
            if (
                encoded(existing) != encoded(request)
                or bound is None
                or tuple(bound) != values
            ):
                raise self.core.ActionError("Projection identity/binding conflict", 409)

    def _withdraw(self, owner, request):
        try:
            self.store.withdraw(
                owner,
                request["requestId"],
                by="system",
                reason="Source card left this block or its ask changed",
                expected_revision=request["revision"],
            )
            return True
        except self.core.ActionError as exc:
            if exc.status != 409:
                raise
            current = self.store.get(owner, request["requestId"])
            if current["state"] in ("open", "needs_context") and not current.get(
                "resolution"
            ):
                raise  # only a proven concurrent terminal/decision transition is benign
            return False  # a concurrent decision/transition won: not retired

    def _bound(self, op):
        binding = self.binding(op["owner"], op["requestId"])
        if not binding or encoded(op["intent"]["request"]) != binding["request"]:
            raise self.core.ActionError("Exact server projection binding required", 409)
        return binding

    def _receipt(self, c, binding, op):
        fingerprint = hashlib.sha256(
            encoded([op["intent"], op["decision"]]).encode()
        ).hexdigest()
        for row in c.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind IN ('unblocked','archived','user_steps_answer') ORDER BY id DESC",
            (binding["task"],),
        ):
            payload = json.loads(row[0] or "{}")
            if not isinstance(payload, dict):
                raise self.core.ActionError("Invalid card event payload", 409)
            receipt = payload.get("user_steps_projection")
            if (
                not isinstance(receipt, dict)
                or receipt.get("operationId") != op["operationId"]
            ):
                continue
            if receipt.get("fingerprint") != fingerprint:
                raise self.core.ActionError("Projection operation conflict", 409)
            return receipt
        return None

    def _answered_citations(self, c, binding, snap):
        generation = _digest(self._source_snapshot(snap))
        answered = set()
        for row in c.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='user_steps_answer'",
                             (binding['task'],)):
            receipt = json.loads(row[0] or '{}').get('user_steps_projection', {})
            if receipt.get('citation_generation') == generation:
                answered.add((receipt.get('cited_id'), receipt.get('cited_revision')))
        return answered

    def _deliver_cited(self, c, binding, op):
        api = self._api()
        snap = json.loads(binding['snapshot'])
        cited = next(r for r in snap['cited_set'] if r['requestId'] == snap['cited_id'])
        choice_id = op['decision']['choiceId']
        other = choice_id == 'answer-text'
        if other:
            if (op.get('provenance') != dict(type='kanban_other', version=1)
                    or op['intent'].get('executionAuthorized') is not False
                    or op['intent'].get('permissionGrants') != []):
                raise self.core.ActionError('Bound informational answer required', 409)
            label = op['intent']['revisionText']
            original_id = choice_id
        else:
            choice = next((ch for ch in cited['choices'] if 'q-' + ch['id'] == choice_id), None)
            if choice is None:
                raise self.core.ActionError('Unknown card choice', 409)
            label, original_id = choice['label'], choice['id']
        comment = (f'Answer recorded in Desktop for "{cited["title"]}" '
                   f'(request {snap["cited_id"]} rev {snap["cited_revision"]}): {label} [{original_id}]')
        receipt = dict(destination='kanban_ask', board=binding['board'], task=binding['task'],
                       operationId=op['operationId'], value=original_id, executionStarted=False,
                       fingerprint=hashlib.sha256(encoded([op['intent'], op['decision']]).encode()).hexdigest(),
                       citation_generation=_digest(self._source_snapshot(snap)),
                       cited_id=snap['cited_id'], cited_revision=snap['cited_revision'])
        required = {(r['requestId'], r['revision']) for r in snap['cited_set']}
        this = (snap['cited_id'], snap['cited_revision'])

        def fence(conn):
            if self._receipt(conn, binding, op):
                return 'already recorded'
            current = self._snapshot(conn, binding['task'], op['owner'], presentation=False)
            if not self._matches_snapshot(current, binding['snapshot']):
                return 'changed ask'
            return None

        # Serialize the partial receipt and comment on the board, not the action
        # store. The final answer uses core's existing atomic fenced transition.
        with api.write_txn(c):
            old = self._receipt(c, binding, op)
            if old:
                return old
            if fence(c):
                raise self.core.ActionError('Source card changed ask', 409)
            if not required <= self._answered_citations(c, binding, snap) | {this}:
                api.add_comment(c, binding['task'], 'user-steps', comment)
                c.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)",
                          (binding['task'], 'user_steps_answer',
                           encoded(dict(user_steps_projection=receipt)), int(time.time())))
                return receipt

        def final_fence(conn):
            error = fence(conn)
            if error:
                return error
            if not required <= self._answered_citations(conn, binding, snap) | {this}:
                return 'missing cited answers'
            return None

        api.unblock_task(c, binding['task'], precondition=final_fence,
                         comment=('user-steps', comment),
                         event_extra=dict(user_steps_projection=receipt))
        recorded = self._receipt(c, binding, op)
        if not recorded:
            raise self.core.ActionError('Source card ask changed or transition refused', 409)
        return recorded

    def _release(self, api, c, binding, op, comment, receipt, fence, ask):
        """The default Unblock on an ask that offered a release frees the stopped card through core's
        ``hermes kanban release``, as the human act the authenticated Desktop operation is. None otherwise:
        the caller then applies today's unblock unchanged.

        The offer is the ``release_target`` stamp core's stranded-park brief writes on its own ask; no agent,
        tool or CLI request can carry it, and core checks it is still true (``kanban_release.offered``: the
        card still held, the same pending stop, and for a decision card the stop it was filed for). So an
        agent-written ask, an ask filed before the stamp existed, a stale decision card, a card no stop holds
        and a core without release all keep the plain unblock, which core refuses on a pending spend decision.

        A refused release (provider safety, containment) leaves the ask in place with the reason as a comment.
        ``fence`` is checked here and again by core inside the transaction that records the release decision.
        The receipt is a ``user_steps_answer`` event on the bound card, written with the choice comment after
        the release lands; a retry finds it and returns it. A retry after a crash between the release and
        that write finds the card already released by this operation's note (``released_by``, read from
        core's ``stop_released`` event) on an ask that carried the offer for that card, and records the
        receipt then, instead of refusing a click that worked."""
        core_release = getattr(api, "release", None)
        offered = getattr(core_release, "offered", None)
        if offered is None:
            return None
        operation = op["operationId"]
        note = "Desktop Unblock, operation " + operation
        if offered(c, binding["task"], ask) is None:
            done = core_release.released_by(c, binding["task"], note)
            stamp = ask.get("release_target") if isinstance(ask, dict) else None
            if done is None or not isinstance(stamp, dict) or stamp.get("task") != done:
                return None
            out = dict(outcome="released", target=done)
        else:
            if fence(c):
                raise self.core.ActionError("Source card ask changed or transition refused", 409)
            out = core_release.release(
                c,
                binding["task"],
                writer=core_release.human_writer("desktop-operation:" + operation),
                author="user-steps",
                note=note,
                precondition=fence,
            )
        if out["outcome"] == "refused" and out["refusal"] == "ask_changed":
            raise self.core.ActionError("Source card ask changed or transition refused", 409)
        if out["outcome"] == "refused":
            reason = (
                f"Desktop Unblock did not release {out['target']} ({out['refusal']}): "
                f"{out['message']}\nOperation: {operation}"
            )
            if not c.execute(
                "SELECT 1 FROM task_comments WHERE task_id=? AND author='user-steps' AND body=?",
                (binding["task"], reason),
            ).fetchone():  # a redelivery of the same operation says it once
                api.add_comment(c, binding["task"], "user-steps", reason, system=True)
            raise self.core.ActionError(
                f"Release refused ({out['refusal']}): {out['message']}", 409
            )
        receipt = dict(receipt, released=out["target"], outcome=out["outcome"])
        with api.write_txn(c):
            old = self._receipt(c, binding, op)
            if old:
                return old
            api.add_comment(
                c,
                binding["task"],
                "user-steps",
                comment + f"\nReleased {out['target']}: {out['outcome']}",
            )
            c.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)",
                (
                    binding["task"],
                    "user_steps_answer",
                    encoded(dict(user_steps_projection=receipt)),
                    int(time.time()),
                ),
            )
        return self._receipt(c, binding, op)

    def deliver(self, op):
        binding = self._bound(op)
        if json.loads(binding['snapshot']).get('remote'):
            return self.remote.deliver(op, binding)
        api = self._api()
        c = api.connect(self._path(binding["board"]))
        try:
            if json.loads(binding['snapshot']).get('cited_id'):
                return self._deliver_cited(c, binding, op)
            old = self._receipt(c, binding, op)
            if old:
                return old
            request = json.loads(binding["request"])
            selected = next(
                (
                    i
                    for i, ch in enumerate(request["choices"])
                    if ch["id"] == op["decision"]["choiceId"]
                ),
                None,
            )
            other = op['decision']['choiceId'] == 'answer-text'
            if other and (op.get('provenance') != dict(type='kanban_other', version=1)
                          or op['intent'].get('executionAuthorized') is not False
                          or op['intent'].get('permissionGrants') != []):
                raise self.core.ActionError('Bound informational answer required', 409)
            if selected is None and not other:
                raise self.core.ActionError("Unknown card choice", 409)
            hr = json.loads(binding["snapshot"])["ask"]
            options = hr.get("choices") or [
                dict(label="Unblock", value="unblock"),
                dict(label="Archive", value="archive"),
            ]
            choice = (dict(label='Other', value=op['intent']['revisionText'])
                      if other else options[selected])
            comment = (
                "Choice recorded in Desktop: "
                + encoded(choice)
                + "\nOperation: "
                + op["operationId"]
            )
            receipt = dict(
                destination="kanban_ask",
                board=binding["board"],
                task=binding["task"],
                operationId=op["operationId"],
                value=choice["value"],
                executionStarted=False,
                fingerprint=hashlib.sha256(
                    encoded([op["intent"], op["decision"]]).encode()
                ).hexdigest(),
            )

            def fence(conn):
                if self._receipt(conn, binding, op):
                    return "already recorded"
                current = self._snapshot(
                    conn, binding["task"], op["owner"], presentation=False
                )
                return (
                    None
                    if current is not None and encoded(current) == binding["snapshot"]
                    else "changed ask"
                )

            archive = not other and not hr.get("choices") and choice["value"] == "archive"
            if not other and not hr.get("choices") and choice["value"] == "unblock":
                released = self._release(api, c, binding, op, comment, receipt, fence, hr)
                if released is not None:
                    return released
            # Core owns the transaction (and post-commit archive cleanup). Its event
            # is the durable receipt, committed together with the choice comment.
            (api.archive_task if archive else api.unblock_task)(
                c,
                binding["task"],
                precondition=fence,
                comment=("user-steps", comment),
                event_extra={"user_steps_projection": receipt},
            )
            recorded = self._receipt(c, binding, op)
            if not recorded:
                raise self.core.ActionError(
                    "Source card ask changed or transition refused", 409
                )
            return recorded
        finally:
            c.close()

    def readback(self, op, receipt):
        binding = self._bound(op)
        if json.loads(binding['snapshot']).get('remote'):
            return self.remote.deliver(op, binding) == receipt
        c = self.core._ro_connect(self._path(binding["board"]))
        try:
            return self._receipt(c, binding, op) == receipt
        finally:
            c.close()


class Presentation:
    """Captured-store presentation only: no board discovery, runtime or writer initialization."""

    def __init__(self, core, store):
        if not store.read_only:
            raise core.ActionError('Presentation requires a read-only action store', 503)
        self.core, self.store = core, store

    views = Mirror.views
    present = Mirror.present
    _present = Mirror._present


class Delivery:
    """Route only server-bound projections; ordinary requests retain their receiver."""

    # The wrapper can route to Mirror, whose terminal refusal must share exclusive
    # custody with dispatch and retirement. Serialize ordinary delivery too rather
    # than choosing weaker custody before the operation's route is known.
    exclusive_dispatch = True

    def __init__(self, mirror, ordinary):
        self.mirror, self.ordinary = mirror, ordinary

    def _receiver(self, op):
        return (
            self.mirror
            if self.mirror.binding(op["owner"], op["requestId"])
            else self.ordinary
        )

    def deliver(self, op):
        return self._receiver(op).deliver(op)

    def readback(self, op, receipt):
        return self._receiver(op).readback(op, receipt)
