"""Source projections. Never store transcript bodies or tool arguments.

Each source commits its cursor together with derived samples. Session sources
revisit changed sessions, including pending calls/turns; board sources revisit
changed tasks, including old tasks that finish after the initial backfill.
"""
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

NOTICES = {'kanban_notice', 'internal_notification', 'process_complete',
           'async_delegation_complete', 'loop_tick', 'auto_continue'}


def ro_connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


def sample(metric, key, start, end, host='local', gateway='local', **attrs):
    if start is None or end is None or end < start:
        return None
    event_id = hashlib.sha256((metric + ':' + str(key)).encode()).hexdigest()
    return dict(id=event_id, metric=metric, start=start, dur_ms=(end-start)*1000,
                host=host, gateway=gateway, attrs={k: str(v) for k, v in attrs.items()})


def text_content(row):
    text = row.get('content') or ''
    if text.startswith('\x00json:'):
        try:
            text = '\n'.join(b.get('text', '') for b in json.loads(text[6:]) if b.get('type') == 'text')
        except (ValueError, TypeError, AttributeError):
            return '[Undecodable structured content]'
    if row['role'] == 'user':
        text = re.split(r'\n(?:\[|<memory-context>|@image:)', text, 1)[0].strip()
        if text.startswith(('Canonical user decision.', 'Message from another Hermes session', 'This comes from an agent')):
            return '[Runtime notice]'
    return text


def state_samples(rows, definitions, source_id='state', host='local', gateway='local'):
    """Independent pure projection, matching research's earliest-message UID dedup."""
    by_session = defaultdict(list)
    for row in rows:
        by_session[row['session_id']].append(dict(row))
    for sid, messages in by_session.items():
        seen, clean = set(), []
        for m in sorted(messages, key=lambda r: (r['timestamp'], r['id'])):
            key = m.get('message_uid') or (m['timestamp'], m['role'], m.get('tool_call_id'), m.get('content'), m.get('tool_calls'))
            if key in seen:
                continue
            seen.add(key)
            m['content'] = text_content(m)
            clean.append(m)
        pending = {}
        for m in clean:
            if m['role'] == 'assistant' and m.get('tool_calls'):
                try:
                    calls = json.loads(m['tool_calls'])
                except (TypeError, ValueError):
                    calls = []
                for call in calls if isinstance(calls, list) else []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get('function') or {}
                    if not isinstance(function, dict):
                        continue
                    for key in ('id', 'call_id'):
                        if call.get(key):
                            pending[call[key]] = (m['timestamp'], function.get('name', call.get('name', 'unknown')))
            if m['role'] == 'tool':
                call = pending.pop(m.get('tool_call_id'), None)
                if call:
                    name = m.get('tool_name') or call[1]
                    for definition in definitions:
                        if definition['end'] == 'tool_result' and definition['start'] == name:
                            result = sample(definition['id'], (source_id, sid, m.get('tool_call_id')), call[0], m['timestamp'], host, gateway, session=sid)
                            if result:
                                yield result
        for i, user in enumerate(clean):
            if user['role'] != 'user' or user.get('display_kind') in NOTICES:
                continue
            text = user['content'].strip()
            if not text or re.match(r'^[\[<]|^[^\w\s]+\s*\[', text):
                continue
            boundary = next((j for j in range(i+1, len(clean)) if clean[j]['role'] == 'user'), len(clean))
            assistants = [m for m in clean[i+1:boundary] if m['role'] == 'assistant']
            finals = [m for m in assistants if not m.get('tool_calls') and m.get('finish_reason') in ('stop', 'end_turn') and m['content'].strip()]
            for definition in definitions:
                if definition['start'] != 'typed_user':
                    continue
                end = assistants[0] if definition['end'] == 'first_assistant' and assistants else (finals[-1] if definition['end'] == 'explicit_final' and finals else None)
                if end:
                    result = sample(definition['id'], (source_id, sid, user.get('message_uid') or user['id']), user['timestamp'], end['timestamp'], host, gateway, session=sid)
                    if result:
                        yield result


def collect_state(path, store, definitions, since, until, host='local', gateway='local'):
    source = 'state:' + str(Path(path).resolve())
    from .registry import fingerprint
    cursor_key = source + ':' + fingerprint(definitions)
    previous = store.cursor(cursor_key) or {}
    with closing(ro_connect(path)) as db:
        ceiling = db.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()[0]
        # Restrict to Desktop: these are interactive proxies, not autonomous jobs.
        sessions = db.execute('''SELECT DISTINCT m.session_id FROM messages m JOIN sessions s ON s.id=m.session_id
            WHERE s.source='desktop' AND m.id>? AND m.id<=? AND m.timestamp>=?''',
                              (previous.get('id', 0), ceiling, since)).fetchall()
        samples = []
        for (sid,) in sessions:
            rows = [dict(r) for r in db.execute('''SELECT id,session_id,role,
                CASE WHEN role='user' THEN content WHEN role='assistant' THEN substr(content,1,400) ELSE NULL END content,
                tool_call_id,tool_calls,tool_name,timestamp,finish_reason,display_kind,message_uid
                FROM messages WHERE session_id=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,id''', (sid, since, until))]
            samples.extend(state_samples(rows, definitions, source, host, gateway))
    return store.ingest(samples, cursor_key, {'id': ceiling, 'at': until})


def board_samples(tasks, events, definitions, source_id='board', host='local', gateway='local'):
    grouped = defaultdict(list)
    for event in events:
        grouped[event['task_id']].append(event)
    for task in tasks:
        history = sorted(grouped[task['id']], key=lambda e: e['id'])
        for definition in definitions:
            start_kind, end_kind = definition['start'], definition['end']
            if start_kind == 'created':
                start = next((e['created_at'] for e in history if e['kind'] == 'created'), None)
                ends = [e['created_at'] for e in history if e['kind'] == end_kind]
                end = (ends[-1] if end_kind == 'completed' else ends[0]) if ends else None
                result = sample(definition['id'], (source_id, task['id']), start, end, host, gateway, card=task['id'])
                if result:
                    yield result
            else:
                pending = None
                for event in history:
                    if event['kind'] == start_kind:
                        pending = event
                    elif event['kind'] == end_kind and pending is not None:
                        result = sample(definition['id'], (source_id, pending['id'], event['id']), pending['created_at'], event['created_at'], host, gateway, card=task['id'])
                        if result:
                            yield result
                        pending = None


def collect_board(path, store, definitions, since, until, host='local', gateway='local'):
    source = 'kanban:' + str(Path(path).resolve())
    from .registry import fingerprint
    cursor_key = source + ':' + fingerprint(definitions) + ':events-v2'
    previous = store.cursor(cursor_key) or {}
    with closing(ro_connect(path)) as db:
        ceiling = db.execute('SELECT COALESCE(MAX(id),0) FROM task_events').fetchone()[0]
        tasks = [dict(r) for r in db.execute('''SELECT DISTINCT t.id FROM tasks t
             JOIN task_events e ON e.task_id=t.id WHERE e.id>? AND e.id<=?
             AND e.created_at>=? AND e.created_at<=?''',
                                           (previous.get('id', 0), ceiling, since, until))]
        events = []
        for task in tasks:
            events.extend(dict(r) for r in db.execute('''SELECT id,task_id,kind,created_at FROM task_events
                WHERE task_id=? AND created_at<=? ORDER BY created_at,id''', (task['id'], until)))
    samples = [s for s in board_samples(tasks, events, definitions, source, host, gateway) if since <= s['start'] <= until]
    # Board events are append-only: incoming observations revise existing values.
    # A recently archived old card is not an authoritative deletion of its retained
    # observation merely because its creation falls outside this backfill window.
    return store.reconcile(samples, [d['id'] for d in definitions], cursor_key,
                           {'id': ceiling, 'at': until}, [])


def collect_journals(paths, store, definitions, until, host='local', gateway='local'):
    """Persist unfinished stage pairs across files, rotation and process restarts."""
    inserted = 0
    for path in sorted(map(Path, paths)):
        stat = path.stat()
        from .registry import fingerprint
        source = 'journal:' + str(path.resolve()) + ':' + fingerprint(definitions)
        old = store.cursor(source) or {}
        offset = old.get('offset', 0) if old.get('inode') == stat.st_ino and old.get('offset', 0) <= stat.st_size else 0
        pending = store.meta_get('stage_pending', {})
        samples = []
        with path.open('rb') as stream:
            stream.seek(offset)
            while True:
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    break  # partial writes are retried, never consumed
                offset = stream.tell()
                try:
                    row = json.loads(line)
                    if 'metric' in row:
                        if row['metric'] in {d['id'] for d in definitions}:
                            from .store import _sample
                            samples.append(_sample(row))
                        continue
                    key = str(row.get('boot')) + ':' + str(row['id'])
                    stages = pending.setdefault(key, {})
                    stages[row['stage']] = {k: row.get(k) for k in ('utc_ns', 'mono_ns', 'path', 'method')}
                    for d in definitions:
                        a, b = stages.get(d['start']), stages.get(d['end'])
                        if not a or not b or (d.get('path') and d['path'] != a.get('path')):
                            continue
                        if a.get('mono_ns') is None or b.get('mono_ns') is None or not a.get('utc_ns'):
                            continue
                        start = a['utc_ns'] / 1e9
                        dur = (b['mono_ns'] - a['mono_ns']) / 1e9
                        result = sample(d['id'], key, start, start+dur, host, gateway)
                        if result:
                            samples.append(result)
                except (ValueError, TypeError, KeyError):
                    continue
        # Pending rows are a recoverable projection: save before cursor commit.
        pending = {key: stages for key, stages in pending.items() if any((s.get('utc_ns') or 0)/1e9 >= until-3600 for s in stages.values())}
        store.meta_set('stage_pending', dict(list(pending.items())[-10000:]))
        inserted += store.ingest(samples, source, {'inode': stat.st_ino, 'offset': offset, 'at': until})
    return inserted
