"""Pure hub projections. Desktop may lay these out, never recount cards."""
from collections import Counter, defaultdict

STEPS = [
    ('intake', 'Intake', 'triage + lane gate'), ('waiting', 'Waiting', 'on a card, time or you'),
    ('ready', 'Ready', 'needs a worker'), ('running', 'Running', 'on a worker'),
    ('review', 'Review', 'PR review'), ('ci', 'CI', 'required checks'), ('merge', 'Merge', 'approved + green'),
    ('deploy', 'Deploy', 'merged, not live'), ('test', 'Test', 'live verification'), ('done', 'Done', 'this window')]
REASONS = {
    'you': ('needs you', '#ff5d5d'), 'dep': ('waits on a card', '#f5c542'), 'gate': ('held at gate', '#c49bff'),
    'time': ('scheduled', '#6b7280'), 'slot': ('no free worker', '#ff9f43'), 'quota': ('model quota', '#ff9f43'),
    'triage': ('unspecified', '#9aa3b2'), 'run': ('running', '#4c9bff'), 'review': ('review / CI', '#3ddc97'),
    'limit:cards_per_day': ('over cards/day', '#e08bd0'), 'limit:usd_per_day': ('over $/day', '#e08bd0')}


# The strip's all-lanes counts: one bucket per open card, in urgency order. Colours are owned by Desktop.
# A card held at a lane gate or over a lane's daily limit is not waiting for a worker: no free slot would start it.
STATUS = ('you', 'ext', 'gate', 'worker', 'run', 'dep', 'triage', 'other')
_STATUS_OF = {'you': 'you', 'time': 'ext', 'review': 'ext', 'gate': 'gate', 'slot': 'worker', 'quota': 'worker',
              'run': 'run', 'dep': 'dep', 'triage': 'triage'}


def status_of(reason):
    reason = str(reason or '')
    if reason == 'gov:dependencies':
        return 'dep'
    if reason == 'gov:schedule':
        return 'ext'
    if reason.startswith('limit:') or reason in ('gov:lane_gate', 'gov:lane_daily_limit', 'gov:lane_paused'):
        return 'gate'
    return 'worker' if reason.startswith('gov:') else _STATUS_OF.get(reason, 'other')


def _gate_kind(reason):
    return 'lane_gate' if reason in ('gate', 'gov:lane_gate') else str(reason).removeprefix('gov:')


def gates(cards, lanes):
    """Every gate holding cards, named: [{lane, kind, count, linked, unlinked: {creator: n}, admitted_today,
    admit_per_day}], biggest first. ``kind`` is ``lane_gate`` or a ``limit:*`` daily cap. ``unlinked`` counts the held
    cards no chat session owns, by the automation that filed them, so a count with no rows in the session pane says why."""
    out = {}
    for c in cards:
        if status_of(c.get('reason')) != 'gate' or not c.get('lane'):
            continue
        key = (c['lane'], _gate_kind(c.get('reason')))
        row = out.setdefault(key, {'lane': key[0], 'kind': key[1], 'count': 0, 'linked': 0, 'unlinked': Counter()})
        row['count'] += 1
        src = c.get('source') or {}
        if src.get('kind') == 'session' and src.get('id'):
            row['linked'] += 1
        else:
            row['unlinked'][str(src.get('id') or src.get('kind') or 'unknown')] += 1
    rows = []
    for row in out.values():
        lane = (lanes or {}).get(row['lane']) or {}
        row['unlinked'] = dict(row['unlinked'].most_common())
        if row['kind'] == 'lane_gate':
            row['admitted_today'] = lane.get('admitted_today')
            row['admit_per_day'] = lane.get('admit_per_day')
        rows.append(row)
    return sorted(rows, key=lambda r: (-r['count'], r['lane'], r['kind']))



def session_counts(cards):
    """Open cards per originating chat session: {session_id: {lane: {status: n}}}, for the strip's session filter."""
    out = {}
    for c in cards:
        src = c.get('source') or {}
        if src.get('kind') != 'session' or not src.get('id') or not c.get('lane'):
            continue
        lanes = out.setdefault(src['id'], {})
        cats = lanes.setdefault(c['lane'], {})
        key = status_of(c.get('reason'))
        cats[key] = cats.get(key, 0) + 1
    return out


def status_counts(cards):
    tally = Counter(status_of(c.get('reason')) for c in cards)
    return {key: tally.get(key, 0) for key in STATUS}


def reason_info(reason):
    return REASONS.get(reason, ('over limit', '#e08bd0') if str(reason).startswith('limit:') else (reason or 'unknown', '#9aa3b2'))


def age(hours):
    # Match the former renderer's positive Math.round semantics.
    rounded = lambda n: int(n + .5)
    return f'{max(1, rounded(hours * 60))}m' if hours < 1 else f'{rounded(hours)}h' if hours < 48 else f'{rounded(hours / 24)}d'


def dam_hint(reason, cards):
    n = len(cards)
    plural = lambda word: f'{n} {word}' + ('' if n == 1 else 's')
    if reason == 'you':
        text = 'answer ' + plural('ask')
    elif reason == 'dep':
        groups = {}
        unknown = 0
        for card in cards:
            blockers = card.get('blockers') or ([card['blocker']] if card.get('blocker') else [])
            valid = {b['id']: b for b in blockers if isinstance(b, dict) and b.get('id')}
            if not valid:
                unknown += 1
            for b in valid.values():
                group = groups.setdefault(b.get('pr') or b['id'], {'blocker': b, 'cards': set(), 'partial': False})
                group['cards'].add(card['id'])
                group['partial'] |= len(valid) > 1
        hints = []
        for g in sorted(groups.values(), key=lambda g: -len(g['cards'])):
            b = g['blocker']
            action = 'land PR #' + b['pr'].rstrip('/').split('/')[-1] if b.get('pr') else 'finish ' + b['id']
            result = f"unblocks {len(g['cards'])} dependency waits" if g['partial'] else f"frees {len(g['cards'])}"
            hints.append(f'{action} → {result}')
        if unknown:
            hints.append(f"{unknown} card{' waits' if unknown == 1 else 's wait'} on an open parent")
        text = '; '.join(hints)
    else:
        text = {
            'gate': f'admit or release {n} held', 'limit:cards_per_day': 'raise cards/day, or wait for local midnight',
            'limit:usd_per_day': 'raise $/day, or wait for local midnight', 'quota': 'waits for model quota',
            'time': 'wakes on its schedule', 'triage': 'specify ' + plural('card'),
            'slot': plural('card') + (' needs' if n == 1 else ' need') + ' a free worker'
        }.get(reason, plural('card') + ' held')
    return f"{text} · oldest {age(max((float(c.get('age_h') or 0) for c in cards), default=0))}"


def project(snap, raw, expanded):
    view = {**snap, 'view_version': 1, 'lanes': {name: dict(row) for name, row in snap.get('lanes', {}).items()}}
    raw = raw or {}
    if raw.get('governors'):
        view['governors'] = raw['governors']
        governors = {g['id']: g for g in raw['governors']['rows']}
        for name, row in view['lanes'].items():
            lane_cards = [c for c in raw.get('cards', []) if c.get('lane') == name]
            reasons = raw['governors']['lane_reasons'].get(name, [])
            row['governors'] = [{**governors[r['id']], 'lane_count': r['count']} for r in reasons]
            lane_known = raw['governors'].get('lane_accounting_available', raw['governors']['available'])
            if raw['governors'].get('incomplete_lanes') is not None:
                lane_known = name not in raw['governors']['incomplete_lanes']
            row['governor_held'] = sum(r['count'] for r in reasons) if lane_known else None
            row['top_reason'] = ('Governor state unknown · incomplete lane evidence' if not lane_known else
                                 f"{reasons[0]['count']} held · {governors[reasons[0]['id']]['name']}" if reasons else
                                 'lane paused' if row.get('paused') else
                                 'Governor state unknown' if not raw['governors']['available'] else
                                 'nothing ready' if not row.get('ready') else 'starting as workers free up')
            row['boards'] = sorted({c.get('board') for c in lane_cards if c.get('board')})
            row['steps'] = [dict(key=k, label=l, count=sum(c.get('step') == k for c in lane_cards),
                                 piling=sum(c.get('step') == k for c in lane_cards) >= 4) for k, l, _ in STEPS]
            row['held_sessions'] = sorted({c['source']['id'] for c in lane_cards if c.get('governor') and
                                          c.get('source', {}).get('kind') == 'session'})
            row['held_cards'] = [dict(board=c['board'], id=c['id']) for c in lane_cards if c.get('governor')]
            if not lane_known:
                row.update(governors=[], steps=[], held_sessions=[], held_cards=[])
            if name == 'babybear':
                row['device_footnote'] = raw['governors']['device_footnote']
        view['sidebar'] = dict(cost_today=sum((r.get('usage') or {}).get('usd_today', 0) for r in raw.get('lanes', {}).values()),
                               idle_lanes=[n for n, r in view['lanes'].items() if not r.get('open')])
    if expanded:
        for name, row in raw.get('lanes', {}).items():
            view['lanes'].setdefault(name, {}).update({k: v for k, v in row.items()
                                                     if k in ('limits', 'usage', 'run_hours', 'cost_usd', 'done')})
    for row in view['lanes'].values():
        row['not_ready'] = max(0, row.get('open', 0) - row.get('running', 0) - row.get('blocked_on_you', 0))
        for key in ('running', 'blocked_on_you', 'open', 'ready', 'held', 'done'):
            row.setdefault(key, 0)
        gap = row.get('actual', 0) - row.get('target', 0)
        row['drift'] = ('paused' if row.get('paused') else '' if abs(gap) <= .1 else 'over: using idle slots'
                        if gap > 0 else 'under: nothing ready' if row['ready'] == 0 else 'under: waiting for slots')
    view['totals'] = {key: sum(r[key] for r in view['lanes'].values()) for key in
                      ('running', 'not_ready', 'blocked_on_you', 'open', 'ready', 'held', 'done')}
    if raw and raw.get('configured') is not False and 'cards' in raw:
        view['status'] = status_counts(raw['cards'])
        view['gates'] = gates(raw['cards'], view['lanes'])
        if 'attention' in raw:
            view['attention'] = raw['attention']
            if view['attention'].get('available'):
                assert len(view['attention']['rows']) == view['status']['you']
        # The same card evidence owns counts and the fleet fair-share denominator.
        fleet_running = sum(c.get('reason') == 'run' for c in raw['cards'])
        idle = [name for name, row in view['lanes'].items() if not row['ready']]
        for name, row in view['lanes'].items():
            reasons = Counter(c.get('reason') or 'unknown' for c in raw['cards'] if c.get('lane') == name)
            lane = Counter()
            for reason, count in reasons.items():
                lane[status_of(reason)] += count
            limits = raw.get('lanes', {}).get(name, {}).get('limits') or row.get('limits') or {}
            maximum = limits.get('max_workers')
            cap = maximum if maximum is not None else round(row.get('target', 0) * fleet_running)
            row['strip'] = {'blocked': lane['you'], 'running': lane['run'],
                            'other': sum(lane.values()) - lane['you'] - lane['run'],
                            'cap': cap, 'in_share': min(lane['run'], cap), 'over': max(0, lane['run'] - cap),
                            'wait': {r: n for r, n in reasons.items() if r not in ('you', 'run')},
                            'cap_kind': 'max' if maximum is not None else 'fair share',
                            'fleet_running': fleet_running, 'idle_lanes': [n for n in idle if n != name]}
            series = raw.get('strip_tokens', {}).get(name)
            if series is not None:
                row['strip']['tokens'] = series
                row['strip']['token_avg'] = sum(series) / 360
            actual = raw.get('strip_actual', {}).get(name)
            if actual is not None:
                row['strip']['actual'] = actual
            row['status'] = {key: lane.get(key, 0) for key in STATUS}  # the strip's totals for a lane selection
            row['gates'] = [g for g in view['gates'] if g['lane'] == name]
        view['sessions'] = session_counts(raw['cards'])
    if not expanded:
        return view
    view['flow_error'] = raw.get('reason', '') if raw.get('configured') is False else ''
    cards = raw.get('cards', [])
    # Legacy /flow consumers still see cards and per-lane limits/usage at the top level.
    view['cards'] = cards
    view['window'] = raw.get('window')

    keys = [s[0] for s in STEPS]
    step = lambda c: c.get('step') if c.get('step') in keys else 'landing_unclassified'
    columns = [dict(key=k, label=l, hint=h) for k, l, h in STEPS]
    if any(step(c) == 'landing_unclassified' for c in cards):
        columns.append(dict(key='landing_unclassified', label='Unclassified', hint='landing stage not provable'))
    names = list(dict.fromkeys([*view['lanes'], *raw.get('lanes', {}), *(c['lane'] for c in cards)]))
    rank = lambda r: list(REASONS).index(r) if r in REASONS else len(REASONS)
    grouped = defaultdict(list)
    for c in cards:
        grouped[c['lane'], step(c)].append(c)
    cells = []
    for name in names:
        for column in columns:
            group = sorted(grouped[name, column['key']], key=lambda c: (rank(c.get('reason')), -float(c.get('age_h') or 0)))
            tally = Counter(c.get('reason') for c in group)
            count = raw.get('lanes', {}).get(name, {}).get('done', 0) if column['key'] == 'done' else len(group)
            cells.append(dict(lane=name, step=column['key'], cards=group, count=count,
                              piling=len(group) >= 4 or any(c.get('reason') == 'you' for c in group),
                              summary=' · '.join(f'{tally[r]} {reason_info(r)[0]}' for r in sorted(tally, key=rank)[:2])))
    assert sum(c['count'] for c in cells if c['step'] != 'done') == len(cards)
    view['floor'] = dict(columns=columns, names=names, cells=cells)
    graphs = {}
    for by in ('project', 'source'):
        def label(c):
            s = c.get('source') or {}
            kind = s.get('kind')
            if by == 'project':
                return s.get('project') or ('automations' if kind == 'automation' else 'you (manual)' if kind == 'user' else 'no project')
            if kind == 'user':
                return 'you (manual)'
            ident = str(s.get('id') or '?')
            return ('session' if kind == 'session' else 'auto') + ' ' + (ident[:21] + '…' if len(ident) > 22 else ident)
        counts = Counter(label(c) for c in cards)
        keep = set(sorted(counts, key=lambda k: -counts[k])[:6])
        sources, lanes, sinks, sl, lk = Counter(), Counter(), Counter(), Counter(), Counter()
        by_sink = defaultdict(list)
        for c in cards:
            source = label(c) if label(c) in keep else 'other'
            sink = 'step:' + step(c) if c.get('reason') in ('run', 'review') else 'dam:' + str(c.get('reason') or 'unknown')
            lane = c['lane']
            sources[source] += 1; lanes[lane] += 1; sinks[sink] += 1
            sl[source, lane] += 1; lk[lane, sink] += 1
            by_sink[sink].append(c)
        def nodes(counter, order):
            return [dict(key=k, count=counter[k]) for k in sorted(counter, key=order)]
        sink_nodes = nodes(sinks, lambda k: keys.index(k[5:]) - 100 if k.startswith('step:') and k[5:] in keys else rank(k[4:]))
        for node in sink_nodes:
            dam = node['key'].startswith('dam:')
            reason = node['key'][4:] if dam else 'run' if node['key'][5:] == 'running' else 'review'
            label_text, color = reason_info(reason)
            node.update(dam=dam, label=label_text if dam else dict((k, l) for k, l, _ in STEPS).get(node['key'][5:], node['key'][5:]),
                        color=color, hint=dam_hint(reason, by_sink[node['key']]) if dam else '')
        graph = dict(total=len(cards), sources=nodes(sources, lambda k: (-sources[k], k)),
                     lanes=nodes(lanes, lambda k: (names.index(k), k)), sinks=sink_nodes,
                     edges=dict(sourceLane=[dict(from_=a, to=b, count=n) for (a, b), n in sl.items()],
                                laneSink=[dict(from_=a, to=b, count=n) for (a, b), n in lk.items()]))
        for edges in graph['edges'].values():
            for edge in edges:
                edge['from'] = edge.pop('from_')
            assert sum(e['count'] for e in edges) == len(cards)
        for layer in ('sources', 'lanes', 'sinks'):
            assert sum(n['count'] for n in graph[layer]) == len(cards)
        graphs[by] = graph
    view['flow'] = {**raw, 'graphs': graphs}
    return view
