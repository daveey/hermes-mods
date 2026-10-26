"""Narrow literal whole-turn vetoes; not a general authority classifier."""
# Match the complete latest user row, never a substring/quote/tool result. More
# complex restrictions stay with semantic scope assessment; absence is NOT
# permission, and partial action denials must not stop other authorized work.
_WHOLE_TURN = frozenset({
    'stop', 'cancel', 'do not continue', 'analysis only', 'status only',
    'plan first', 'do not change anything', 'do not execute anything',
})


def whole_turn(evidence):
    users = [(ref, row) for ref, row in evidence.items() if row.get('role') == 'user']
    if not users:
        return None
    ref, row = users[-1]
    text = row.get('content')
    if not isinstance(text, str):
        return None
    text = ' '.join(text.strip().lower().rstrip('.!').split())
    return ref if text in _WHOLE_TURN else None
