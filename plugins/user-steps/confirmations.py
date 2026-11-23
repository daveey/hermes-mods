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
    prefix = reply[:reply.index(span)].strip()
    if (prefix.count('```') % 2 or prefix.count('~~~') % 2
            or '::user-step{' in prefix or '?' in prefix):
        return None
    visible = re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~', '', prefix)
    # Two concrete plan steps, or a named plan with substantive prose. All
    # original text (including limits, exclusions and code) is frozen below.
    steps = re.findall(r'^\s*(?:\d+[.)]|[-*])\s+(.+)', visible, re.M)
    structured = len([s for s in steps if len(s.split()) >= 4]) >= 2
    prose = bool(re.search(r'^\s*#{1,6}\s+[^\n]*\bplan\b', visible, re.I | re.M)) and len(visible.split()) >= 25
    if not (structured or prose):
        return None
    suffix = reply[reply.index(span) + len(span):].strip()
    return prefix + ('\n\n' + suffix if suffix else '')
