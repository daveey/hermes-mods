"""Bounded, profile-qualified user scope recovery. No runtime writes."""
import copy
import json
import math
from pathlib import Path
import importlib.util
_spec = importlib.util.spec_from_file_location('_coach_scope_images', Path(__file__).with_name('goal_images.py'))
_images = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_images)


def is_evidence_row(row):
    """Persistence identity is not synthetic content; unknown markers fail closed."""
    from agent.message_metadata import PERSISTENCE_ONLY_MESSAGE_FIELDS
    from agent.steering_provenance import steering_content
    return (isinstance(row, dict)
            and not any(k.startswith('_') and k not in PERSISTENCE_ONLY_MESSAGE_FIELDS
                        and k != '_db_persisted' and row[k] for k in row)
            and not _queued_receipt(row)
            and not row.get('observed')
            and not (row.get('role') == 'user' and row.get('display_kind')
                     and steering_content(row) is None))

def _queued_receipt(row):
    metadata = row.get('display_metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return isinstance(metadata, dict) and metadata.get('_queued_prompt') is True


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

def _user_row(row):
    from agent.steering_provenance import steering_content
    text = steering_content(row)
    if text is not None:
        return {'role': 'user', 'content': text, 'content_source': 'native_content',
                'message_uid': row['message_uid'],
                'steering_source': {k: copy.deepcopy(row[k]) for k in
                    ('role', 'content', 'display_kind', 'message_uid', '_native_steer')}}
    metadata = row.get('display_metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if row.get('_user_content_sanitized') or metadata.get('user_content_sanitized'):
        raise ValueError('ambiguous_memory_provenance')
    if isinstance(row.get('content'), list):
        # Historical multimodal injection shares content, without a native split.
        raise ValueError('scope_unsupported_content')
    result = {'role': 'user', 'content': copy.deepcopy(row.get('content'))}
    # A native sidecar establishes a content/wire split. NULL cannot authenticate
    # legacy textual memory wrappers. The sidecar itself is never user authority.
    if isinstance(row.get('api_content'), str) and row['api_content']:
        result['content_source'] = 'native_content'
    return result


def require_known_steering(session_id, live_rows, *, historical=False, captured_rows=None):
    """Missing live attestation is UNKNOWN, never absence of a restriction.

    The DB is used only as a loss detector, not to manufacture human authority.
    Archived rows count too. A later/concurrent durable steer conservatively
    makes assessment unavailable; its text is never imported as permission.
    """
    from agent.steering_provenance import steering_content

    known = set()
    # Only the trusted capture projection keeps this source. Raw history labels
    # are discarded by _user_row; attestations are revalidated, never restored.
    for row in captured_rows or []:
        source = row.get('steering_source')
        if source is None:
            continue
        text = steering_content(source)
        if (text is None or text != row.get('content')
                or source.get('message_uid') != row.get('message_uid')):
            raise ValueError('scope_unknown_steering')
        known.add((source['message_uid'], source['content']))
    for row in live_rows or []:
        if row.get('role') != 'user' or row.get('display_kind') != 'steer' or _queued_receipt(row):
            continue
        if not is_evidence_row(row) or steering_content(row) is None:
            if historical or row.get('_native_steer'):
                raise ValueError('scope_unknown_steering')
            continue  # an unpersisted display-only spoof is not human input
        known.add((row.get('message_uid'), row.get('content')))
    _check_saved_steering(session_id, known)


def _check_saved_steering(session_id, known):
    from contextlib import closing
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    from hermes_state_messages import SessionMessagesMixin

    path = Path(get_hermes_home()) / 'state.db'
    if not session_id or not path.exists():
        return
    with closing(_ro_connect(path)) as conn:
        conn.execute('BEGIN')
        ticks = [0]
        def budget():
            ticks[0] += 1
            return int(ticks[0] > 1000)
        conn.set_progress_handler(budget, 1000)
        seen = set()
        cutoff = float('inf')
        for _ in range(32):
            current = conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            if current is None:
                if seen:
                    raise ValueError('scope_missing_ancestor')
                return
            current = dict(current)
            if session_id in seen:
                raise ValueError('scope_lineage_cycle')
            seen.add(session_id)
            if current.get('profile_name') != get_active_profile_name():
                raise ValueError('scope_profile_mismatch')
            saved = conn.execute(
                "SELECT message_uid, content, display_metadata FROM messages WHERE session_id=? "
                "AND role='user' AND display_kind='steer' AND timestamp < ? LIMIT 201",
                (session_id, cutoff)).fetchall()
            if len(saved) > 200:
                raise ValueError('context_limit')
            for row in saved:
                if not _queued_receipt(dict(row)) and (row['message_uid'], row['content']) not in known:
                    raise ValueError('scope_unknown_steering')
            if SessionMessagesMixin._is_explicit_fork_child_row(None, current, include_reset=True):
                return
            parent_id = current.get('parent_session_id')
            if not parent_id:
                return
            parent = conn.execute('SELECT end_reason FROM sessions WHERE id=?', (parent_id,)).fetchone()
            if parent is None:
                raise ValueError('scope_missing_ancestor')
            if parent['end_reason'] != 'compression':
                return
            started = current.get('started_at')
            if type(started) not in (int, float) or not math.isfinite(started):
                raise ValueError('scope_missing_cutoff')
            cutoff = min(cutoff, started)
            session_id = parent_id
        raise ValueError('scope_lineage_limit')


def _capture_rows(raw_rows, history, user_message, canonical):
    # Import only native fields, never history's self-declared trust labels.
    native_row = None
    if canonical is not None:
        idx = canonical.get('message_index')
        uid = canonical.get('message_uid')
        if (type(idx) is not int or not 0 <= idx < len(history or [])
                or not any(r is history[idx] for r in raw_rows)
                or not isinstance(uid, str) or not uid
                or history[idx].get('message_uid') != uid
                or canonical.get('content') != user_message):
            raise ValueError('scope_invalid_native_anchor')
        native_row = history[idx]
    # Identity, not equality: repeated requests must not promote an older row.
    return [({'role': 'user', 'content': copy.deepcopy(user_message),
              'content_source': 'native_content', 'message_uid': canonical['message_uid']}
             if r is native_row else _user_row(r)) for r in raw_rows]


def recover_scope(session_id, user_message, history, *, canonical_user_content=None):
    """Recover only user rows, backwards through verified compression edges.

    Never chase a compression tip (it can be a future turn). Ancestors are cut
    at the child's creation time; same-session recovery requires a unique exact
    current request anchor. Missing anchors use caller history, not latest DB
    rows. Ambiguity/overflow is UNKNOWN rather than partial authorization.
    """
    from contextlib import closing
    from hermes_constants import get_hermes_home
    from hermes_state_messages import SessionMessagesMixin
    from hermes_cli.profiles import get_active_profile_name

    require_known_steering(session_id, history, historical=True)
    raw_rows = [r for r in history or [] if is_evidence_row(r) and r.get('role') == 'user']
    rows = _capture_rows(raw_rows, history, user_message, canonical_user_content)
    path = Path(get_hermes_home()) / 'state.db'
    recovered = []
    if path.exists():
        with closing(_ro_connect(path)) as conn:
            conn.execute('BEGIN')
            # Bound SQL work as well as returned data; no migrations or writes.
            ticks = [0]
            def budget():
                ticks[0] += 1
                return int(ticks[0] > 1000)
            conn.set_progress_handler(budget, 1000)
            current = conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            if current:
                current = dict(current)
                if current.get('profile_name') != get_active_profile_name():
                    raise ValueError('scope_profile_mismatch')
                clean = "role='user' AND COALESCE(_compressed_summary,0)=0 AND COALESCE(observed,0)=0 AND display_kind IS NULL"
                # Queue receipts are durable UI placeholders, not executed turns.
                # Exclude only the native boolean marker, not human text or other metadata.
                clean += (" AND CASE WHEN json_valid(display_metadata) THEN "
                          "COALESCE(json_type(display_metadata, '$._queued_prompt'), '') != 'true' ELSE 1 END")
                anchors = conn.execute('SELECT id FROM messages WHERE session_id=? AND ' + clean +
                                       ' AND content=? LIMIT 2', (session_id, json.dumps(user_message) if isinstance(user_message, list) else user_message)).fetchall()
                if len(anchors) > 1:
                    raise ValueError('scope_ambiguous_anchor')
                segments = []
                if anchors:
                    segments.append((session_id, 'id < ?', anchors[0]['id']))
                else:
                    # Core captures before persisting the current request. Its stamped
                    # row is a frozen cutoff, unlike guessing the latest durable row.
                    current_rows = [r for r in raw_rows if r.get('content') == user_message]
                    cutoff = current_rows[0].get('timestamp') if len(current_rows) == 1 else None
                    if (type(cutoff) in (int, float) and math.isfinite(cutoff)
                            and cutoff >= current['started_at']):
                        segments.append((session_id, 'timestamp < ?', cutoff))
                    elif conn.execute('SELECT 1 FROM messages WHERE session_id=? AND ' + clean + ' LIMIT 1',
                                      (session_id,)).fetchone():
                        raise ValueError('scope_missing_anchor')
                seen = {session_id}
                for _ in range(32):
                    if SessionMessagesMixin._is_explicit_fork_child_row(None, current, include_reset=True):
                        break
                    parent_id = current.get('parent_session_id')
                    if not parent_id:
                        break
                    parent = conn.execute('SELECT * FROM sessions WHERE id=?', (parent_id,)).fetchone()
                    if not parent:
                        raise ValueError('scope_missing_ancestor')
                    parent = dict(parent)
                    if parent.get('end_reason') != 'compression':
                        break
                    if parent_id in seen:
                        raise ValueError('scope_lineage_cycle')
                    if parent.get('profile_name') != current.get('profile_name'):
                        raise ValueError('scope_profile_mismatch')
                    cutoff = current.get('started_at')
                    if not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff):
                        raise ValueError('scope_missing_cutoff')
                    segments.append((parent_id, 'timestamp < ?', cutoff))
                    seen.add(parent_id)
                    current = parent
                else:
                    raise ValueError('scope_lineage_limit')
                for sid, bound, cutoff in reversed(segments):
                    saved = conn.execute("SELECT content, display_metadata, CASE WHEN api_content != '' THEN 'native' END AS api_content "
                                         'FROM messages WHERE session_id=? AND ' + clean +
                                         ' AND ' + bound + ' ORDER BY id LIMIT 201', (sid, cutoff)).fetchall()
                    for row in saved:
                        content = row['content']
                        # Multimodal JSON cannot safely be treated as human text here.
                        if not isinstance(content, str) or content.startswith(('[', '{')):
                            raise ValueError('scope_unsupported_content')
                        recovered.append(_user_row(dict(row)))
                    if len(recovered) > 200 or _images.serialized_size(recovered) > 32000:
                        raise ValueError('context_limit')
    # Retain ordering/repeated restrictions; only remove an overlapping suffix/prefix.
    overlap = 0
    for n in range(1, min(len(recovered), len(rows)) + 1):
        if [r['content'] for r in recovered[-n:]] == [r['content'] for r in rows[:n]]:
            overlap = n
    rows = (recovered[:-overlap] if overlap else recovered) + rows
    if not rows or rows[-1].get('content') != user_message:
        rows.append({'role': 'user', 'content': user_message})
    if len(rows) > 200 or _images.serialized_size(rows) > 32000:
        raise ValueError('context_limit')
    return rows


def resolve_scope(session_id, contexts, turn_messages=None):
    """Resolve a cache miss after rotation, or recover a fresh-process request.

    `contexts` must be the caller's bounded, active-profile, per-turn capture
    cache. Caller owns locking and storing the returned isolated copy under the
    new ID. Never follows descendants, imports durable tool results, or reads
    newer user rows into a frozen capture. None is a poisoned capture, not a miss.
    For restart without a capture, caller must supply real current-turn user
    rows; durable latest-message guessing is forbidden.
    """
    from contextlib import closing
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name
    from hermes_state_messages import SessionMessagesMixin

    path = Path(get_hermes_home()) / 'state.db'
    if not session_id:
        raise ValueError('scope_missing_session')
    if path.exists():
        with closing(_ro_connect(path)) as conn:
            conn.execute('BEGIN')
            ticks = [0]
            def budget():
                ticks[0] += 1
                return int(ticks[0] > 1000)
            conn.set_progress_handler(budget, 1000)
            current = conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            seen = set()
            for _ in range(32):
                if current is None:
                    raise ValueError('scope_missing_ancestor')
                current = dict(current)
                sid = current['id']
                if sid in seen:
                    raise ValueError('scope_lineage_cycle')
                seen.add(sid)
                if current.get('profile_name') != get_active_profile_name():
                    raise ValueError('scope_profile_mismatch')
                if sid in contexts:
                    rows = copy.deepcopy(contexts[sid])
                    if rows is None:
                        raise ValueError('scope_incomplete_capture')
                    supplied_users = [r.get('content') for r in turn_messages or []
                                      if is_evidence_row(r) and r.get('role') == 'user']
                    captured_users = [r.get('content') for r in rows if r.get('role') == 'user']
                    if supplied_users and (not captured_users or supplied_users[-1] != captured_users[-1]):
                        raise ValueError('scope_current_request_mismatch')
                    if len(rows) > 200 or _images.serialized_size(rows) > 32000:
                        raise ValueError('context_limit')
                    return rows
                if SessionMessagesMixin._is_explicit_fork_child_row(None, current, include_reset=True):
                    break
                parent_id = current.get('parent_session_id')
                if not parent_id:
                    break
                parent = conn.execute('SELECT * FROM sessions WHERE id=?', (parent_id,)).fetchone()
                if parent is None:
                    raise ValueError('scope_missing_ancestor')
                if parent['end_reason'] != 'compression':
                    break
                cutoff = current.get('started_at')
                started = parent['started_at']
                if (type(cutoff) not in (int, float) or not math.isfinite(cutoff)
                        or type(started) not in (int, float) or not math.isfinite(started)
                        or started > cutoff):
                    raise ValueError('scope_invalid_cutoff')
                current = parent
            else:
                raise ValueError('scope_lineage_limit')
    rows = [r for r in turn_messages or [] if is_evidence_row(r) and r.get('role') == 'user']
    if not rows or not isinstance(rows[-1].get('content'), str) or not rows[-1]['content']:
        raise ValueError('scope_current_request_unavailable')
    return recover_scope(session_id, rows[-1]['content'], rows[:-1])


def align_observed(rows, turn_messages):
    """Reinsert observed pairs at their live result positions, never after scope.

    Tools missing from the current suffix stay in the captured prefix. The live
    suffix provides chronology, not a replacement for observed result contents.
    Duplicate/reordered ids are ambiguous and cannot certify closure.
    """
    calls, pairs = {}, {}
    for row in rows:
        if row.get('role') == 'assistant':
            for call in row.get('tool_calls') or []:
                cid = call.get('id')
                if cid in calls:
                    raise ValueError('duplicate_call_id')
                calls[cid] = call
        elif row.get('role') == 'tool':
            cid = row.get('tool_call_id')
            if cid in pairs or cid not in calls:
                raise ValueError('ambiguous_observed_result')
            pairs[cid] = [{'role': 'assistant', 'tool_calls': [calls[cid]]}, row]
    live_ids = [r.get('tool_call_id') for r in turn_messages or []
                if is_evidence_row(r) and r.get('role') == 'tool']
    if len(set(live_ids)) != len(live_ids):
        raise ValueError('duplicate_result')
    present = set(live_ids) & pairs.keys()
    if [cid for cid in live_ids if cid in pairs] != [cid for cid in pairs if cid in present]:
        raise ValueError('observed_chronology_mismatch')
    # Observed results absent from a live suffix must precede that suffix.
    seen_present = False
    for cid in pairs:
        seen_present = seen_present or cid in present
        if seen_present and cid not in present:
            raise ValueError('observed_chronology_incomplete')
    prefix = []
    for row in rows:
        if row.get('role') == 'tool' and row.get('tool_call_id') in present:
            continue
        if row.get('role') == 'assistant' and row.get('tool_calls'):
            row = dict(row, tool_calls=[c for c in row['tool_calls'] if c.get('id') not in present])
        prefix.append(row)
    return prefix, pairs
