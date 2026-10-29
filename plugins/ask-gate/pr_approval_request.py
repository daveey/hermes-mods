"""Conservative PR-only request syntax; parsing is never approval or authorization.

Public, stdlib-only seam for filing gates and private reconcilers. Callers must
apply their own authority/owner policy and verify the current head and receipt.
Correction metadata is deliberately separate from strict action recognition.
"""
from __future__ import annotations

import json
import re

REPO = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
NUMBER = r"[1-9][0-9]*"
HEX = r"[0-9a-f]{7,40}"
REF = re.compile(r"https://github\.com/(" + REPO + r")/pull/(" + NUMBER
                 + r")(?![0-9A-Za-z/?#])|\b(" + REPO + r")#(" + NUMBER + r")(?![0-9A-Za-z])")
HEAD = re.compile(r"\b(?:head|sha|commit)\s*[:=@]?\s*`?(" + HEX + r")(?![0-9A-Za-z_])", re.I)
SHORT = re.compile(r"\bPR\s*#(" + NUMBER + r")\b", re.I)
LABELED = re.compile(r"\b(" + REPO + r")\s+PR\s*#(" + NUMBER + r")\b", re.I)
REPO_LABEL = re.compile(r"\bRepo(?:sitory)?\s*:?\s+(" + REPO + r")\b", re.I)
LIFECYCLE = re.compile(r"\bUnblock\s*=(.*?)[;.]\s*archive\s*=(.*)\Z", re.I | re.S)
# Full clauses, not verb prefixes: arbitrary objects/tails would reopen scope.
PR_OBJECT = r"(?:the\s+)?(?:PR|change)|it"
UNBLOCK_OUTCOME = re.compile(
    r"(?:queue(?:\s+(?:merges?|(?:the\s+)?merge\s+train))?"
    r"|merge\s+train"
    r"|(?:agent\s+)?merges?(?:\s+(?:" + PR_OBJECT + r"))?(?:\s+via\s+normal\s+gates)?"
    r"|(?:deploys?|release|ship)(?:\s+(?:" + PR_OBJECT + r"))?"
    r"|(?:I\s+)?(?:verify|check)(?:\s+CI)?"
    r"|(?:I\s+)?(?:re[- ]run|watch)\s+CI"
    r"|(?:continue|resume)(?:\s+the\s+task)?)", re.I)
ARCHIVE_OUTCOME = re.compile(
    r"(?:close(?:\s+(?:the\s+)?PR)?"
    r"|(?:drop|abandon)\s+the\s+change"
    r"|(?:findings\s+stay|(?:the\s+)?card\s+stays?)\s+open"
    r"|leave\s+(?:it|the\s+PR)\s+unmerged"
    r"|PR\s+left\s+open)", re.I)
OUTCOME_JOIN = re.compile(r",|\+|\b(?:and|then)\b", re.I)


def _outcome_failure(lifecycle):
    """Name the first unknown atom; never infer permission from an unknown verb."""
    for name, text, allowed in zip(("Unblock", "archive"), lifecycle.groups(),
                                    (UNBLOCK_OUTCOME, ARCHIVE_OUTCOME)):
        text = text.strip()
        # Only the final sentence terminator is punctuation, not arbitrary tails.
        if name == "archive" and text.endswith("."):
            text = text[:-1].rstrip()
        for clause in OUTCOME_JOIN.split(text):
            clause = clause.strip()
            if not allowed.fullmatch(clause):
                return f"Unrecognised {name} outcome clause: {clause!r}."
    return ""
REFERENCE = (r"(?:https://github\.com/" + REPO + r"/pull/" + NUMBER
             + r"|" + REPO + r"#" + NUMBER + r"|PR\s*#" + NUMBER + r")")
SURFACE = r"(?:Discord|Slack|(?:the\s+)?(?:BB\s+)?Inbox)"
SURFACES = SURFACE + r"(?:(?:\s*/\s*|,\s*(?:or\s+)?|\s+or\s+)" + SURFACE + r")*"
HEAD_CLAUSE = r"(?:head|sha|commit)\s+" + HEX
# Scope is metadata, not free-form worker instructions. Labels are short slugs,
# not prose; fullmatch also rejects second refs, heads, clauses and URL tails.
SCOPE_REFERENCE = (r"(?:https://github\.com/" + REPO + r"/pull/" + NUMBER
                   + r"|" + REPO + r"#" + NUMBER
                   + r"|" + REPO + r" +PR *#" + NUMBER
                   + r"|PR *#" + NUMBER + r"|(?:Repo(?:sitory)? *:? +)?" + REPO + r")")
SCOPE = re.compile(
    r" *" + SCOPE_REFERENCE + r"(?: +\((?P<label>[A-Za-z0-9][A-Za-z0-9._-]{0,63})\))?"
    + r"(?:,? +(?:head|sha|commit) +" + HEX + r")?(?: +only)? *", re.I | re.ASCII)


def _scope_failure(hr):
    scope = hr.get("scope")
    # Standalone parser callers may omit scope if the action resolves itself.
    if scope is None or (isinstance(scope, str) and not scope.strip(" ")):
        return ""
    if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
        return f"Unrecognised scope text: {scope!r}."
    return ""


ACTION = re.compile(
    r"\s*(?:please\s+)?(?:Approve|OK|Authorize)\s+" + REFERENCE
    + r"(?:\s+(?:at\s+)?" + HEAD_CLAUSE + r"|\s*\(" + HEAD_CLAUSE + r"\))?"
    + r"(?:\s+on GitHub)?"
    + r"(?:,?\s+or OK the bb approval ask in " + SURFACES + r")?\s*[.]?\s*", re.I)


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _request(row):
    hr = _get(row, "human_request")
    if isinstance(hr, str):
        try:
            hr = json.loads(hr)
        except ValueError:
            return None
    return hr if isinstance(hr, dict) else None


def _refs(text):
    return {(m[1] or m[3], int(m[2] or m[4])) for m in REF.finditer(text)}


def _context(row, hr):
    parts = [_get(row, "title"), _get(row, "body"), hr.get("scope")]
    if any(p is not None and not isinstance(p, str) for p in parts):
        return None
    return "\n".join(p or "" for p in parts)


def _resolve(action, context):
    """Resolve only explicit PR references or explicit Repo/Repository labels."""
    refs = _refs(action)
    numbers = {n for _, n in refs} | {int(n) for n in SHORT.findall(action)}
    if len(numbers) != 1:
        return None
    number = next(iter(numbers))
    repos = set(REPO_LABEL.findall(context))
    if not refs:
        refs = {(r, n) for r, n in _refs(context) if n == number}
        refs.update((m[1], int(m[2])) for m in LABELED.finditer(context) if int(m[2]) == number)
    if repos and (len(repos) != 1 or any(r not in repos for r, _ in refs)):
        return None
    if not refs and len(repos) == 1:
        refs = {(next(iter(repos)), number)}
    return next(iter(refs)) if len(refs) == 1 else None


def request_target(row):
    """Return (owner/repo, PR number, action head or None), else None.

    No owner restriction here. Context can resolve a short PR number but cannot
    supply an execution/approval head. Unknown/mixed authorizations are rejected.
    """
    hr = _request(row)
    if not hr or hr.get("interaction") not in ("approval", "reply", "external") or _scope_failure(hr):
        return None
    action = hr.get("action")
    if not isinstance(action, str):
        return None
    lifecycle = LIFECYCLE.search(action)
    if lifecycle:
        if (_outcome_failure(lifecycle)
                or len(re.findall(r'\bUnblock\s*=', action, re.I)) != 1
                or len(re.findall(r'\barchive\s*=', action, re.I)) != 1):
            return None
        action = action[:lifecycle.start()].rstrip(" .")
    if not ACTION.fullmatch(action):
        return None
    context = _context(row, hr)
    if context is None:
        return None
    target = _resolve(action, context)
    heads = set(HEAD.findall(action))
    if target is None or len(heads) > 1:
        return None
    return *target, next(iter(heads), None)


def scope_label(row):
    """Return a syntactically valid label verbatim, not authority or instructions."""
    scope = (_request(row) or {}).get("scope")
    match = SCOPE.fullmatch(scope) if isinstance(scope, str) else None
    return match.group("label") if match else None


def scope_label_failure(row, head_ref_name):
    """Bind optional label to the authoritative PR headRefName (case-sensitive).

    Call after fetching the PR, before trusting a receipt. Syntax-only filing
    cannot establish this invariant; a slug grammar alone is not sufficient.
    """
    failure = _scope_failure(_request(row) or {})
    if failure:
        return failure
    label = scope_label(row)
    if label is not None and label != head_ref_name:
        return f"Scope label {label!r} does not equal PR headRefName {head_ref_name!r}."
    return ""


def looks_like_pr_approval(row) -> bool:
    """Broad filing diagnostic, not permission or strict parse success."""
    hr = _request(row)
    action = hr.get("action") if hr else None
    return bool(isinstance(action, str)
                and re.search(r"\b(?:approve|ok|authorize)\b", action, re.I)
                and (SHORT.search(action) or REF.search(action)
                     or re.search(r"\b(?:pull request|bb approval ask)\b", action, re.I)))


def _metadata(row):
    """Unambiguous correction hints only; never feed this into a reconciler."""
    hr = _request(row) or {}
    action = hr.get("action")
    action = action if isinstance(action, str) else ""
    # Lifecycle outcomes are not the requested target.
    lifecycle = LIFECYCLE.search(action)
    if lifecycle:
        action = action[:lifecycle.start()]
    context = _context(row, hr)
    target = _resolve(action, context) if context is not None else None
    refs = _refs(action)
    numbers = {n for _, n in refs} | {int(n) for n in SHORT.findall(action)}
    number = next(iter(numbers)) if len(numbers) == 1 else None
    if not numbers and context is not None:
        context_refs = _refs(context) | {(m[1], int(m[2])) for m in LABELED.finditer(context)}
        context_numbers = {n for _, n in context_refs}
        if len(context_numbers) == 1:
            number = next(iter(context_numbers))
            target = _resolve(f"PR #{number}", context)
    heads = set(HEAD.findall(action + "\n" + (context or "")))
    return (target[0] if target else None, number,
            next(iter(heads)) if len(heads) == 1 else None)


def parse_failure_reason(row) -> str:
    """Empty on success; otherwise explain syntax and missing/ambiguous hints."""
    if request_target(row) is not None:
        return ""
    repo, number, head = _metadata(row)
    missing = [name for name, value in (("repository (OWNER/REPO)", repo),
               ("PR number (N)", number), ("head (SHA)", head)) if value is None]
    reason = "Not a supported PR-only approval request; use the canonical wording without additional authorization."
    hr = _request(row) or {}
    scope_failure = _scope_failure(hr)
    if scope_failure:
        reason += " " + scope_failure
    action = hr.get("action")
    lifecycle = LIFECYCLE.search(action) if isinstance(action, str) else None
    if lifecycle:
        failure = _outcome_failure(lifecycle)
        if failure:
            reason += " " + failure
    elif isinstance(action, str) and re.search(r"\b(?:Unblock|archive)\s*=", action, re.I):
        reason += " Malformed Unblock/archive section; use Unblock = <clauses>; archive = <clauses>."
    if missing:
        reason += " Missing or ambiguous fields: " + ", ".join(missing) + "."
    return reason


def canonical_wording(row) -> str:
    """Render supplied unambiguous metadata, leaving explicit missing placeholders.

    Ellipses for lifecycle outcomes require the caller's actual intended meanings;
    this helper never invents what accepting or declining should execute.
    """
    repo, number, head = _metadata(row)
    return (f"Approve https://github.com/{repo or 'OWNER/REPO'}/pull/{number or 'N'} "
            f"(head {head or 'SHA'}) on GitHub, or OK the bb approval ask in "
            "Discord, Slack or the BB Inbox. Unblock = …; archive = ….")
