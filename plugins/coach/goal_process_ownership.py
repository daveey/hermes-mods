"""Read-only composition of native local process ownership and return evidence.

Only exact same-profile/session canonical message identities are supported.
No process-management operations, retained results, or Kanban receipts here.
"""
from contextlib import closing
from pathlib import Path
import json
import re
import socket
import sqlite3


def _current_request(home, session_id, profile, request, snapshot, ro_connect, current_user_matches):
    with closing(ro_connect(home / "state.db")) as state:
        state.execute("BEGIN")
        origin = state.execute("SELECT profile_name FROM sessions WHERE id=?", (session_id,)).fetchone()
        row = state.execute(
            "SELECT content, message_uid, observed, display_kind, _compressed_summary, display_metadata "
            "FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (session_id,)).fetchone()
        if (not origin or origin["profile_name"] != profile
                or not current_user_matches(row, request) or row["message_uid"] != snapshot.message_uid):
            raise ValueError("owner_process_current_request_mismatch")
        metadata = json.loads(row["display_metadata"] or "{}")
        if metadata.get("_queued_prompt"):
            raise ValueError("owner_process_current_request_mismatch")


def _consumer(snapshot, *, expected=None):
    if snapshot.session_key == snapshot.session_id:
        from tui_gateway.process_consumer_health import available, receipt_version
        version = receipt_version(snapshot.home, snapshot.session_id, snapshot.generation)
        if version is not None and available(snapshot.home, snapshot.session_id, snapshot.generation,
                                             expected=expected):
            return version
    elif snapshot.session_key.startswith("agent:"):
        from gateway.process_consumer_health import receipt_version
        return receipt_version(snapshot, expected=expected)
    return None


def authoritative_process(v, evidence, session_id, ro_connect, current_user_matches):
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    ref = v.get("continuation_ref")
    if (not isinstance(ref, dict) or set(ref) != {"gateway", "profile", "process_id"}
            or any(not isinstance(x, str) or not x for x in ref.values())
            or not re.fullmatch(r"proc_[0-9a-f]{12}", ref["process_id"])):
        raise ValueError("owner_readback_unavailable")
    profile = get_active_profile_name() or "default"
    if ref["gateway"] != socket.gethostname() or ref["profile"] != profile or not session_id:
        raise ValueError("owner_process_route_mismatch")
    try:
        from tools.process_registry import process_registry
        readback = getattr(process_registry, "ownership_snapshot", None)
        snapshot = readback(ref["process_id"]) if callable(readback) else None
        if snapshot is None:
            raise ValueError("owner_readback_unavailable")
        home = Path(get_hermes_home()).resolve()
        users = [r.get("content") for r in evidence.values() if r["role"] == "user"]
        request = users[-1] if users else None
        if (not v["actionable"] or not v["unfinished"] or not isinstance(request, str)
                or not request or snapshot.home != str(home) or snapshot.session_id != session_id
                or snapshot.content != request):
            raise ValueError("owner_process_scope_mismatch")
        _current_request(home, session_id, profile, request, snapshot, ro_connect, current_user_matches)
        version = _consumer(snapshot)
        if version is None:
            raise ValueError("owner_process_consumer_unverified")
        # Detect an exit/handoff during the independent scope/consumer reads.
        if readback(ref["process_id"]) != snapshot:
            raise ValueError("owner_process_changed")
        _current_request(home, session_id, profile, request, snapshot, ro_connect, current_user_matches)
        if _consumer(snapshot, expected=version) != version:
            raise ValueError("owner_process_consumer_changed")
    except (ImportError, OSError, sqlite3.Error, AttributeError, TypeError) as exc:
        raise ValueError("owner_readback_unavailable") from exc
