"""Pure, owner-qualified dependency outline projection for PanelModel."""


def project(sections, sources, bundle, *, key, row_view, successors, terminal, prefixes):
    """Annotate counted rows; return separate, non-counted shared blocker nodes."""
    displayed = {r['key']: r for rows in sections.values() for r in rows}
    eligible = [r for section in ('needs_you', 'stuck', 'waiting', 'ready') for r in sections[section]
                if r['key'] in sources]
    parents, offlist = {}, {}

    for view in eligible:
        source = sources[view['key']]
        pending = [(source, frozenset())]
        nearest, blocker, cyclic = None, None, False

        for _ in range(80):
            if not pending:
                break
            card, path = pending.pop(0)
            identity = key(card)
            if identity in path:
                cyclic = True
                continue
            if card.get('status') in terminal:
                continue
            if identity != view['key']:
                blocker = blocker or card
                if identity in displayed and nearest is None:
                    nearest = identity
            pending.extend((dep, path | {identity}) for dep in successors(card))
        # An incomplete bounded walk cannot establish acyclic ancestry.
        if cyclic or pending:
            continue
        if nearest:
            parents[view['key']] = nearest
        elif blocker:
            offlist.setdefault(key(blocker), (blocker, []))[1].append(view['key'])

    groups = []
    for identity, (card, children) in offlist.items():
        if len(children) < 2:
            continue
        group = row_view(card)
        group.update(key='blocker:' + identity, kind='blocker', parent=None, depth=0,
                     detail=' · '.join(str(v) for v in (card.get('status') or 'status unknown',
                                                       card.get('gateway'), card.get('hostname')) if v),
                     status=card.get('status') or 'unknown',
                     section={'running': 'running', 'review': 'running', 'ready': 'ready',
                              'triage': 'stuck'}.get(card.get('status'), 'waiting'))
        groups.append(group)
        for child in children:
            parents[child] = group['key']
    all_nodes = {**displayed, **{g['key']: g for g in groups}}
    # Reject every cycle member (including cycles formed by multiple displayed chains).
    for start in list(parents):
        path, cursor = [], start
        while cursor in parents and cursor not in path:
            path.append(cursor)
            cursor = parents[cursor]
        if cursor in path:
            for member in path[path.index(cursor):]:
                parents.pop(member, None)
    # Depth overflow becomes a root, not a hidden row or a fabricated shortcut edge.
    def depth(identity):
        n = 0
        while identity in parents:
            n += 1
            identity = parents[identity]
        return n
    for identity in sorted(parents, key=lambda k: (depth(k), k)):
        if depth(identity) > 4:
            parents.pop(identity)
    for identity, view in displayed.items():
        view.update(parent=parents.get(identity), depth=depth(identity))
        if identity in parents:
            parent = all_nodes[parents[identity]]
            prefix = prefixes.get(identity)
            # Suppress only text naming the actual rendered parent. Other blockers,
            # canonical waits, warnings, reasons and clock tokens remain untouched.
            if prefix and prefix[0] == key(parent):
                detail = view.get('detail', '')
                start = detail.find(prefix[1])
                if start in (0, len('stalled: ')):
                    tail = detail[start + len(prefix[1]):]
                    if not tail or tail.startswith(' · '):
                        view['nested_detail'] = detail[:start] + (tail[3:] if tail.startswith(' · ') else tail)
    return groups
