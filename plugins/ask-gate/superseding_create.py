"""Create-time reconciliation of obsolete, non-running work.

Matching is advisory about relevance, not a semantic claim of supersession. Each
candidate needs an explicit disposition; naming an ID alone never releases it.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

_CLOSED = {'done', 'archived', 'running'}
# Seven-day replay requires a stricter general threshold than approval asks.
# Retain the original threshold for explicit named removals: otherwise a new
# design's novel vocabulary hides the exact old acceptance it deletes.
MIN_COVERAGE = 0.50
_REMOVAL = re.compile(r'\b(?i:delete|remove|replace|retire|drop)\s+(?:(?i:the)\s+)?(?:`([^`\n]+)`|([A-Z][A-Z_]{2,}))')


def removals(text: str) -> list[str]:
    names = []
    for match in _REMOVAL.finditer(text):
        prefix = re.split(r'[.!?\n]', text[:match.start()])[-1]
        if re.search(r"\b(?:not|never|without|no|don[’']t)\b", prefix, re.I):
            continue
        names.append(match[1] or match[2])
    return names


def removes_named(text: str, names: list[str]) -> bool:
    return any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I) for name in names)

_HEADER = re.compile(r'^\s*(?:#{1,6}\s*)?Supersedes:\s*$', re.I)
_ENTRY = re.compile(r'^\s*[-*]?\s*`?(t_[0-9a-f]{8,})`?\s*:\s*(archive|amend|independent)\s*(?::\s*(.*))?\s*$', re.I)


def off() -> bool:
    try:
        from hermes_cli.config import load_config_readonly
        value = (load_config_readonly().get('ask_gate') or {}).get('superseding_create', True)
        return value is False or str(value).lower() in {'false', 'off', '0', 'no'}
    except ImportError:
        return False


def dispositions(body: str) -> dict[str, tuple[str, str]]:
    """Only consecutive list entries under an explicit Supersedes: heading count."""
    result = {}
    active = False
    duplicates = set()
    for line in body.splitlines():
        if _HEADER.fullmatch(line):
            active = True
            continue
        if not active or not line.strip():
            continue
        match = _ENTRY.fullmatch(line)
        if not match:
            active = False
            continue
        tid, action, reason = match.groups()
        if tid in result:
            duplicates.add(tid)
        if action.lower() == 'independent' and not (reason or '').strip():
            continue
        result[tid] = (action.lower(), (reason or '').strip())
    return {k: v for k, v in result.items() if k not in duplicates}


def score(core: Any, scorer: Any, title: str, body: str, rows: list[tuple],
          exclude: set[str] | None = None) -> list[dict]:
    """The ask scorer's IDF coverage, with title-phrase matching as a positive signal.

    Rows have the same six fields as superseding._rows. All candidates are returned;
    there is no six-hit cap or identical-retry release on the create surface.
    """
    # Disposition prose must not manufacture new topical hits on the retry.
    body = re.split(r'(?im)^\s*(?:#{1,6}\s*)?Supersedes:\s*$', body)[0]
    q = scorer.tokens(core, title + '\n' + body[:scorer.BODY_CHARS])
    docs = [(r, scorer.tokens(core, (r[1] or '') + '\n' + (r[4] or '')[:scorer.BODY_CHARS]))
            for r in rows if r[2] not in _CLOSED]
    if not docs:
        return []
    df = {}
    for _, ts in docs:
        for token in ts & q:
            df[token] = df.get(token, 0) + 1
    idf = {t: math.log(1 + len(docs) / count) for t, count in df.items()}
    total = sum(idf.values())
    anchors = q - {core._stem(t) for t in scorer._ANCHOR_STOP}
    hits = []
    candidate_rows = [r for r, _ in docs]
    named_removals = removals(title + '\n' + body[:scorer.BODY_CHARS])
    for row, ts in docs:
        if row[0] in (exclude or set()):
            continue
        shared = ts & q
        coverage = sum(idf[t] for t in shared) / total if total else 0
        named_removal = removes_named((row[1] or '') + '\n' + (row[4] or '')[:scorer.BODY_CHARS], named_removals)
        floor = scorer.MIN_COVERAGE if named_removal else MIN_COVERAGE
        if coverage < floor:
            continue
        token_match = bool(anchors & ts and len(shared) >= scorer.MIN_SHARED)
        phrase = (not token_match and scorer.mentioned(core, row, title + '\n' + body[:scorer.BODY_CHARS], candidate_rows))
        if phrase or token_match:
            hits.append({'id': row[0], 'title': row[1], 'score': round(coverage, 3),
                         'shared': sorted(shared), 'phrase': phrase})
    return sorted(hits, key=lambda h: (-h['score'], h['id']))


def inventory(core: Any) -> tuple[list[tuple], dict[str, Path], dict[str, set[str]]]:
    rows, paths, edges = [], {}, {}
    for path in core.boards():
        conn = core._ro_connect(path, timeout=2)
        try:
            for row in conn.execute("select id,title,status,completed_at,coalesce(body,''),'' from tasks where status not in ('done','archived','running')"):
                if row[0] not in paths:
                    rows.append(tuple(row))
                    paths[row[0]] = path
            tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
            if 'task_links' in tables:
                for parent, child in conn.execute('select parent_id,child_id from task_links'):
                    edges.setdefault(parent, set()).add(child)
                    edges.setdefault(child, set()).add(parent)
        finally:
            conn.close()
    return rows, paths, edges


def result_dict(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def created_id(value: Any) -> str | None:
    value = result_dict(value)
    if not value.get('ok') or value.get('created') is False or value.get('error'):
        return None
    tid = value.get('task_id') or (value.get('task') or {}).get('id')
    return tid if isinstance(tid, str) and re.fullmatch(r't_[0-9a-f]{8,}', tid) else None


def preflight(core: Any, scorer: Any, args: dict, tools: list[dict], session_id: str = '') -> dict | None:
    if off():
        return None
    rows, paths, edges = inventory(core)
    excluded = set(args.get('parents') or [])
    # An idempotent retry can refer to an already-created card; exclude its graph neighbors.
    key = args.get('idempotency_key')
    if key:
        for path in core.boards():
            conn = core._ro_connect(path, timeout=2)
            try:
                for (tid,) in conn.execute('select id from tasks where idempotency_key=?', (key,)):
                    excluded.add(tid)
                    excluded.update(edges.get(tid, set()))
            finally:
                conn.close()
    for tool in tools:
        if tool.get('tool') == 'kanban_create':
            tid = created_id(tool.get('result'))
            if tid:
                excluded.add(tid)
    hits = score(core, scorer, str(args.get('title') or ''), str(args.get('body') or ''), rows, excluded)
    declared = dispositions(str(args.get('body') or ''))
    missing = [h for h in hits if h['id'] not in declared]
    if missing:
        return {'action': 'block', 'message': 'ask-gate: superseding_create — reconcile these open cards:\n'
                + '\n'.join('- ' + h['id'] + ': ' + h['title'] for h in missing)
                + '\nAdd a Supersedes: block with one line per matched ID: '
                  '<id>: archive, <id>: amend (after commenting the acceptance change), '
                  'or <id>: independent: <reason>. Naming a card or repeating this create does not release the check.'}
    archives = []
    for hit in hits:
        tid = hit['id']
        action, _ = declared[tid]
        if action == 'amend' and not amended(core, paths[tid], tid, tools):
            return {'action': 'block', 'message': 'ask-gate: superseding_create — ' + tid
                    + ' needs a successful kanban_comment this turn starting Acceptance change: before amend.'}
        if action == 'archive':
            refusal = archive_candidate(paths[tid], tid)
            if refusal:
                return {'action': 'block', 'message': 'ask-gate: superseding_create — cannot archive '
                        + tid + ': ' + refusal + '. Use amend or independent with a reason instead.'}
            row = next(r for r in rows if r[0] == tid)
            archives.append((str(paths[tid]), tid, row[1], row[4]))
    if archives:
        pending = core.shared_turn_state(str(core.home())).setdefault('superseding_create_pending', {})
        if len(pending) >= 64:
            return {'action': 'block', 'message': 'ask-gate: superseding_create has 64 unconfirmed archive creates; reconcile them before creating more.'}
        pending[(session_id, json.dumps(args, sort_keys=True))] = archives
    return None


def amended(core: Any, path: Path, tid: str, tools: list[dict]) -> bool:
    for tool in tools:
        if tool.get('tool') != 'kanban_comment':
            continue
        result = result_dict(tool.get('result'))
        if not result.get('ok') or result.get('task_id') != tid or not result.get('comment_id'):
            continue
        conn = core._ro_connect(path, timeout=2)
        try:
            row = conn.execute('select body from task_comments where id=? and task_id=?',
                               (result['comment_id'], tid)).fetchone()
            if row and re.match(r'(?is)^\s*Acceptance change:\s*\S.{15,}', row[0] or ''):
                return True
        finally:
            conn.close()
    return False


def board_name(path: Path) -> str:
    return path.parent.name if path.parent.parent.name == 'boards' else 'default'


def archive_candidate(path: Path, tid: str, *, new_id: str | None = None,
                      snapshot: tuple | None = None) -> str | None:
    """Use core scope/foreign/containment gates and atomic pointer+archive, never stop a run."""
    from tools.kanban_tools import _reject_delegated_child_mutation
    from tools.kanban_tools_board import tool_board
    from hermes_cli.kanban_archive_scope import worker_archive_scope
    from hermes_cli.kanban_containment_connections import held
    from hermes_cli.kanban_foreign_card import check
    from hermes_cli.kanban_remote_stop import remote_holder

    _reject_delegated_child_mutation('kanban_archive', {'board': board_name(path)})
    with tool_board(board_name(path)) as (kb, conn):
        def precondition(c):
            check(c, tid)
            refusal, _ = worker_archive_scope(c, tid)
            if refusal:
                return refusal
            row = c.execute('select status,claim_lock,worker_pid,title,coalesce(body,\'\') from tasks where id=?', (tid,)).fetchone()
            if not row or row[0] in _CLOSED or row[1] or row[2]:
                return 'card is no longer open and unclaimed'
            if held(c, tid) or remote_holder(c, tid):
                return 'card is contained or remotely claimed'
            if snapshot and (row[3], row[4]) != snapshot:
                return 'card acceptance changed after scoring'
            return None
        refusal = precondition(conn)
        if refusal or new_id is None:
            return refusal
        pointer = 'Superseded by ' + new_id + ' (Supersedes: archive disposition on the replacement card).'
        if not kb.archive_task(conn, tid, via='superseding_create', comment=('ask-gate', pointer),
                               precondition=precondition, event_extra={'superseded_by': new_id}):
            return 'archive precondition refused; nothing archived'
        row = conn.execute('select status from tasks where id=?', (tid,)).fetchone()
        comment = conn.execute('select 1 from task_comments where task_id=? and body=?', (tid, pointer)).fetchone()
        if not row or row[0] != 'archived' or not comment:
            return 'archive readback was not confirmed'
    return None


def after_create(core: Any, args: dict, result: Any, session_id: str) -> None:
    pending = core.shared_turn_state(str(core.home())).setdefault('superseding_create_pending', {})
    archives = pending.pop((session_id, json.dumps(args, sort_keys=True)), [])
    new_id = created_id(result)
    if not new_id:
        return
    for path, tid, title, body in archives:
        try:
            refusal = archive_candidate(Path(path), tid, new_id=new_id, snapshot=(title, body))
        except Exception as exc:
            refusal = str(exc)
        core.log_event('superseding_create_archive', new_id=new_id, old_id=tid,
                       archived=not bool(refusal), error=refusal)
        if refusal:
            # Observer hooks cannot change the successful create result. Persist a
            # truthful warning on its card rather than silently losing the cleanup.
            from tools.kanban_tools import _handle_comment
            _handle_comment({'task_id': new_id, 'board': args.get('board'),
                             'body': 'Supersession cleanup NOT performed for ' + tid + ': ' + refusal})
