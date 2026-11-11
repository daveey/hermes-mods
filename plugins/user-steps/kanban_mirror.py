"""Server-owned projection of blocked card asks into the canonical decision queue.

A source reference is provenance, never write authority. Only an immutable binding
written by this producer can authorize the receiver, and the receiver rechecks the
exact blocked ask and block generation under the board write transaction.
"""

import hashlib
import inspect
import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path


MISMATCH = "Request no longer matches its frozen server binding"


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
        with store._tx() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS kanban_projections ("
                "namespace TEXT NOT NULL, request_id TEXT NOT NULL, session TEXT NOT NULL, "
                "board TEXT NOT NULL, task TEXT NOT NULL, snapshot TEXT NOT NULL, request TEXT NOT NULL, "
                "PRIMARY KEY(namespace,request_id))"
            )

    def _session_row(self, sid):
        # Read failure is not evidence that a card lost its owner.
        c = self.core._ro_connect(self.core._state_db())
        try:
            return c.execute(
                "SELECT id,parent_session_id,end_reason,source FROM sessions WHERE id=?",
                (sid,),
            ).fetchone()
        finally:
            c.close()

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

            c = self.core._ro_connect(self.core._state_db())
            c.row_factory = sqlite3.Row
            try:
                chain = _walk_compression_chain(
                    sid, lambda s: c.execute(_CHAIN_STEP_SQL, (s,)).fetchone()
                )
                return chain[-1]
            finally:
                c.close()
        except ImportError as exc:
            raise OSError("Core continuation resolver unavailable") from exc

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
        c = self.core._ro_connect(self._path(board))
        c.row_factory = sqlite3.Row
        try:
            return self._snapshot(c, task, owner, presentation=presentation)
        finally:
            c.close()

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
        if not self._fenced_core():
            logging.getLogger("user-steps").warning(
                "Card projection paused: core lacks fenced unblock/archive (deploy core first)"
            )
            boards = {}
        else:
            boards = self.boards
        for board, path in boards.items():
            try:
                c = self.core._ro_connect(path)
                c.row_factory = sqlite3.Row
                try:
                    rows = c.execute(
                        "SELECT id FROM tasks WHERE status='blocked' AND human_request IS NOT NULL"
                    ).fetchall()
                    snapshots = [self._snapshot(c, r[0], owner) for r in rows]
                finally:
                    c.close()
            except (sqlite3.Error, OSError):
                continue  # unavailable is NOT evidence to withdraw
            for snap in filter(None, snapshots):
                try:
                    if self._relocate(owner, board, snap):
                        request = self._current_request(owner, board, snap)
                        if request is not None:
                            self._publish(owner, board, snap, request)
                except self.core.ActionError as exc:
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
                logging.getLogger("user-steps").warning(
                    "Older card binding reconciliation deferred request=%s: %s",
                    binding["request_id"],
                    exc,
                )

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
            c = self.core._ro_connect(self._path(binding["board"]))
            try:
                delivered = self._receipt(c, binding, op)
            finally:
                c.close()
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
            if selected is None:
                raise self.core.ActionError("Unknown card choice", 409)
            hr = json.loads(binding["snapshot"])["ask"]
            options = hr.get("choices") or [
                dict(label="Unblock", value="unblock"),
                dict(label="Archive", value="archive"),
            ]
            choice = options[selected]
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

            archive = not hr.get("choices") and choice["value"] == "archive"
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
