"""Conservative recovery for explicit confirmation of a same-message plan.

No transcript lookup, model-invented scope, or approval is performed here.
"""
import re


_CONFIRM = re.compile(r"\b(?:say|reply|answer|click)\s+[\s*`\"']*(?:go|yes)\b|\b(?:shall|should) I (?:proceed|start)|\b(?:ready to proceed|confirm (?:the|this) plan)\b", re.I)


def preceding_plan(reply, span):
    """Return exact preceding prose only for an unambiguous structured plan.

    Bare promises, fenced examples, multiple asks, and missing/ambiguous spans
    remain incomplete. Refuse oversize input rather than dropping limits.
    """
    if not isinstance(reply, str) or len(reply) > 65536 or not span or reply.count(span) != 1:
        return None
    if not _CONFIRM.search(span):
        return None
    start = reply.index(span)
    end = start + len(span)
    prefix = reply[:start].strip()
    suffix = reply[end:].strip()
    # Every surrounding ask makes recovery ambiguous, on either side. Never
    # fold a second approval into the selected request's execution authority.
    surrounding = prefix + '\n' + suffix
    if _CONFIRM.search(surrounding) or '::user-step{' in surrounding or '?' in surrounding:
        return None
    # Markdown fences close only with the same character and at least the
    # opening length. Interior shorter fences are data, not delimiters.
    fence = None
    visible_lines = []
    offset = 0
    for line in reply.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*?)[\r\n]*$', line)
        was_fenced = fence is not None
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        inert = was_fenced or marker is not None
        if inert and offset < end and offset + len(line) > start:
            return None
        if offset < start and not inert:
            visible_lines.append(line[:max(0, start - offset)])
        offset += len(line)
    if fence:
        return None
    visible = ''.join(visible_lines)
    # Two concrete plan steps, or a named plan with substantive prose. All
    # original text (including limits, exclusions and code) is frozen below.
    steps = re.findall(r'^\s*(?:\d+[.)]|[-*])\s+(.+)', visible, re.M)
    structured = len([s for s in steps if len(s.split()) >= 4]) >= 2
    prose = bool(re.search(r'^\s*#{1,6}\s+[^\n]*\bplan\b', visible, re.I | re.M)) and len(visible.split()) >= 25
    if not (structured or prose):
        return None
    suffix = reply[reply.index(span) + len(span):].strip()
    return prefix + ('\n\n' + suffix if suffix else '')
