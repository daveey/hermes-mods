"""Fail-closed recovery of one final, top-level confirmation after a plan.

This is deliberately a small accepted grammar, not a Markdown interpreter.
Anything outside it must use an explicit action_request with a complete scope.
"""
import re


_CONFIRM = re.compile(r"\b(?:say|reply|answer|click)\s+[\s*`\"']*(?:go|yes)\b|\b(?:shall|should) I (?:proceed|start)|\b(?:ready to proceed|confirm (?:the|this) plan)\b", re.I)


def preceding_plan(reply, span):
    """Freeze the exact plan only for an unambiguous final prose block.

    Reject ambiguous Markdown altogether instead of trying to strip examples
    and accidentally manufacturing authority from their remaining text.
    """
    if not isinstance(reply, str) or len(reply) > 65536 or not span or reply.count(span) != 1:
        return None
    # Count across the whole raw message, including examples and the selected
    # span itself. A model-selected span is evidence, never parser authority.
    matches = list(_CONFIRM.finditer(reply))
    if len(matches) != 1:
        return None
    blocks = re.split(r'\n[ \t]*\n', reply.rstrip())
    if len(blocks) < 2 or blocks[-1] != span or '\n' in span or '\r' in span:
        return None
    # The directive must begin the final block; inline-code, list, quote and
    # example prefixes are not confirmations. Do not trim indentation here.
    if not _CONFIRM.match(span):
        return None
    # Unsupported Markdown anywhere stays on the existing incomplete path.
    # This also rejects unclosed containers and lazy blockquote/list continuations
    # without extending a home-grown Markdown parser at an authorization boundary.
    if (any(char in reply for char in ('`', '~', '<', '>'))
            or re.search(r'^(?:[ \t]+\S|\s*(?:[-+*]|\d+[.)])\s+.*\b(?:say|reply|answer|click)\b)', reply, re.I | re.M)
            or '::user-step{' in reply or '?' in reply[:reply.index(span)]
            or re.search(r'\b(?:example|sample|quotation)(?:\s+only)?\s*:', reply, re.I)):
        return None
    prefix = reply[:reply.index(span)].rstrip()
    adjacent = blocks[-2]
    # The immediately preceding block must itself be substantive plan prose,
    # not a dangling label after a plan several blocks earlier.
    steps = re.findall(r'^(?:\d+[.)]|[-*])\s+(.+)', adjacent, re.M)
    structured = len([s for s in steps if len(s.split()) >= 4]) >= 2
    prose = (bool(re.search(r'^#{1,6}\s+[^\n]*\bplan\b', prefix, re.I | re.M))
             and len(adjacent.split()) >= 25)
    if not (structured or prose):
        return None
    return prefix
