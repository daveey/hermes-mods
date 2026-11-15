"""Opt-in headless task reads from the already-authorized Board replica.

No network credentials, owner writes, board imports, or live-state claims.
"""
from __future__ import annotations

import re
import time


def enabled():
    try:
        from hermes_cli.config import load_config
    except ImportError:  # standalone plugin tests / older non-core hosts
        return False
    return load_config().get("session_mentions", {}).get("task_transport") == "board_replica"


def target_parts(args):
    target = str(args.get("target") or "").strip()
    board, gateway = args.get("board"), args.get("gateway")
    if target.startswith("@task:"):
        match = re.fullmatch(r"@task:([a-zA-Z0-9_-]+)(?:@([a-zA-Z0-9_.-]+))?/(t_[a-zA-Z0-9]+)", target)
        if not match:
            raise ValueError("Invalid task reference")
        b, g, target = match.groups()
        if board and board != b or gateway and g and gateway != g:
            raise ValueError("Conflicting board/gateway selectors")
        board, gateway = b, g or gateway
    elif ":" in target:
        g, target = target.split(":", 1)
        if gateway and gateway != g:
            raise ValueError("Conflicting gateway selectors")
        gateway = g
    if target and not re.fullmatch(r"t_[a-zA-Z0-9]+", target):
        raise ValueError("Invalid task id")
    return board, gateway, target


def handle(args):
    from tools import kanban_tools as kt
    from hermes_cli.kanban_foreign_card import open_replica, replica_path
    from hermes_cli.kanban_db_connect import _main_db_file
    from hermes_cli.kanban_db_sync import board_slug_for_path

    if args.get("action") == "comment":
        return {"ok": False, "rejection": "read_only_transport",
                "error": "Board replica transport is read-only; comment through the task owner."}
    try:
        if args.get("action") == "list":
            kt._require_orchestrator_tool("kanban_list")
        board, gateway, tid = target_parts(args)
        limit = max(1, min(100, int(args.get("limit") or 10)))
        with kt._granted_board(board, tid) as (kb, live, _):
            db_path = _main_db_file(live)
            if not db_path:
                raise ValueError("Selected board has no database path")
            selected = board_slug_for_path(db_path)
            path = replica_path(db_path)
            with open_replica(db_path) as conn:
                if conn is None:
                    raise ValueError("No authorized Board replica for the selected board")
                meta = {"source": "replica", "board": selected, "live": False,
                        "read_at": time.time(),
                        "snapshot_file_mtime": path.stat().st_mtime if path else None,
                        "freshness": "Replica may lag its owner; file mtime is not owner liveness."}
                if args.get("action") == "list":
                    query = str(args.get("query") or "").casefold()
                    statuses = set(str(args.get("status") or "").split(",")) - {""}
                    rows = conn.execute("SELECT id,title,status,created_on FROM tasks ORDER BY created_at DESC")
                    tasks = []
                    for row in rows:
                        row = dict(row)
                        if gateway and row["created_on"] != gateway:
                            continue
                        if statuses and row["status"] not in statuses:
                            continue
                        if query not in (row["id"] + " " + row["title"]).casefold():
                            continue
                        row.update(source="replica", owner=row.pop("created_on"))
                        tasks.append(row)
                        if len(tasks) >= limit:
                            break
                    return {"ok": True, **meta, "tasks": tasks,
                            "scope": "selected board only; not a search of all gateways/boards"}
                task = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
                if task is None:
                    raise ValueError("Task not found in selected board replica; specify its board explicitly")
                task = dict(task)
                owner = task.get("created_on")
                if gateway and owner != gateway:
                    raise ValueError("Requested gateway does not match replica owner; use its exact owner id")
                # Reuse public tool projections; do not export raw task authority fields.
                view = {key: task.get(key) for key in kt._TASK_FIELDS}
                view.update(source="replica", owner=owner)
                comments, events, runs = kb.list_comments(conn, tid), kb.list_events(conn, tid), kb.list_runs(conn, tid)
                # Resolve custody from the latest completion before truncating history.
                # Preserve records verbatim, including unknown machines and warnings.
                completion = next((e for e in reversed(events) if e.kind == "completed"), None)
                receipt = (completion.payload or {}).get("artifact_receipt") if completion else None
                return {"ok": True, **meta, "task": view,
                        "artifact_receipt": receipt if isinstance(receipt, list) else [],
                        "history_limit": limit,
                        "history_truncated": {"comments": len(comments) > limit, "events": len(events) > limit, "runs": len(runs) > limit},
                        "parents": kb.parent_ids(conn, tid), "children": kb.child_ids(conn, tid),
                        "comments": [kt._fields(c, kt._COMMENT_FIELDS) for c in comments[-limit:]],
                        "events": [kt._fields(e, kt._EVENT_FIELDS) for e in events[-limit:]],
                        "runs": [kt._fields(r, kt._RUN_FIELDS) for r in runs[-limit:]]}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "rejection": getattr(exc, "code", "replica_read_failed")}
