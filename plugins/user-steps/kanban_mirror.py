"""Server-owned projection of blocked card asks into the canonical decision queue.

A source reference is provenance, never write authority. Only an immutable binding
written by this producer can authorize the receiver, and the receiver rechecks the
exact blocked ask and block generation under the board write transaction.
"""

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Mirror:
    def __init__(self, core, store, *, boards=None, backend=None, session_exists=None):
        self.core, self.store, self.backend = core, store, backend
        board_source = core.kanban_boards if boards is None else boards
        self.boards = dict(board_source() if callable(board_source) else board_source)
        self.session_exists = session_exists or self._user_session
        with store._tx() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS kanban_projections ("
                "namespace TEXT NOT NULL, request_id TEXT NOT NULL, session TEXT NOT NULL, "
                "board TEXT NOT NULL, task TEXT NOT NULL, snapshot TEXT NOT NULL, request TEXT NOT NULL, "
                "PRIMARY KEY(namespace,request_id))"
            )

    def _user_session(self, sid):
        row = self.core._row(self.core._state_db(), sid)
        return bool(row and row[3] in ("desktop", "cli", "tui", "web"))

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

    def _snapshot(self, c, task, owner):
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
            sid = next((r[0] for r in subs if self.session_exists(r[0])), None)
        if sid != owner["storedSessionId"]:
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

    def _read_snapshot(self, board, task, owner):
        c = self.core._ro_connect(self._path(board))
        c.row_factory = sqlite3.Row
        try:
            return self._snapshot(c, task, owner)
        finally:
            c.close()

    def _request(self, owner, board, snap):
        # UUID5 identity includes the exact ask, lifecycle and owner, not a fuzzy title.
        identity = encoded([owner, board, snap])
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

    def reconcile(self, owner):
        self.core.validate_owner(owner)
        for board, path in self.boards.items():
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
                request = self._request(owner, board, snap)
                with self.store._tx() as c:
                    c.execute(
                        "INSERT OR IGNORE INTO kanban_projections VALUES (?,?,?,?,?,?,?)",
                        (
                            self.store._ns(owner),
                            request["requestId"],
                            owner["storedSessionId"],
                            board,
                            snap["task"],
                            encoded(snap),
                            encoded(request),
                        ),
                    )
                try:
                    self.store.get(owner, request["requestId"])
                except self.core.ActionError as exc:
                    if exc.status != 404:
                        raise
                    try:
                        self.store.create(owner, request)
                    except self.core.ActionError as race:
                        if race.status != 409:
                            raise
                        self.store.get(
                            owner, request["requestId"]
                        )  # concurrent identical producer
        with self.store._read() as c:
            bindings = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM kanban_projections WHERE namespace=? AND session=?",
                    (self.store._ns(owner), owner["storedSessionId"]),
                )
            ]
        for binding in bindings:
            if binding["board"] not in self.boards:
                continue  # a temporarily absent/archived board must not break other tiles
            try:
                current = self._read_snapshot(binding["board"], binding["task"], owner)
                request = self.store.get(owner, binding["request_id"])
            except (sqlite3.Error, OSError):
                continue
            if encoded(current) == binding["snapshot"] or request["state"] not in (
                "open",
                "needs_context",
            ):
                continue
            if request.get("resolution"):
                oid = request["resolution"]["operationId"]
                op = self.store.operation(owner, oid)["operation"]
                if op["dispatchState"] in ("delivered", "cancelled"):
                    continue
                # A board commit may precede its outbox acknowledgment. Preserve that
                # receipt for retry; cancel only a provably stale, undelivered decision.
                c = self.core._ro_connect(self._path(binding["board"]))
                try:
                    delivered = self._receipt(c, binding, op)
                finally:
                    c.close()
                if not delivered:
                    self.store.retire_operation(
                        owner,
                        oid,
                        by="system",
                        reason="Source card ask changed before delivery",
                        evidence="Exact blocked-card snapshot no longer matches",
                    )
                continue
            self.store.withdraw(
                owner,
                request["requestId"],
                by="system",
                reason="Source card left this block or its ask changed",
                expected_revision=request["revision"],
            )

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
                current = self._snapshot(conn, binding["task"], op["owner"])
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
