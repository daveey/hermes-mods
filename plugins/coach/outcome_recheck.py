"""Evidence-bound deterministic rechecks of deferred coach outcomes.

No network, judge, transcript mutation or broad family exemption. Unknown evidence
keeps investigation admission open. Legacy recovery uses only this producer's home.
"""
import importlib.util
import json
import os
import socket
from contextlib import closing
from pathlib import Path

MAX_LOG = 32 * 1024 * 1024
MAX_CONTEXT = 256 * 1024
MAX_ROWS = 1024


def _load(name):
    spec = importlib.util.spec_from_file_location('_coach_outcome_' + name, Path(__file__).with_name(name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def capture(nudge_id, session_id, reply, tool_log):
    """Capture whole inputs or nothing: a clipped reply is never replay authority."""
    if not nudge_id or not session_id or not isinstance(reply, str) or not reply:
        return None
    if not isinstance(tool_log, list) or not all(isinstance(x, str) for x in tool_log):
        return None
    r = dict(kind='coach_outcome', rule='pr_state_claim', nudge_id=nudge_id,
             session_id=session_id, reply=reply, tool_log=tool_log, complete=True)
    return r if len(json.dumps(r)) <= MAX_CONTEXT else None


def exempts(r):
    if (r.get('rule') != 'pr_state_claim' or r.get('complete') is not True
            or capture(r.get('nudge_id'), r.get('session_id'), r.get('reply'), r.get('tool_log')) is None):
        return None
    if _load('reply_check').pr_state_claim(r['reply'], r['tool_log']) is None:
        return 'coach pr_state_claim outcome replay (exact retained context)'
    return None


def legacy(d, handoff_signal, ro):
    """Bind a legacy payload to one exact nudge/outcome pair before reading tools."""
    if (d.get('source') != 'signal' or d.get('component') != 'agent'
            or 'coach pr_state_claim check:' not in str(d.get('symptom') or '')
            or not d.get('session_id') or not d.get('fingerprint')):
        return None
    host = socket.gethostname()
    if d.get('host') and d['host'] != host:
        return None
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    log = home / 'coach/log.jsonl'
    if not log.exists() or log.stat().st_size > MAX_LOG:
        return None
    nudges, outcomes = {}, {}
    with log.open(encoding='utf-8') as f:
        for line in f:
            if 'pr_state_claim' not in line:
                continue
            r = json.loads(line)
            nid = r.get('nudge_id')
            if not nid or r.get('rule') != 'pr_state_claim':
                continue
            if r.get('event') == 'reply_nudge':
                nudges.setdefault(nid, []).append(r)
            elif r.get('event') == 'reply_nudge_outcome':
                outcomes.setdefault(nid, []).append(r)
    matches = []
    for nid, ns in nudges.items():
        # Identical replicated carriers are one observation, differing ones ambiguous.
        ns = list({json.dumps(n, sort_keys=True): n for n in ns}.values())
        os_ = list({json.dumps(o, sort_keys=True): o for o in outcomes.get(nid, [])}.values())
        relevant = any(n.get('session_id') == d['session_id']
                       and n.get('ts') == d.get('at', d.get('complaint_at')) for n in ns)
        if not relevant:
            continue
        if len(ns) != 1 or len(os_) != 1:
            return None
        n, o = ns[0], os_[0]
        if (n.get('host') != host or n.get('session_id') != d['session_id']
                or n.get('ts') != d.get('at', d.get('complaint_at'))
                or o.get('resolved') is not False or not o.get('session_id')
                or not n['ts'] <= o.get('ts', 0) <= n['ts'] + 3600):
            continue
        rec = {**n, 'outcome_evidence': o.get('evidence') or '', 'reply': o.get('reply') or '',
               'outcome_item_ids': o.get('item_ids') or []}
        rebuilt = handoff_signal(rec, '')
        if rebuilt['fp'] == d['fingerprint'] and rebuilt['symptom'] == d.get('symptom'):
            matches.append((n, o))
    if len(matches) != 1:
        return None
    n, o = matches[0]
    # The outcome's continuation session, not necessarily the original nudge session.
    if not (home / 'state.db').exists():
        return None
    rows, size = [], 0
    with closing(ro(home / 'state.db')) as db:
        cursor = db.execute('SELECT role,substr(content,1,?),substr(tool_calls,1,?),tool_call_id,timestamp '
                            'FROM messages WHERE session_id=? AND timestamp>=? AND timestamp<=? '
                            'ORDER BY timestamp,id LIMIT ?',
                            (MAX_CONTEXT + 1, MAX_CONTEXT + 1, o['session_id'], n['ts'], o['ts'], MAX_ROWS + 1))
        for row in cursor:
            size += len(json.dumps(row))
            if len(rows) >= MAX_ROWS or size > MAX_CONTEXT:
                return None
            rows.append(row)
    if any(r[0] == 'user' for r in rows):
        return None  # a new turn boundary cannot borrow a later turn's PR read
    prefix = o.get('reply')
    if not isinstance(prefix, str) or not prefix:
        return None
    replies = {(r[1], r[4]) for r in rows if r[0] == 'assistant' and not r[2]
               and isinstance(r[1], str) and r[1].startswith(prefix)}
    if len(replies) != 1:
        return None
    reply, reply_at = next(iter(replies))
    calls, results = {}, {}
    for role, content, raw, call_id, at in rows:
        if at >= reply_at:
            continue
        if role == 'assistant' and raw:
            for c in json.loads(raw):
                cid = c.get('id') or c.get('call_id')
                if not cid:
                    return None
                if cid in calls and calls[cid] != c:
                    return None
                calls[cid] = c
        elif role == 'tool' and call_id:
            if call_id in results and results[call_id] != content:
                return None
            results[call_id] = content
    ar = _load('agent_rules')
    lines = []
    for cid, c in calls.items():
        fn = c.get('function') or {}
        # Only successful retained terminal reads establish legacy positive evidence.
        if fn.get('name') != 'terminal' or cid not in results:
            continue
        result = json.loads(results[cid])
        if result.get('exit_code') != 0 or result.get('error'):
            continue
        args = fn.get('arguments') or {}
        args = json.loads(args) if isinstance(args, str) else args
        command = args.get('command')
        if isinstance(command, str):
            command = ar.strip_injected_preamble(command)
            lines.append(f'-> terminal({command[:ar.TURN_LOG_ARG_CAP]})')
            lines.extend(ar.pr_read_lines(command))
    return capture(n['nudge_id'], o['session_id'], reply, lines) if lines else None
