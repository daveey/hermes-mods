"""Bounded, read-only local owner evidence.

Local scheduled ownership needs a native future wake, a fresh dispatcher receipt
and a scoped result consumer. Remote/process and dependency owners remain UNKNOWN.
Runtime liveness is a point-in-time observation, not a future delivery guarantee.
"""
from pathlib import Path

def _ro_connect(path):
    import sqlite3
    uri = Path(path).resolve().as_uri()
    try:
        conn = sqlite3.connect(uri + '?mode=ro', uri=True, timeout=1)
    except sqlite3.OperationalError:
        # WAL sidecars may have been removed by the last writer. Never create a DB.
        conn = sqlite3.connect(uri + '?mode=rw', uri=True, timeout=1)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
    except Exception:
        conn.close()
        raise
    return conn


def authoritative_owner(v, evidence, session_id):
    """Conservative local running-worker proof, never cached model/tool assertions.

    Supported route: exact local hostname, current profile, explicit board, native
    task ID, TUI/Desktop origin subscription. Exact request/body equality avoids
    turning a model's claimed goal match into authority. Scheduled routes need native dispatcher evidence; dependency routes remain UNKNOWN. cross_task.read is deliberately NOT invoked:
    its session-mentions contract also adopts/shepherds remote cards and its
    relay is neither a bounded read-only probe nor a profile-qualified proof.
    """
    import re
    import socket
    import sqlite3
    import time
    from contextlib import closing
    from hermes_constants import get_hermes_home
    from hermes_cli import kanban_db as kb
    from hermes_cli.profiles import get_active_profile_name
    from hermes_cli.kanban_host_identity import is_local_claim
    from hermes_cli.kanban_db_dispatch import _worker_alive

    ref = v.get('continuation_ref')
    if (not isinstance(ref, dict) or set(ref) != {'gateway', 'profile', 'board', 'task_id'}
            or any(not isinstance(x, str) or not x for x in ref.values())
            or not re.fullmatch(r't_[0-9a-f]{8}', ref['task_id'])
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', ref['board'])):
        raise ValueError('owner_readback_unavailable')
    if ref['gateway'] != socket.gethostname():
        raise ValueError('owner_remote_readback_unavailable')
    profile = get_active_profile_name() or 'default'
    if ref['profile'] != profile or not session_id:
        raise ValueError('owner_profile_or_origin_mismatch')
    users = [row.get('content') for row in evidence.values() if row['role'] == 'user']
    request = users[-1] if users else None
    if not isinstance(request, str) or not request or not v['actionable'] or not v['unfinished']:
        raise ValueError('owner_request_unavailable')

    try:
        # Never SessionDB()/kb.connect(): those may create/migrate/write stores.
        with closing(_ro_connect(Path(get_hermes_home()) / 'state.db')) as state:
            state.execute('BEGIN')
            origin = state.execute('SELECT profile_name FROM sessions WHERE id=?', (session_id,)).fetchone()
            current = state.execute("SELECT content, observed, display_kind, _compressed_summary, display_metadata FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
            if not origin or origin['profile_name'] != profile or not _current_user_matches(current, request):
                raise ValueError('owner_current_request_mismatch')
        # Resolve an explicit board, but refuse environment-pinned foreign paths.
        expected = (kb.kanban_home() / 'kanban.db' if ref['board'] == 'default'
                    else kb.board_dir(ref['board']) / 'kanban.db')
        path = kb.kanban_db_path(board=ref['board'])
        if path.resolve() != expected.resolve():
            raise ValueError('owner_board_pin_mismatch')
        if kb.read_board_metadata(ref['board']).get('archived'):
            raise ValueError('owner_board_archived')
        with closing(_ro_connect(path)) as conn:
            conn.execute('BEGIN')  # consistent card/run/subscription snapshot
            task = conn.execute('SELECT * FROM tasks WHERE id=?', (ref['task_id'],)).fetchone()
            if (not task or task['status'] not in ('running', 'scheduled', 'todo') or task['assignee'] != profile
                    or task['session_id'] != session_id or task['body'] != request
                    or task['title'] != v['goal'] or task['completed_at'] is not None):
                raise ValueError('owner_task_scope_or_state_mismatch')
            if task['status'] != 'running':
                _waiting_continuation(conn, task, path, profile)
            else:
                run = conn.execute('SELECT * FROM task_runs WHERE id=? AND task_id=?',
                                   (task['current_run_id'], ref['task_id'])).fetchone()
                if (not run or run['profile'] != profile or run['status'] != 'running'
                        or run['ended_at'] is not None or run['outcome'] is not None
                        or not run['claim_expires'] or run['claim_expires'] <= time.time()
                        or not is_local_claim(run['claim_lock'])
                        or not isinstance(run['worker_started_at'], str)
                        or '|' not in run['worker_started_at']
                        or not _worker_alive(run['worker_pid'], run['worker_started_at'])):
                    raise ValueError('owner_live_continuation_unverified')
            sub = conn.execute("SELECT 1 FROM kanban_notify_subs WHERE task_id=? AND platform='tui' AND chat_id=? AND thread_id='' AND notifier_profile=? AND delivery_mode IN ('notify','notify+wake')", (ref['task_id'], session_id, profile)).fetchone()
            if not sub:
                raise ValueError('owner_result_return_unverified')
            _live_result_consumer(session_id, Path(get_hermes_home()), path)
    except (OSError, sqlite3.Error) as exc:
        raise ValueError('owner_readback_unavailable') from exc



def _waiting_continuation(conn, task, board_path, profile):
    """Compose a native future wake with scoped recent scheduler evidence.

    Dependency graphs still require continuing proof for each unfinished parent.
    The root is resolved by native code, never supplied by the model/tool result.
    Files owned by the runtime UID are trusted state, not a hostile-UID boundary.
    """
    from hermes_cli import kanban_db as kb
    wait = waiting_readback(conn, task)
    if wait['kind'] != 'scheduled':
        raise ValueError('owner_dispatcher_unverified')
    try:
        from gateway.kanban_dispatcher_health import available
    except ImportError as exc:
        raise ValueError('owner_dispatcher_unverified') from exc
    if not available(kb.kanban_home() / 'kanban', board_path, profile, 'ready'):
        raise ValueError('owner_dispatcher_unverified')


def waiting_readback(conn, task):
    """Read a bounded native wait snapshot, NOT an ownership certificate.

    Caller supplies a read-only transaction. Never run wake_check commands,
    recompute readiness, or infer an ongoing dispatcher from a parked row.
    Native link_tasks gates ready -> todo without setting block_kind; that
    historical block label is not the dependency contract.
    """
    import math
    import time
    from hermes_cli.kanban_continuation import pending_wake
    if task['status'] == 'scheduled':
        wake = pending_wake(conn, task['id'])
        when = wake.get('wake_at') if wake else None
        if (type(when) not in (int, float) or not math.isfinite(when)
                or when <= time.time()):
            raise ValueError('owner_scheduled_wake_unverified')
        return {'kind': 'scheduled', 'wake_at': when}
    if task['status'] != 'todo':
        raise ValueError('owner_wait_state_unverified')
    parents = _dependency_readback(conn, task['id'])
    if not parents or not any(status not in ('done', 'archived') for _, status in parents):
        raise ValueError('owner_dependency_unverified')
    return {'kind': 'dependency', 'parents': parents}


def _dependency_readback(conn, task_id):
    """Bound both traversal and rows; reject dangling links and cycles.

    Inspect the whole bounded ancestor graph, not just a self-link at the root.
    A shared ancestor in a DAG is valid; only a node on the active path cycles.
    No parent state, even running, asserts worker or dispatcher liveness.
    """
    visiting, visited, direct = set(), set(), ()
    stack = [(task_id, False)]
    edges = 0
    while stack:
        node, leaving = stack.pop()
        if leaving:
            visiting.remove(node)
            visited.add(node)
            continue
        if node in visiting:
            raise ValueError('owner_dependency_unverified')
        if node in visited:
            continue
        if len(visited) + len(visiting) >= 64:
            raise ValueError('owner_dependency_unverified')
        rows = conn.execute(
            'SELECT p.id, p.status FROM task_links l LEFT JOIN tasks p '
            'ON p.id=l.parent_id WHERE l.child_id=? ORDER BY l.parent_id LIMIT 65',
            (node,)).fetchall()
        edges += len(rows)
        if edges > 64 or any(p['id'] is None for p in rows):
            raise ValueError('owner_dependency_unverified')
        if node == task_id:
            direct = tuple((p['id'], p['status']) for p in rows)
        visiting.add(node)
        stack.append((node, True))
        stack.extend((p['id'], False) for p in rows)
    return direct


def _live_result_consumer(session_id, home, board_path):
    """Read native successful-poll receipts; never inspect foreign thread frames.

    Older cores or unloaded/cross-process consumers have no authoritative local
    receipt. A live thread whose database reads fail does not certify return.
    """
    from tui_gateway import kanban_consumer_health
    from hermes_cli import kanban_db as kb
    boards = kb.list_boards(include_archived=False)
    if not any(Path(meta.get('db_path') or kb.kanban_db_path(meta['slug'])).resolve()
               == board_path.resolve() for meta in boards):
        raise ValueError('owner_result_consumer_unverified')
    if not kanban_consumer_health.available(home, session_id, board_path):
        raise ValueError('owner_result_consumer_unverified')


def _current_user_matches(row, request):
    """Matching text does not authenticate a synthetic or provenance-lost row.

    Do not skip a newer synthetic row to find a convenient older request.
    The wire sidecar is never user authority.
    """
    import json
    if (row is None or row['content'] != request or row['observed']
            or row['display_kind'] is not None or row['_compressed_summary']):
        return False
    raw = row['display_metadata']
    if raw is None:
        return True
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(metadata, dict) and not metadata.get('user_content_sanitized')
