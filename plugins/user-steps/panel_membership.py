"""Read-only complete-membership inputs for the panel remote reconciliation memo.

This is an invalidation key, NOT a projection or evidence of release. No cached
membership list, DB mtime, WAL signature or global revision can certify it.
ActionStore request/binding/operation dependencies belong to the caller's key.
"""
from contextlib import closing
import hashlib
import json
import math



def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _known_task(task):
    return (task.get('status') in ('todo', 'ready', 'running', 'scheduled',
                                  'review', 'blocked', 'triage', 'done', 'archived')
            and not task.get('stale') and not task.get('partial_source_unknown'))


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def _aliases(conn, session, step_sql, cap):
    """Canonical continuation edges only; forks/delegates are not aliases."""
    found = set()
    todo = [session]
    edges = set()
    while todo:
        sid = todo.pop()
        if sid in found:
            continue
        if len(found) >= cap:
            raise ValueError('compression lineage truncated')
        row = conn.execute('SELECT parent_session_id FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None:
            raise ValueError('session missing')
        found.add(sid)
        child = conn.execute(step_sql, (sid,)).fetchone()
        if child:
            edges.add((sid, child[0]))
            todo.append(child[0])
        if row[0]:
            parent = conn.execute(step_sql, (row[0],)).fetchone()
            if parent and parent[0] == sid:
                edges.add((row[0], sid))
                if row[0] not in found:
                    todo.append(row[0])
    forward = dict(edges)
    for sid in found:
        seen = set()
        while sid in forward:
            if sid in seen:
                raise ValueError('compression cycle')
            seen.add(sid)
            sid = forward[sid]
    return sorted(found), sorted(edges)


def _fresh(value, now, window):
    return (type(value) in (int, float) and math.isfinite(value)
            and value > 0 and 0 <= now - value <= window)


def _shadows(paths, *, connect, gateway, profile, aliases, obligations, now, window):
    """Complete origin-scoped shadow inventory, not the reader's last-good cache.

    Owner labels are ledger-derived in replica_cards. Only an exact, current
    binding can vouch for a selected shadow; default-profile guesses cannot.
    Every configured board must be readable and fresh, including empty boards.
    """
    captured = []
    for board, path in paths:
        with closing(connect(path, timeout=.5)) as conn:
            conn.execute('BEGIN')
            pulled = conn.execute('SELECT pulled_at FROM kanban_sync_state').fetchall()
            if len(pulled) != 1 or not _fresh(pulled[0][0], now, window):
                raise ValueError('unknown shadow freshness')
            conn.execute('SELECT id,title,status,assignee,created_at,started_at,priority,'
                         'block_kind,human_request,created_on FROM tasks LIMIT 0')
            conn.execute('SELECT task_id,chat_gateway,chat_profile,chat_session,kind,released '
                         'FROM task_chat_links LIMIT 0')
            links = conn.execute(
                'SELECT * FROM task_chat_links WHERE chat_gateway=? AND chat_profile=? '
                'AND chat_session IN (SELECT value FROM json_each(?))',
                (gateway, profile, _json(aliases)))
            names = [col[0] for col in links.description]
            links = [dict(zip(names, row)) for row in links.fetchall()]
            tasks = []
            for link in links:
                if link.get('released') not in (0, 1) or not link.get('task_id'):
                    raise ValueError('unknown shadow link')
                row = conn.execute('SELECT * FROM tasks WHERE id=?', (link['task_id'],))
                names = [col[0] for col in row.description]
                raw = row.fetchone()
                task = dict(zip(names, raw)) if raw is not None else None
                tasks.append(task)
                # Local cards do not enter replica_cards' remote membership.
                if task is not None and task.get('created_on') == gateway:
                    continue
                if link['released']:
                    continue
                if task is None or not _known_task(task):
                    raise ValueError('unknown shadow task')
                matches = [b for b, resolved, _, _, released, _ in obligations
                           if not released and b['owner']['board'] == board
                           and b['owner']['task_id'] == link['task_id']
                           and (b['origin']['session'] == link['chat_session']
                                or resolved == link['chat_session'])]
                owners = {(b['owner']['gateway'], b['owner']['profile']) for b in matches}
                if len(owners) != 1 or next(iter(owners))[0] != task.get('created_on'):
                    raise ValueError('shadow owner/binding mismatch')
            captured.append([board, sorted(links, key=_json), sorted(tasks, key=_json)])
    return sorted(captured, key=_json)


def capture(owner, *, gateway, state_path, replica_path, ledger_path, connect,
            step_sql, chain_cap, linked_sessions, validate_binding,
            scope_heartbeats, now, shadow_paths=None, shadow_window=180.0,
            display_cards=None):
    """Return a stable digest, or None when complete inputs cannot be vouched for.

    Each database is read in a bounded read transaction and closed before return.
    The caller captures BEFORE work and must retain a non-sliding full-pass ceiling.
    Required files/tables, including an initialized empty ledger, must exist.
    Rows without provable ownership fail closed rather than becoming empty input.
    """
    try:
        profile, session = owner['profile'], owner['storedSessionId']
        if not all(isinstance(v, str) and v for v in (gateway, profile, session)):
            return None
        with closing(connect(state_path, timeout=.5)) as state:
            state.execute('BEGIN')
            aliases, edges = _aliases(state, session, step_sql, chain_cap)
        alias_set = set(aliases)
        obligations, receipts, hashes = [], [], set()
        with closing(connect(ledger_path, timeout=.5)) as ledger:
            ledger.execute('BEGIN')
            for row in ledger.execute('SELECT binding_json,resolved_session,task_json,'
                                      'observation_version,released,binding_hash FROM origin_obligations'):
                raw, resolved, task_raw, version, released, digest = row
                binding = _object(raw)
                validate_binding(binding)
                origin, target = binding['origin'], binding['owner']
                if (origin['gateway'], origin['profile']) != (gateway, profile):
                    continue
                if origin['session'] not in alias_set and resolved not in alias_set:
                    continue
                task = _object(task_raw)
                if not _known_task(task) or type(version) is not int or version < 1 or released not in (0, 1):
                    return None
                obligations.append([binding, resolved, task, version, released, digest])
                hashes.add(digest)

            for digest, raw in ledger.execute('SELECT binding_hash,receipt_json FROM origin_receipts'):
                if digest in hashes:
                    receipts.append([digest, _object(raw)])
        with closing(connect(replica_path, timeout=.5)) as replica:
            replica.execute('BEGIN')
            meta = dict(replica.execute("SELECT k,v FROM meta WHERE k IN ('gateway','last_ok','offline_since')"))
            if meta.get('gateway') != gateway or meta.get('offline_since'):
                return None
            if not _fresh(float(meta.get('last_ok', 'nan')), now, 180):
                return None
            # Read all membership links, not just previously known task IDs. The
            # selected inventory must see arrivals, removals and legacy twins.
            candidates, direct = [], set()
            for gw, key, raw, version in replica.execute(
                    "SELECT gw,key,data,version FROM replica WHERE coll='tasks'"):
                task = _object(raw)
                parts = key.split('/')
                if len(parts) not in (2, 3) or not all(parts) or task.get('id') != parts[-1]:
                    raise ValueError('unknown replica identity')
                identity = (parts[-2], parts[-1])
                candidates.append((gw, key, task, version, identity))
                if alias_set.intersection(linked_sessions(task, gateway, profile)):
                    direct.add((gw, key))
            # Retained ledger payloads are not current just because their scope
            # is live. Require the exact owner publication, version AND content,
            # as the canonical projector does before accepting an observation.
            # Missing/re-homed records stay conservatively unknown: this capture
            # must not refresh the ledger or duplicate its release/rehome policy.
            publications = {(gw, key): (task, version)
                            for gw, key, task, version, _ in candidates}
            for binding, _, task, version, released, _ in obligations:
                current = publications.get((binding['owner']['gateway'], binding['task_record_key']))
                if current != (task, version):
                    return None
                if task['status'] in ('done', 'archived'):
                    from hermes_cli.kanban_origin_ledger import _owner_evidence
                    evidence = _owner_evidence(task)
                    if not (released and evidence.get('verified') is True
                            or task['status'] == 'archived' and evidence):
                        return None
            exact = direct | {(b[0]['owner']['gateway'], b[0]['task_record_key']) for b in obligations}
            seeds = [(b[0]['owner']['gateway'], b[0]['owner']['board'], b[0]['owner']['task_id'], b[2])
                     for b in obligations]
            seeds.extend((gw, identity[0], identity[1], task)
                         for gw, key, task, _, identity in candidates if (gw, key) in direct)
            def relevant(gw, key, task, identity):
                if (gw, key) in exact:
                    return True
                for source_gw, board, tid, seed in seeds:
                    if identity != (board, tid):
                        continue
                    # An unqualified twin might carry this chat's link after
                    # canonical folding. Unknown identity must not certify absence.
                    if gw == source_gw and len(key.split('/')) == 2:
                        return True
                    # Re-homes require the same non-null creation identity, not
                    # merely an ID reused in another gateway/profile's chat.
                    created = seed.get('created_at')
                    if created is not None and task.get('created_at') == created:
                        return True
                return False
            rows = [[gw, key, task, version] for gw, key, task, version, identity in candidates
                    if relevant(gw, key, task, identity)]
            for gw, key, task, version in rows:
                if (len(key.split('/')) != 3 or type(version) is not int or version < 1
                        or not _known_task(task)):
                    return None
            # Local-authority obligations require local-board inputs, which this
            # remote-only capture cannot certify. Leave them conservatively unkeyed.
            if any((b[0]['owner']['gateway'], b[0]['owner']['profile']) == (gateway, profile)
                   for b in obligations):
                return None
            scopes = {(gw, key.split('/')[0], key.split('/')[1]) for gw, key, _, _ in rows
                      if len(key.split('/')) == 3}
            scopes.update((b[0]['owner']['gateway'], b[0]['owner']['profile'], b[0]['owner']['board'])
                          for b in obligations)
            infos = replica.execute("SELECT gw,data FROM replica WHERE coll='gateway' AND key='info'").fetchall()
            beats = scope_heartbeats(infos)
            # Unknown or expired owner evidence never certifies unchanged input.
            # Healthy heartbeat refreshes are observational, not semantic churn.
            for scope in scopes:
                beat = beats.get(scope)
                if beat is None or not _fresh(beat[0], now, beat[1]):
                    return None
            # Scope presence/state is covered by the strict heartbeat verdict;
            # preserve all other relevant owner-info semantics, not other scopes.
            info_inputs = []
            for gw, raw in infos:
                wanted = {(p, b) for g, p, b in scopes if g == gw}
                if not wanted:
                    continue
                info = _object(raw)
                selected = []
                for scope in info['task_scopes']:
                    if (scope.get('profile'), scope.get('board')) in wanted:
                        selected.append({k: v for k, v in scope.items() if k != 'observed_at'})
                info_inputs.append([gw, selected])
        shadows = None if shadow_paths is None else _shadows(
            shadow_paths, connect=connect, gateway=gateway, profile=profile,
            aliases=aliases, obligations=obligations, now=now, window=shadow_window)
        display = None
        if shadow_paths is not None:
            # linked_cards uses stale_ok=True. Its retained origin projection can
            # lag the stores above, so its own semantic input must also be keyed:
            # a later cache/facts publication must not be swallowed by a memo hit.
            if not isinstance(display_cards, list) or any(not isinstance(c, dict) for c in display_cards):
                return None
            identities = {(b[0]['owner']['board'], b[0]['owner']['task_id']) for b in obligations}
            identities.update((board, link['task_id']) for board, links, _ in shadows for link in links)
            display = []
            for card in display_cards:
                if (card.get('session') not in alias_set and card.get('origin_session') not in alias_set
                        and (card.get('board'), card.get('id')) not in identities):
                    continue
                if not _known_task(card):
                    return None
                display.append({k: v for k, v in card.items() if k != 'synced_at'})
            display.sort(key=_json)
        payload = [2, gateway, profile, session, aliases, edges,
                   sorted(obligations, key=_json), sorted(receipts, key=_json),
                   sorted(rows, key=_json), sorted(info_inputs, key=_json), shadows, display]
        return hashlib.sha256(_json(payload).encode()).hexdigest()
    except Exception:
        # Optional core/version differences and every unavailable input fail closed.
        return None


def unchanged(previous, current):
    """Unknown/absent captures are always changed, including None -> None."""
    return (isinstance(previous, str) and len(previous) == 64
            and isinstance(current, str) and current == previous)


def for_remote(remote, owner, *, now, view):
    """Production adapter, resolving stores from trusted local installation state."""
    try:
        from hermes_cli import kanban_db_sync
        shadow_paths, shadow_window, display_cards = None, 180.0, None
        gateway = remote.gateway()
        if kanban_db_sync.sync_config().get('links_reader') == 'replica':
            from hermes_cli import kanban_sync_client, kanban_chat_links, kanban_session_activity
            from hermes_cli.profiles import get_active_profile_name
            if (kanban_sync_client.load_identity().gateway != gateway
                    or get_active_profile_name() != owner['profile']):
                return None
            shadow_paths = [(board, kanban_sync_client.shadow_path(board))
                            for board, _ in kanban_sync_client.board_paths()]
            shadow_window = kanban_chat_links._stale_after_s()
            display_cards = kanban_session_activity._remote_links(stale_ok=True)
        from hermes_constants import get_hermes_home
        from hermes_state_compression import _CHAIN_STEP_SQL, _CHAIN_CAP
        from hermes_cli.kanban_replica_projection import linked_sessions
        from hermes_cli.kanban_origin_ledger import validate_binding
        from hermes_cli.kanban_scope_heartbeat import scope_heartbeats
        return capture(owner, gateway=gateway,
                       state_path=remote.mirror.core._state_db(), replica_path=view.db_path(),
                       ledger_path=get_hermes_home() / 'kanban' / 'origin-ledger.db',
                       connect=remote.mirror.core._ro_connect, step_sql=_CHAIN_STEP_SQL,
                       chain_cap=_CHAIN_CAP, linked_sessions=linked_sessions,
                       validate_binding=validate_binding, scope_heartbeats=scope_heartbeats, now=now,
                       shadow_paths=shadow_paths, shadow_window=shadow_window, display_cards=display_cards)
    except Exception:
        return None
