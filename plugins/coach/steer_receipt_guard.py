"""Refuse a hand-written steer-ACK poll: a count/exists over task_comments matching an ACK token.

A mid-run steer asks the worker to reply with a token, so the steer comment itself contains that token.
`count(*) ... body LIKE '%STEER-ACK-<tag>%'` therefore counts the request as its own acknowledgement
(self-heal agent-6ac431aed9: ack=1 for a worker that died at startup and never received the steer).
`id > <steer>` does not fix it either: a later quote by the supervisor or an older run's reply still
matches, and nothing checks delivery to the expected run.

This is an accidental-query guard keyed on the demonstrated shape, not universal SQL analysis. It
refuses only a presence/aggregate probe (count/exists/total/sum, `select 1`) over task_comments whose
body filter (LIKE/GLOB/instr) names an ACK token. Row reads (`select id, author, body ...`) stay allowed
for diagnosis. Covered entry paths: terminal command text (including ssh/bash -lc wrapping, whose
escaping is normalized) and execute_code source text.
"""
import re

MESSAGE = (
    "A count/exists poll over task_comments for a steer ACK token is not an acknowledgement: your own "
    "steer contains the token, so it matches itself, and nothing checks delivery to the worker's run. "
    "Use the read-only verifier instead: python3 ~/.hermes/skills/devops/kanban-orchestrator/scripts/"
    "steer_receipt.py --db ~/.hermes/kanban.db --task <id> --request-comment <steer comment id> "
    "--run <worker run id> --token <token>. It reports posted / delivered / acknowledged separately and "
    "needs a comment_delivered event for that comment and run, then a later reply whose commented event "
    "has writer_run_id = that run (author/profile name is not identity). Row reads "
    "(select id, author, body ...) stay allowed for diagnosis."
)

_TABLE = re.compile(r"\btask_comments\b")
_PRESENCE = re.compile(r"\b(?:count|exists|total|sum)\s*\(|\bselect\s+(?:distinct\s+)?1\b")
_ACK_STRING = r"""(?:'[^']*\back\b[^']*'|"[^"]*\back\b[^"]*")"""
_ACK_FILTER = re.compile(
    r"\b(?:like|glob)\s+" + _ACK_STRING + r"|\binstr\s*\([^,()]*,\s*" + _ACK_STRING)


def _normalize(text):
    # ssh "sqlite3 db \"... like '%X%'\"" and bash -lc '... \'%X%\'' leave backslash escapes in the
    # tool text; drop them so quoting depth does not hide the query.
    return text.replace("\\", "").lower()


def unsafe_text(text):
    if not isinstance(text, str) or "task_comments" not in text:
        return False
    for chunk in _normalize(text).split(";"):
        if _TABLE.search(chunk) and _PRESENCE.search(chunk) and _ACK_FILTER.search(chunk):
            return True
    return False


def blocked(tool_name, args):
    if not isinstance(args, dict):
        return False
    if tool_name == "terminal":
        return unsafe_text(args.get("command"))
    if tool_name == "execute_code":
        return unsafe_text(args.get("code"))
    return False
