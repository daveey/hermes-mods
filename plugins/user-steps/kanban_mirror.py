"""Server-owned projection of blocked card asks into the canonical decision queue.

A source reference is provenance, never write authority. Only an immutable binding
written by this producer can authorize the receiver, and the receiver rechecks the
exact blocked ask and block generation under the board write transaction.
"""

import hashlib
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
_EVENT_KINDS = ("created", "blocked", "unblocked", "archived", "completed")
_MEMO: dict = {}  # owner key -> (inputs key, monotonic time of the full pass that produced it)
_MEMO_LOCK = threading.Lock()
_MEMO_MAX = 256
_POOL: dict = {}
_POOL_LOCK = threading.Lock()
_POOL_MAX = 64
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
    __slots__ = ("conn", "ident", "gen", "lock", "owner", "memo")

    def __init__(self):
        self.conn: sqlite3.Connection | None = None
        self.ident = None
        self.gen = 0  # a fresh connection restarts PRAGMA data_version: keys carry the generation
        self.lock = threading.Lock()
        self.owner = None
        self.memo: dict = {}  # per-connection derived values, valid for one (gen, data_version)


def _evict():
    for key in list(_POOL):
        stale = _POOL[key]
        if stale.lock.acquire(blocking=False):
            try:
                del _POOL[key]
                if stale.conn is not None:
                    stale.conn.close()
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
            entry = _POOL[str(path)] = _Kept()
    if entry.owner == threading.get_ident():
        c = _open_ro(path)
        try:
            yield c
        finally:
            c.close()
        return
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
        board_source = core.kanban_boards if boards is None else boards
        self.boards = dict(board_source() if callable(board_source) else board_source)
        self.session_exists = session_exists or self._user_session
        self.session_present = session_present or session_exists or self._stored_session
        self.tip = tip or self._tip
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

        return SimpleNamespace(
            connect=connect,
            write_txn=kanban_db.write_txn,
            add_comment=kanban_db.add_comment,
            unblock_task=kanban_db.unblock_task,
            archive_task=kanban_db.archive_task,
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
        if row.get("status") != "blocked" or str(
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
            "('created','blocked','unblocked','archived','completed')",
            (task,),
        ).fetchone()[0]
        return dict(
            task=task,
            session=sid,
            generation=generation,
            ask=hr,
            title=row.get("title") or hr["action"],
        )

    def _read_snapshot(self, board, task, owner, *, presentation=True):
        with _kept(self._path(board)) as c:
            return self._snapshot(c, task, owner, presentation=presentation)

    def _request(self, owner, board, snap, incarnation=0):
        # UUID5 identity includes the exact ask, lifecycle and owner, not a fuzzy title.
        # A later incarnation re-projects the same ask after an earlier projection was
        # withdrawn/retired; incarnation 0 keeps the original identity byte for byte.
        identity = encoded(
            [owner, board, snap] + ([incarnation] if incarnation else [])
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
            gatewayId=owner["gatewayId"],
            profile=owner["profile"],
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

    def present(self, request):
        """Display-only metadata; never rewrite a frozen request or mint a new tile."""
        binding = self.binding(request['owner'], request['requestId'])
        if not binding:
            return request
        frozen = self.store.get(request['owner'], request['requestId'])
        frozen.pop('resolution', None)
        frozen['state'] = json.loads(binding['request'])['state']
        if encoded(frozen) != binding['request']:
            return request
        snap = json.loads(binding['snapshot'])
        hr = snap['ask']
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
        return dict(request, presentation=dict(kind='kanban-question', question=question,
                    details=details, recommendedChoiceId=recommended, other=True,
                    sourceRef=dict(gatewayId=request['owner']['gatewayId'],
                                   profile=request['owner']['profile'], type='kanban',
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

    def _current_request(self, owner, board, snap):
        """The request to publish for this exact ask, or None when none may be shown.

        Identity is deterministic, so a withdrawn (or mismatch-retired) projection can
        never be reopened in place: returning to an owner mints the next incarnation.
        A recorded answer or an unproven mismatch fails closed (None).
        """
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
                if not op or (op.get("retired") or {}).get("reason") != MISMATCH:
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

    def reconcile(self, owner):
        self.core.validate_owner(owner)
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
            with _kept(self.core._state_db()) as c:
                # Unrelated worker sessions cannot affect an ask. Keep the user-session
                # shape (including new user owners) and exact rows for every creating
                # session/local subscriber, even custom-source legacy subscribers.
                parts.append(tuple(c.execute(
                    "SELECT MAX(rowid), COUNT(*) FROM sessions "
                    "WHERE source IN ('desktop','cli','tui','web')").fetchone()))
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
        latest = ("(SELECT MAX(e.id) FROM task_events e WHERE e.task_id=t.id AND e.kind IN "
                  f"({','.join('?' * len(_EVENT_KINDS))}))")
        rows = [tuple(r) for r in c.execute(
            f"SELECT {cols}, {latest} FROM tasks t WHERE t.status='blocked' AND t.human_request IS NOT NULL "
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
        session_ids.update(r[1] for r in (subs or []) if r[1])
        return _digest([rows, subs]), tuple(sorted(session_ids))

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
                        "SELECT id FROM tasks WHERE status='blocked' AND human_request IS NOT NULL"
                    ).fetchall()
                    snapshots = [self._snapshot(c, r[0], owner) for r in rows]
            except (sqlite3.Error, OSError):
                clean = False
                continue  # unavailable is NOT evidence to withdraw
            for snap in filter(None, snapshots):
                try:
                    if self._relocate(owner, board, snap):
                        request = self._current_request(owner, board, snap)
                        if request is not None:
                            self._publish(owner, board, snap, request)
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
        if binding["board"] not in self.boards:
            return  # temporarily absent board is not departure evidence
        current = self._read_snapshot(
            binding["board"], binding["task"], owner, presentation=False
        )
        request = self.store.get(owner, binding["request_id"])
        tip = self.tip(current["session"]) if current is not None else None
        if request["state"] not in ("open", "needs_context"):
            return
        public = {k: v for k, v in request.items() if k != "resolution"}
        public["state"] = json.loads(binding["request"]).get("state")
        mismatch = encoded(public) != binding["request"]
        if encoded(current) == binding["snapshot"] and not mismatch:
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
        except self.core.ActionError as exc:
            if exc.status != 409:
                raise
            current = self.store.get(owner, request["requestId"])
            if current["state"] in ("open", "needs_context") and not current.get(
                "resolution"
            ):
                raise  # only a proven concurrent terminal/decision transition is benign

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
            "SELECT payload FROM task_events WHERE task_id=? AND kind IN ('unblocked','archived') ORDER BY id DESC",
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

    def deliver(self, op):
        binding = self._bound(op)
        api = self._api()
        c = api.connect(self._path(binding["board"]))
        try:
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
        c = self.core._ro_connect(self._path(binding["board"]))
        try:
            return self._receipt(c, binding, op) == receipt
        finally:
            c.close()


class Delivery:
    """Route only server-bound projections; ordinary requests retain their receiver."""

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
