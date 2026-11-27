"""Optional v1 diagnostic presentation only; never admission or lifecycle classification."""
import copy
import math


def present(view, row, record, *, now, terminal, serving, human_dependency=None):
    """Only append diagnostic fields; owner-qualified row coordinates stay authoritative."""
    if row.get('status') in terminal:
        view['actionability'] = {'version': 1, 'task_id': row.get('id'), 'action': 'no_open_action',
                                 'evidence_state': 'not_applicable'}
        view['details'].append('Actionability: n/a — terminal task has no open action')
        return
    detail = record.get('detail') if isinstance(record, dict) else None
    detail = detail if isinstance(detail, dict) else {}
    task = detail.get('task') if isinstance(detail.get('task'), dict) else {}
    a = detail.get('actionability')
    remote = bool(row.get('remote') or row.get('source') == 'replica' or row.get('ownership_state') == 'unknown')
    valid = isinstance(a, dict) and type(a.get('version')) is int and a['version'] == 1
    valid = valid and a.get('task_id') == task.get('id') == row.get('id') and isinstance(row.get('id'), str)
    # A dict keyed by bare task ID is not proof of global uniqueness.
    for coordinate in ('board', 'gateway', 'profile'):
        if record.get(coordinate) and record[coordinate] != row.get(coordinate):
            valid = False
        if task.get(coordinate) and task[coordinate] != row.get(coordinate):
            valid = False
    if remote:
        valid = valid and detail.get('source') == 'replica' and record.get('board') == row.get('board')
        valid = valid and bool(row.get('gateway')) and detail.get('owner') == row.get('gateway')
        valid = valid and bool(row.get('profile')) and record.get('profile', row['profile']) == row['profile']
    else:
        valid = valid and detail.get('source') == 'live' and row.get('gateway') in serving
    if valid:
        valid = (a.get('status') == row.get('status') and a.get('evidence_state') in ('known', 'unknown')
                 and isinstance(a.get('owner'), dict) and isinstance(a.get('escalation'), dict)
                 and all(isinstance(a.get(k), str) for k in ('action', 'instruction', 'contract_key'))
                 and all(isinstance(a.get(k), list) and all(isinstance(s, str) for s in a[k])
                         for k in ('violations', 'source_refs')))
    if valid:
        owner, escalation = a['owner'], a['escalation']
        deadline = escalation.get('at')
        valid = (isinstance(owner.get('kind'), str) and isinstance(owner.get('refs'), list)
                 and all(isinstance(s, str) for s in owner['refs'])
                 and (deadline is None or (type(deadline) in (int, float) and math.isfinite(deadline) and deadline > 0))
                 and (a.get('blocker') is None or (isinstance(a['blocker'], dict)
                      and all(isinstance(a['blocker'].get(k), str) for k in ('prerequisite', 'trigger')))))
    if not valid:
        view['actionability'] = {'version': 1, 'task_id': row.get('id'), 'evidence_state': 'unknown'}
        view['details'].append('Actionability: unavailable — evidence unknown; owner, next action, recovery trigger and deadline unavailable')
        return
    a = copy.deepcopy(a)
    if human_dependency and a['action'] == 'satisfy_dependency':
        # Source diagnosis can embed the parent's imperative in all three
        # carriers. This is display-only: retain the contract, actor, restriction
        # and evidence fields without changing the source snapshot.
        changed = False
        for dependency in a.get('dependencies') or []:
            if not isinstance(dependency, dict):
                continue
            for parent, summary in human_dependency:
                if (dependency.get('task_id') == parent['id'] and
                        all(not dependency.get(c) or dependency[c] == parent.get(c)
                            for c in ('gateway', 'profile', 'board'))):
                    dependency['trigger'] = summary
                    changed = True
                    break
        if changed:
            summary = '; '.join(d['trigger'] for d in a['dependencies']
                                if isinstance(d, dict) and isinstance(d.get('trigger'), str))
        elif not a.get('dependencies'):
            # Older snapshots can carry only the aggregate trigger.
            summary = '; '.join(label for _, label in human_dependency)
        else:
            summary = None  # Explicit foreign coordinates are not this parent.
        if summary is not None:
            a['instruction'] = summary
            if a.get('blocker'):
                a['blocker']['trigger'] = summary
    if remote or row.get('stale') or row.get('partial_source_unknown'):
        a['evidence_state'] = 'unknown'
    view['actionability'] = a
    def shown(value):
        return value if isinstance(value, str) and value.strip() else 'unavailable'
    owner = a['owner']
    label = owner['kind'] + (': ' + ', '.join(owner['refs']) if owner['refs'] else ' (unavailable)')
    lines = [f'Actionability owner: {label}', f'Actionability next action ({shown(a["action"])}): {shown(a["instruction"])}']
    blocker = a.get('blocker')
    if blocker:
        lines.extend([f'Actionability blocker: {shown(blocker["prerequisite"])}',
                      f'Actionability recovery trigger: {shown(blocker["trigger"])}'])
    deadline = a['escalation'].get('at')
    lines.append('Actionability deadline unavailable' if deadline is None else
                 f'Actionability recorded deadline: {deadline}' + (' (escalation overdue)' if now >= deadline else ''))
    lines.append('Actionability receipt ' + shown(a['escalation'].get('receipt_uid')))
    lines.append(f'Actionability evidence: {a["evidence_state"]}; violations: ' + (', '.join(a['violations']) or 'none recorded'))
    view['details'].extend(lines)
